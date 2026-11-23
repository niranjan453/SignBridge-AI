from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import anyio
from fastapi import HTTPException, Request

AUDIO_FORMATS = {"audio/wav": ".wav", "audio/mpeg": ".mp3", "audio/ogg": ".ogg"}
VIDEO_FORMATS = {"video/mp4": ".mp4", "video/webm": ".webm"}


def matches_format(content_type: str, header: bytes) -> bool:
    if content_type == "audio/wav":
        return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if content_type == "audio/mpeg":
        return header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 255 and header[1] & 224 == 224)
    if content_type == "audio/ogg":
        return header.startswith(b"OggS")
    if content_type == "video/mp4":
        return header[4:8] == b"ftyp"
    return header.startswith(b"\x1a\x45\xdf\xa3")


@asynccontextmanager
async def temporary_media(request: Request, formats: dict[str, str]) -> AsyncIterator[Path]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in formats:
        raise HTTPException(415, "Unsupported media type")
    limit = request.app.state.settings.media_max_bytes
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdigit() or len(length) > 20:
            raise HTTPException(400, "Invalid Content-Length")
        if int(length) > limit:
            raise HTTPException(413, "Media exceeds the upload size limit")

    with TemporaryDirectory(prefix="signbridge-") as directory:
        path = Path(directory) / f"{uuid4().hex}{formats[content_type]}"
        header = bytearray()
        size = 0
        async with await anyio.open_file(path, "xb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "Media exceeds the upload size limit")
                header.extend(chunk[: max(0, 16 - len(header))])
                await output.write(chunk)
        if size == 0:
            raise HTTPException(422, "Media body is empty")
        if not matches_format(content_type, header):
            raise HTTPException(415, "Media signature does not match the declared type")
        yield path
