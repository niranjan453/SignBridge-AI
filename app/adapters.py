import asyncio
import base64
import io
import logging
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Protocol

from app.ai_contracts import (
    LLMRequest, LLMResult, Recognition, RetrievalHit, RetrievalRequest, RetrievalResult, Source,
)
from app.schemas import Mode, VisionMetadata
from app.vision import CameraFrame, InvalidVisionInput, PreparedFrame, SignInput, VisionProcessor

log = logging.getLogger("uvicorn.error")


class ProcessingError(Exception):
    status = "failed"
    code = "processing_failed"
    message = "Translation processing failed"
    http_status = 502


class ServiceUnavailable(ProcessingError):
    code = "service_unavailable"
    message = "Translation service unavailable"
    http_status = 503


class ProcessingTimeout(ProcessingError):
    status = "timed_out"
    code = "processing_timeout"
    message = "Translation processing timed out"
    http_status = 504


class InvalidInput(ProcessingError):
    code = "invalid_input"
    message = "Invalid translation input"
    http_status = 422


@dataclass(frozen=True)
class AdapterResult:
    output_text: str
    audio_base64: str | None = None
    audio_content_type: str | None = None
    sign_tokens: list[str] | None = None
    recognized_text: str | None = None
    confidence: float | None = None
    vision: VisionMetadata | None = None


class SignRecognizer(Protocol):
    async def recognize_sign(self, media: SignInput, language: str) -> Recognition: ...


class SpeechRecognizer(Protocol):
    async def recognize_speech(self, media: Path, language: str) -> Recognition: ...


class SpeechSynthesizer(Protocol):
    async def synthesize(self, text: str, language: str) -> tuple[bytes, str]: ...


class LanguageProcessor(Protocol):
    async def to_sign(self, text: str, language: str) -> list[str]: ...


class LLM(Protocol):
    async def process(self, request: LLMRequest) -> LLMResult: ...


class ContextRetriever(Protocol):
    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


