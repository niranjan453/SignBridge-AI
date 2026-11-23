from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://signbridge@localhost:5432/signbridge"
    jwt_secret: SecretStr | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    access_token_minutes: int = Field(30, ge=1, le=1440)
    media_max_bytes: int = Field(10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    processing_timeout_seconds: float = Field(10, gt=0, le=120)
    ai_model_version: str = "mock-1"
    ai_model_name: str = "signbridge-mock"
    ai_context_timeout_seconds: float = Field(2, gt=0, le=120)
    ws_idle_seconds: float = Field(30, gt=0, le=300)
    ws_frames_per_second: int = Field(5, ge=1, le=30)
    ws_max_sessions: int = Field(16, ge=1, le=128)
    login_attempts_per_minute: int = Field(10, ge=1)
    translations_per_minute: int = Field(30, ge=1)

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
