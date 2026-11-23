from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from app.adapters import ProcessingError, TranslationService
from app.schemas import Mode, TranslationOut
from app.vision import CameraFrame, PreparedFrame


async def run_translation(
    service: TranslationService, mode: Mode, language: str, timeout: float,
    text: str | None = None, media: Path | CameraFrame | None = None,
    window: deque[PreparedFrame] | None = None,
) -> tuple[TranslationOut, ProcessingError | None]:
    start = perf_counter()
    if isinstance(media, CameraFrame):
        input_type = "frame"
    elif media is None:
        input_type = "text"
    else:
        input_type = "audio" if mode == Mode.SPEECH_TO_TEXT else "video"

    error = None
    data = {}
    try:
        output = await service.translate(mode, language, text, media, timeout=timeout, window=window)
        data = asdict(output)
    except ProcessingError as exc:
        error = exc
        data = {"error": {"code": exc.code, "message": exc.message}}

    result = TranslationOut(
        id=uuid4(), mode=mode, status=error.status if error else "completed",
        model_version=service.model_version, model_name=service.model_name, is_mock=service.is_mock,
        input_type=input_type, processing_time_ms=round((perf_counter() - start) * 1000, 3),
        timestamp=datetime.now(timezone.utc), **data,
    )
    return result, error
