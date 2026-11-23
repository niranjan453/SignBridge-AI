from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Password = Annotated[str, StringConstraints(min_length=12, max_length=128)]
Language = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{1,8})*$", max_length=35)]


class Login(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=128)]


class Register(Login):
    name: Name
    password: Password


class UpdateProfile(BaseModel):
    name: Name


class ChangePassword(BaseModel):
    current_password: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Password


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: EmailStr
    created_at: datetime
    updated_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class Preferences(BaseModel):
    preferred_language: Language = "en-IN"
    save_history: bool = True


class Mode(StrEnum):
    SIGN_TO_TEXT = "sign-to-text"
    SIGN_TO_SPEECH = "sign-to-speech"
    SPEECH_TO_TEXT = "speech-to-text"
    TEXT_TO_SPEECH = "text-to-speech"
    TEXT_TO_SIGN = "text-to-sign"


class TextInput(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    language: Language | None = None


class TranslationError(BaseModel):
    code: str
    message: str


class VisionMetadata(BaseModel):
    width: int
    height: int
    sample_count: int
    preprocessing_ms: float
    landmark_ms: float
    landmark_status: Literal["disabled", "unavailable", "ok"]
    hands_detected: int
    fps: float | None = None
    frame_count: int | None = None
    duration_seconds: float | None = None


class TranslationOut(BaseModel):
    id: UUID
    mode: Mode
    status: str
    model_version: str
    is_mock: bool
    output_text: str | None = None
    audio_base64: str | None = None
    audio_content_type: str | None = None
    sign_tokens: list[str] | None = None
    input_type: Literal["text", "audio", "video", "frame"]
    recognized_text: str | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None
    processing_time_ms: Annotated[float, Field(ge=0)]
    model_name: str
    timestamp: datetime
    error: TranslationError | None = None
    vision: VisionMetadata | None = None


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mode: Mode
    status: str
    model_version: str
    is_mock: bool
    input_text: str | None
    output_text: str | None
    created_at: datetime


class FeedbackIn(BaseModel):
    rating: Annotated[int, Field(ge=1, le=5)]
    comment: Annotated[str, Field(max_length=500)] | None = None