@dataclass(frozen=True)
class TranslationService:
    sign: SignRecognizer
    speech: SpeechRecognizer
    tts: SpeechSynthesizer
    nlp: LanguageProcessor
    rag: ContextRetriever
    llm: LLM
    model_version: str
    is_mock: bool
    model_name: str = "signbridge-mock"
    context_timeout_seconds: float = 2
    vision: VisionProcessor = field(default_factory=VisionProcessor)
    _vision_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2), repr=False)

    async def translate(
        self, mode: Mode, language: str, text: str | None, media: Path | CameraFrame | None,
        *, timeout: float = 10, window: deque[PreparedFrame] | None = None,
    ) -> AdapterResult:
        try:
            async with asyncio.timeout(timeout):
                return await self._translate(mode, language, text, media, window, perf_counter() + timeout)
        except InvalidVisionInput as exc:
            raise InvalidInput() from exc
        except ProcessingError:
            raise
        except TimeoutError:
            raise ProcessingTimeout() from None
        except Exception as exc:
            log.warning("signbridge_adapter_failure error_type=%s", type(exc).__name__)
            raise ProcessingError() from None

    async def _prepare_sign(
        self, media: Path | CameraFrame, window: deque[PreparedFrame] | None, deadline: float,
    ) -> SignInput:
        async with self._vision_slots:
            if perf_counter() >= deadline:
                raise TimeoutError
            if isinstance(media, CameraFrame):
                worker = asyncio.create_task(asyncio.to_thread(self.vision.image, media))
            else:
                worker = asyncio.create_task(asyncio.to_thread(self.vision.video, media, deadline))
            try:
                prepared = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Let the native decoder release the uploaded file before its request removes it.
                await asyncio.gather(worker, return_exceptions=True)
                raise
        if isinstance(prepared, SignInput):
            return prepared
        if window is None:
            return SignInput((prepared,))
        window.append(prepared)
        return SignInput(tuple(window))

    async def _translate(
        self, mode: Mode, language: str, text: str | None, media: Path | CameraFrame | None,
        window: deque[PreparedFrame] | None, deadline: float,
    ) -> AdapterResult:
        recognition = None
        vision_metadata = None
        if mode in (Mode.SIGN_TO_TEXT, Mode.SIGN_TO_SPEECH):
            if media is None or text is not None:
                raise InvalidInput()
            sign_input = await self._prepare_sign(media, window, deadline)
            recognition = Recognition.model_validate(await self.sign.recognize_sign(sign_input, language))
            current = sign_input.latest
            samples = (current,) if sign_input.video is None else sign_input.frames
            vision_metadata = VisionMetadata(
                width=current.width, height=current.height, sample_count=len(sign_input.frames),
                preprocessing_ms=round(sum(sample.preprocessing_ms for sample in samples), 3),
                landmark_ms=round(sum(sample.landmark_ms for sample in samples), 3),
                landmark_status=current.landmarks.status, hands_detected=len(current.landmarks.hands),
                fps=sign_input.video.fps if sign_input.video else None,
                frame_count=sign_input.video.frame_count if sign_input.video else None,
                duration_seconds=sign_input.video.duration_seconds if sign_input.video else None,
            )
            try:
                async with asyncio.timeout(self.context_timeout_seconds):
                    context = await self.rag.retrieve(RetrievalRequest(query=recognition.text, language=language))
                async with asyncio.timeout(self.context_timeout_seconds):
                    result = await self.llm.process(LLMRequest(
                        operation="contextualize", language=language, text=recognition.text, context=context.hits,
                    ))
            except (TimeoutError, ProcessingError) as exc:
                log.warning("signbridge_context_fallback error_type=%s", type(exc).__name__)
                text = recognition.text
            else:
                text = result.text
        elif mode == Mode.SPEECH_TO_TEXT:
            if not isinstance(media, Path) or text is not None:
                raise InvalidInput()
            recognition = Recognition.model_validate(await self.speech.recognize_speech(media, language))
            text = recognition.text
        elif media is not None or text is None or not text.strip() or len(text) > 2000:
            raise InvalidInput()

        metadata = {
            "recognized_text": recognition.text if recognition else None,
            "confidence": recognition.confidence if recognition else None,
            "vision": vision_metadata,
        }
        if mode in (Mode.SIGN_TO_SPEECH, Mode.TEXT_TO_SPEECH):
            audio, content_type = await self.tts.synthesize(text, language)
            return AdapterResult(text, base64.b64encode(audio).decode("ascii"), content_type, **metadata)
        if mode == Mode.TEXT_TO_SIGN:
            return AdapterResult(text, sign_tokens=await self.nlp.to_sign(text, language))
        return AdapterResult(text, **metadata)


class MockAdapters:
    async def recognize_sign(self, media: SignInput, language: str) -> Recognition:
        return Recognition(text="Hello from the mock sign recognizer.", confidence=0.95)

    async def recognize_speech(self, media: Path, language: str) -> Recognition:
        return Recognition(text="Hello from the mock speech recognizer.")

    async def synthesize(self, text: str, language: str) -> tuple[bytes, str]:
        if text == "[mock-error]":
            raise ProcessingError()
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setparams((1, 1, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x80" * 800)
        return output.getvalue(), "audio/wav"

    async def to_sign(self, text: str, language: str) -> list[str]:
        if text == "[mock-error]":
            raise ProcessingError()
        return text.upper().split()

    async def process(self, request: LLMRequest) -> LLMResult:
        if request.operation == "contextualize":
            return LLMResult(text=" ".join(request.text.split()))
        words = request.text if request.operation == "correct_grammar" else " ".join(request.signs).lower()
        text = " ".join(words.split())
        text = text[:1].upper() + text[1:]
        if not text.endswith((".", "!", "?")):
            text = text[:1999].rstrip() + "."
        return LLMResult(text=text[:2000])

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if "hello" not in request.query.casefold().split():
            return RetrievalResult()
        return RetrievalResult(hits=[RetrievalHit(
            text="Hello is a greeting.", source=Source(document_id="greeting", title="Mock vocabulary"),
            relevance=1.0,
        )])


def mock_service(
    version: str, name: str = "signbridge-mock", context_timeout: float = 2,
    vision: VisionProcessor | None = None,
) -> TranslationService:
    adapters = MockAdapters()
    return TranslationService(
        adapters, adapters, adapters, adapters, adapters, adapters, version, True, name, context_timeout,
        vision or VisionProcessor(),
    )
