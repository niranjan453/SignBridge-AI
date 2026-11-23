import asyncio
import base64
import binascii
from collections import deque
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.auth import user_from_token
from app.pipeline import run_translation
from app.schemas import Language, Mode
from app.vision import MAX_FRAME_BYTES, CameraFrame, PreparedFrame

router = APIRouter()
MAX_MESSAGE_CHARS = 4 * ((MAX_FRAME_BYTES + 2) // 3) + 512


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["start"]
    access_token: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    language: Language | None = None


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["frame"]
    session_id: UUID
    sequence: Annotated[int, Field(ge=0, le=2**53 - 1)]
    timestamp_ms: Annotated[int, Field(ge=0, le=2**53 - 1)]
    content_type: Literal["image/jpeg", "image/png"]
    data: str

    def camera_frame(self) -> CameraFrame:
        try:
            data = base64.b64decode(self.data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid base64") from None
        if len(data) > MAX_FRAME_BYTES:
            raise OverflowError
        if self.content_type == "image/jpeg":
            valid = data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
        else:
            valid = data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR") and len(data) >= 33
        if not valid:
            raise ValueError("Frame signature does not match its content type")
        return CameraFrame(data, self.content_type, self.sequence, self.timestamp_ms)


class ProtocolError(Exception):
    def __init__(self, code: str, message: str, close_code: int | None = None) -> None:
        self.code = code
        self.message = message
        self.close_code = close_code


async def receive_message(socket: WebSocket, idle_seconds: float) -> str:
    try:
        async with asyncio.timeout(idle_seconds):
            message = await socket.receive()
    except TimeoutError:
        raise ProtocolError("idle_timeout", "Session expired due to inactivity", 1008) from None
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message["code"])
    text = message.get("text")
    if text is None:
        raise ProtocolError("invalid_message", "Expected a JSON text message", 1003)
    if len(text) > MAX_MESSAGE_CHARS:
        raise ProtocolError("frame_too_large", "Frame message exceeds the size limit", 1009)
    return text


async def send_error(socket: WebSocket, error: ProtocolError) -> None:
    await socket.send_json({"type": "error", "error": {"code": error.code, "message": error.message}})
    if error.close_code:
        await socket.close(code=error.close_code)


def session_language(socket: WebSocket, token: str) -> str:
    with socket.app.state.sessions() as db:
        return user_from_token(socket, db, token).settings.preferred_language


async def process_frames(socket: WebSocket, session_id: UUID, language: str) -> None:
    settings = socket.app.state.settings
    window: deque[PreparedFrame] = deque(maxlen=settings.cv_ws_window)
    clock_offset_ms = float("inf")
    last_sequence = -1
    last_timestamp = -1
    next_frame = 0.0
    consecutive_rate_errors = 0
    while True:
        try:
            message = await receive_message(socket, settings.ws_idle_seconds)
            now = monotonic()
            if now < next_frame:
                consecutive_rate_errors += 1
                raise ProtocolError("rate_limit", "Frame rate limit exceeded",
                                    1008 if consecutive_rate_errors >= settings.ws_frames_per_second else None)
            next_frame = now + 1 / settings.ws_frames_per_second
            consecutive_rate_errors = 0
            try:
                frame = Frame.model_validate_json(message)
            except ValidationError:
                raise ProtocolError("invalid_frame", "Invalid frame message") from None
            if frame.session_id != session_id:
                raise ProtocolError("invalid_session", "Session ID does not match this connection")
            if frame.sequence <= last_sequence or frame.timestamp_ms < last_timestamp:
                raise ProtocolError("invalid_sequence", "Frame sequence must increase and timestamps must be ordered")
            try:
                camera_frame = frame.camera_frame()
            except OverflowError:
                raise ProtocolError("frame_too_large", "Frame exceeds the size limit", 1009) from None
            except ValueError:
                raise ProtocolError("invalid_frame", "Invalid frame encoding or format") from None
        except ProtocolError as exc:
            await send_error(socket, exc)
            if exc.close_code:
                return
            continue

        last_sequence, last_timestamp = frame.sequence, frame.timestamp_ms
        clock_offset_ms = min(clock_offset_ms, now * 1000 - frame.timestamp_ms)
        if now * 1000 - frame.timestamp_ms - clock_offset_ms > settings.cv_ws_max_lag_ms:
            await send_error(socket, ProtocolError("stale_frame", "Frame arrived too late to process"))
            continue
        result, _ = await run_translation(
            socket.app.state.translation_service, Mode.SIGN_TO_TEXT, language,
            settings.processing_timeout_seconds, media=camera_frame, window=window,
        )
        await socket.send_json({
            "type": "result", "session_id": str(session_id), "sequence": frame.sequence,
            "timestamp_ms": frame.timestamp_ms, "translation": result.model_dump(mode="json"),
        })


@router.websocket("/ws/sign-recognition")
async def sign_recognition(socket: WebSocket) -> None:
    settings = socket.app.state.settings
    origin = socket.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        await socket.close(code=1008)
        return
    active = socket.app.state.sign_sessions
    if len(active) >= settings.ws_max_sessions:
        await socket.close(code=1013)
        return

    session_id = uuid4()
    active.add(session_id)
    try:
        await socket.accept()
        try:
            message = await receive_message(socket, settings.ws_idle_seconds)
            start = Start.model_validate_json(message)
        except ValidationError:
            raise ProtocolError("invalid_start", "Expected a valid start message", 1008) from None
        except ProtocolError as exc:
            raise ProtocolError(exc.code, exc.message, exc.close_code or 1008) from None

        preference = "en-IN"
        if start.access_token is not None:
            try:
                preference = await run_in_threadpool(session_language, socket, start.access_token)
            except HTTPException as exc:
                if exc.status_code == 503:
                    raise ProtocolError("auth_unavailable", "Account authentication unavailable", 1013) from None
                raise ProtocolError("invalid_credentials", "Invalid or expired credentials", 1008) from None
            except SQLAlchemyError:
                raise ProtocolError("auth_unavailable", "Account authentication unavailable", 1013) from None
        await socket.send_json({
            "type": "session", "session_id": str(session_id), "language": start.language or preference,
            "authenticated": start.access_token is not None, "max_frame_bytes": MAX_FRAME_BYTES,
            "frames_per_second": settings.ws_frames_per_second,
        })
        await process_frames(socket, session_id, start.language or preference)
    except ProtocolError as exc:
        await send_error(socket, exc)
    except (WebSocketDisconnect, OSError):
        pass
    finally:
        active.discard(session_id)
