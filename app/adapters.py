import base64
import io
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.schemas import Mode


class ProcessingError(Exception):
    pass


@dataclass(frozen=True)
class AdapterResult:
    output_text: str
    audio_base64: str | None = None
    audio_content_type: str | None = None
    sign_tokens: list[str] | None = None


class SignRecognizer(Protocol):
    async def recognize_sign(self, media: Path, language: str) -> str: ...


class SpeechRecognizer(Protocol):
    async def recognize_speech(self, media: Path, language: str) -> str: ...


class SpeechSynthesizer(Protocol):
    async def synthesize(self, text: str, language: str) -> tuple[bytes, str]: ...


class LanguageProcessor(Protocol):
    async def contextualize(self, text: str, language: str, context: list[str]) -> str: ...
    async def to_sign(self, text: str, language: str) -> list[str]: ...


class ContextRetriever(Protocol):
    async def retrieve(self, text: str, language: str) -> list[str]: ...


@dataclass(frozen=True)
class TranslationService:
    sign: SignRecognizer
    speech: SpeechRecognizer
    tts: SpeechSynthesizer
    nlp: LanguageProcessor
    rag: ContextRetriever
    model_version: str
    is_mock: bool

    async def translate(self, mode: Mode, language: str, text: str | None, media: Path | None) -> AdapterResult:
        if mode in (Mode.SIGN_TO_TEXT, Mode.SIGN_TO_SPEECH):
            assert media is not None
            text = await self.sign.recognize_sign(media, language)
        elif mode == Mode.SPEECH_TO_TEXT:
            assert media is not None
            text = await self.speech.recognize_speech(media, language)

        assert text is not None
        if media is not None:
            text = await self.nlp.contextualize(text, language, await self.rag.retrieve(text, language))
        if mode in (Mode.SIGN_TO_SPEECH, Mode.TEXT_TO_SPEECH):
            audio, content_type = await self.tts.synthesize(text, language)
            return AdapterResult(text, base64.b64encode(audio).decode("ascii"), content_type)
        if mode == Mode.TEXT_TO_SIGN:
            return AdapterResult(text, sign_tokens=await self.nlp.to_sign(text, language))
        return AdapterResult(text)


class MockAdapters:
    async def recognize_sign(self, media: Path, language: str) -> str:
        return "Hello from the mock sign recognizer."

    async def recognize_speech(self, media: Path, language: str) -> str:
        return "Hello from the mock speech recognizer."

    async def synthesize(self, text: str, language: str) -> tuple[bytes, str]:
        if text == "[mock-error]":
            raise ProcessingError()
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setparams((1, 1, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x80" * 800)
        return output.getvalue(), "audio/wav"

    async def contextualize(self, text: str, language: str, context: list[str]) -> str:
        return text

    async def to_sign(self, text: str, language: str) -> list[str]:
        if text == "[mock-error]":
            raise ProcessingError()
        return text.upper().split()

    async def retrieve(self, text: str, language: str) -> list[str]:
        return []


def mock_service(version: str) -> TranslationService:
    adapters = MockAdapters()
    return TranslationService(adapters, adapters, adapters, adapters, adapters, version, True)
