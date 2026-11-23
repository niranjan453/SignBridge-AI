from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://signbridge@localhost:5432/signbridge"
    jwt_secret: SecretStr | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    access_token_minutes: int = Field(30, ge=1, le=1440)
    media_max_bytes: int = Field(10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    processing_timeout_seconds: float = Field(10, gt=0, le=120)
    ai_model_version: str = "mock-1"
    ai_model_name: str = "signbridge-mock"
    ai_context_timeout_seconds: float = Field(2, gt=0, le=120)
    cv_max_side: int = Field(224, ge=16, le=640)
    cv_max_pixels: int = Field(2_073_600, ge=256, le=8_294_400)
    cv_rotation: Literal["0", "90", "180", "270"] = "0"
    cv_color: Literal["rgb", "bgr"] = "rgb"
    cv_normalize: bool = True
    cv_video_max_frames: int = Field(240, ge=1, le=600)
    cv_video_max_seconds: float = Field(15, gt=0, le=60)
    cv_video_max_samples: int = Field(12, ge=1, le=24)
    cv_video_sample_fps: float = Field(4, gt=0, le=30)
    cv_ws_window: int = Field(6, ge=1, le=8)
    cv_ws_max_lag_ms: int = Field(2000, ge=250, le=10000)
    mediapipe_model_path: Path | None = None
    mediapipe_detection_confidence: float = Field(0.5, ge=0, le=1)
    mediapipe_presence_confidence: float = Field(0.5, ge=0, le=1)
    ws_idle_seconds: float = Field(30, gt=0, le=300)
    ws_frames_per_second: int = Field(5, ge=1, le=30)
    ws_max_sessions: int = Field(16, ge=1, le=128)
    login_attempts_per_minute: int = Field(10, ge=1)
    translations_per_minute: int = Field(30, ge=1)

    @model_validator(mode="after")
    def validate_vision_memory(self) -> "Settings":
        bytes_per_pixel = 12 if self.cv_normalize else 3
        retained_frames = self.cv_ws_window * self.ws_max_sessions + 2 * self.cv_video_max_samples
        if retained_frames * self.cv_max_side**2 * bytes_per_pixel > 160 * 1024 * 1024:
            raise ValueError("Reduce computer vision resolution, session count, or sample limits")
        return self

    @field_validator("mediapipe_model_path", mode="before")
    @classmethod
    def empty_model_path(cls, value):
        return value or None

    @field_validator("jwt_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            secret = value.get_secret_value()
            if len(secret.encode()) < 32 or secret.startswith("replace-"):
                raise ValueError("Configure a randomly generated JWT secret of at least 32 bytes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
