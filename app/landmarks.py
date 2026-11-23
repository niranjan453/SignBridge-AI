import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol

from numpy.typing import NDArray
from pydantic import BaseModel, Field

log = logging.getLogger("uvicorn.error")


class Landmark(BaseModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    z: float = Field(allow_inf_nan=False)


class Hand(BaseModel):
    points: tuple[Landmark, ...] = Field(min_length=21, max_length=21)
    handedness: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)


@dataclass(frozen=True)
class LandmarkResult:
    status: Literal["disabled", "unavailable", "ok"]
    hands: tuple[Hand, ...] = ()


class HandExtractor(Protocol):
    def extract(self, rgb: NDArray) -> LandmarkResult: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class NoLandmarks:
    status: Literal["disabled", "unavailable"] = "disabled"

    def extract(self, rgb: NDArray) -> LandmarkResult:
        return LandmarkResult(self.status)

    def close(self) -> None:
        pass


class MediaPipeHands:
    def __init__(self, module, detector) -> None:
        self._module = module
        self._detector = detector
        self._lock = Lock()

    @classmethod
    def open(cls, path: Path, detection: float, presence: float) -> "MediaPipeHands":
        import mediapipe as mp

        vision = mp.tasks.vision
        options = vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(path), delegate=mp.tasks.BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.IMAGE,
            num_hands=2,
            min_hand_detection_confidence=detection,
            min_hand_presence_confidence=presence,
        )
        return cls(mp, vision.HandLandmarker.create_from_options(options))

    def extract(self, rgb: NDArray) -> LandmarkResult:
        try:
            image = self._module.Image(image_format=self._module.ImageFormat.SRGB, data=rgb)
            with self._lock:
                result = self._detector.detect(image)
            hands = []
            for index, points in enumerate(result.hand_landmarks[:2]):
                categories = result.handedness[index] if index < len(result.handedness) else []
                category = categories[0] if categories else None
                hands.append(Hand(
                    points=tuple(Landmark(x=p.x, y=p.y, z=p.z) for p in points),
                    handedness=category.category_name if category else None,
                    score=category.score if category else None,
                ))
            return LandmarkResult("ok", tuple(hands))
        except Exception as exc:
            log.warning("signbridge_landmarks_failed error_type=%s", type(exc).__name__)
            return LandmarkResult("unavailable")

    def close(self) -> None:
        with self._lock:
            self._detector.close()


def load_landmarks(model_path: Path | None, detection: float, presence: float) -> HandExtractor:
    if model_path is None:
        return NoLandmarks()
    try:
        return MediaPipeHands.open(model_path, detection, presence)
    except Exception as exc:
        log.warning("signbridge_landmarks_unavailable error_type=%s", type(exc).__name__)
        return NoLandmarks("unavailable")
