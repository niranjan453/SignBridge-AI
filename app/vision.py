from dataclasses import dataclass, field
from io import BytesIO
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Literal

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image, UnidentifiedImageError

from app.landmarks import HandExtractor, LandmarkResult, NoLandmarks


MAX_FRAME_BYTES = 64 * 1024


class InvalidVisionInput(Exception):
    pass


@dataclass(frozen=True)
class CameraFrame:
    data: bytes
    content_type: str
    sequence: int
    timestamp_ms: int


@dataclass(frozen=True)
class ModelInput:
    max_side: int = 224
    color: Literal["rgb", "bgr"] = "rgb"
    normalize: bool = True
    rotation: Literal[0, 90, 180, 270] = 0


@dataclass(frozen=True)
class PreparedFrame:
    pixels: NDArray
    sequence: int
    timestamp_ms: int
    source_width: int
    source_height: int
    model_input: ModelInput
    landmarks: LandmarkResult
    preprocessing_ms: float
    landmark_ms: float

    @property
    def width(self) -> int:
        return self.pixels.shape[1]

    @property
    def height(self) -> int:
        return self.pixels.shape[0]


@dataclass(frozen=True)
class VideoMetadata:
    fps: float
    frame_count: int
    duration_seconds: float


@dataclass(frozen=True)
class SignInput:
    frames: tuple[PreparedFrame, ...]
    video: VideoMetadata | None = None

    @property
    def latest(self) -> PreparedFrame:
        return self.frames[-1]


@dataclass(frozen=True)
class VisionProcessor:
    model_input: ModelInput = field(default_factory=ModelInput)
    landmarks: HandExtractor = field(default_factory=NoLandmarks)
    max_pixels: int = 2_073_600
    max_video_frames: int = 240
    max_video_seconds: float = 15
    max_video_samples: int = 12
    sample_fps: float = 4
    max_video_bytes: int = 10 * 1024 * 1024

    def validate_dimensions(self, width: int, height: int) -> None:
        if min(width, height) < 16 or max(width, height) > 4096 or width * height > self.max_pixels:
            raise InvalidVisionInput("Image or video dimensions exceed processing limits")

    def prepare(
        self, bgr: NDArray, sequence: int, timestamp_ms: int, started: float,
    ) -> PreparedFrame:
        height, width = bgr.shape[:2]
        self.validate_dimensions(width, height)
        source_width, source_height = width, height
        rotation = self.model_input.rotation
        if rotation:
            bgr = cv2.rotate(bgr, {
                90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE,
            }[rotation])
        height, width = bgr.shape[:2]
        scale = min(1, self.model_input.max_side / max(height, width))
        if scale < 1:
            bgr = cv2.resize(bgr, (max(1, round(width * scale)), max(1, round(height * scale))),
                             interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pixels = rgb if self.model_input.color == "rgb" else bgr
        if self.model_input.normalize:
            pixels = pixels.astype(np.float32)
            pixels *= 1 / 255
        pixels.setflags(write=False)
        before_hands = perf_counter()
        hands = self.landmarks.extract(rgb)
        return PreparedFrame(
            pixels, sequence, timestamp_ms, source_width, source_height,
            self.model_input, hands, (before_hands - started) * 1000, (perf_counter() - before_hands) * 1000,
        )

    def image(self, frame: CameraFrame) -> PreparedFrame:
        started = perf_counter()
        if not 0 < len(frame.data) <= MAX_FRAME_BYTES:
            raise InvalidVisionInput("Camera frame exceeds the size limit or is empty")
        expected = {"image/jpeg": "JPEG", "image/png": "PNG"}.get(frame.content_type)
        if expected is None:
            raise InvalidVisionInput("Unsupported image format")
        if expected == "JPEG" and not frame.data.endswith(b"\xff\xd9"):
            raise InvalidVisionInput("Corrupted camera frame")
        try:
            with Image.open(BytesIO(frame.data), formats=[expected]) as header:
                self.validate_dimensions(*header.size)
                header.verify()
            image = cv2.imdecode(np.frombuffer(frame.data, dtype=np.uint8),
                                 cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        except (UnidentifiedImageError, Image.DecompressionBombError, SyntaxError, OSError, ValueError, cv2.error):
            raise InvalidVisionInput("Corrupted camera frame") from None
        if image is None:
            raise InvalidVisionInput("Corrupted camera frame")
        return self.prepare(image, frame.sequence, frame.timestamp_ms, started)

    def video(self, path: Path, deadline: float) -> SignInput:
        if path.suffix not in {".mp4", ".webm"} or not 0 < path.stat().st_size <= self.max_video_bytes:
            raise InvalidVisionInput("Unsupported video format or size")
        try:
            return self._read_video(path, deadline)
        except cv2.error:
            raise InvalidVisionInput("Invalid video or unsupported codec") from None

    def _read_video(self, path: Path, deadline: float) -> SignInput:
        if perf_counter() >= deadline:
            raise TimeoutError
        capture = cv2.VideoCapture()
        try:
            opened = capture.open(str(path), cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 1500,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1500,
                cv2.CAP_PROP_N_THREADS, 1,
            ])
            if not opened:
                raise InvalidVisionInput("Invalid video or unsupported codec")
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
            fps = capture.get(cv2.CAP_PROP_FPS)
            count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            width = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
            height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
            if not all(isfinite(value) for value in (fps, count, width, height)) or not 0 < fps <= 120:
                raise InvalidVisionInput("Invalid video metadata")
            self.validate_dimensions(int(width), int(height))
            if count > self.max_video_frames or count / fps > self.max_video_seconds:
                raise InvalidVisionInput("Video duration or frame count exceeds processing limits")

            frames = []
            next_sample = 0.0
            step = max(1 / self.sample_fps, 1 / fps)
            frame_count = 0
            while True:
                started = perf_counter()
                if started >= deadline:
                    raise TimeoutError
                ok, frame = capture.read()
                if not ok:
                    break
                self.validate_dimensions(frame.shape[1], frame.shape[0])
                seconds = frame_count / fps
                if frame_count >= self.max_video_frames or seconds >= self.max_video_seconds:
                    raise InvalidVisionInput("Video duration or frame count exceeds processing limits")
                if len(frames) < self.max_video_samples and seconds + 1e-9 >= next_sample:
                    frames.append(self.prepare(frame, frame_count, round(seconds * 1000), started))
                    next_sample += step
                frame_count += 1
            if not frames or (path.suffix == ".mp4" and count > frame_count + 1):
                raise InvalidVisionInput("Video is empty, corrupted, or uses an unsupported codec")
            return SignInput(tuple(frames), VideoMetadata(fps, frame_count, frame_count / fps))
        finally:
            capture.release()
