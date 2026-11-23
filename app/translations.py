import asyncio
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.adapters import ProcessingError
from app.auth import DB, CurrentUser, OptionalUser
from app.media import AUDIO_FORMATS, VIDEO_FORMATS, temporary_media
from app.models import Feedback, Translation, User
from app.schemas import FeedbackIn, HistoryOut, Language, Mode, TextInput, TranslationOut

router = APIRouter(tags=["translations"])


def translation_limit(request: Request) -> None:
    request.app.state.limiter.check(request, "translation", request.app.state.settings.translations_per_minute)


def translation_context(db: DB, user: OptionalUser) -> tuple[UUID | None, str]:
    if user is None:
        return None, "en-IN"
    preferences = user.settings
    context = (user.id if preferences.save_history else None, preferences.preferred_language)
    db.rollback()
    return context


TranslationContext = Annotated[tuple[UUID | None, str], Depends(translation_context)]


def save_result(db: DB, user_id: UUID, result: TranslationOut, text: str | None) -> None:
    db.add(Translation(
        id=result.id, user_id=user_id, mode=result.mode.value, status=result.status,
        model_version=result.model_version, is_mock=result.is_mock,
        input_text=text, output_text=result.output_text,
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.get(User, user_id) is not None:
            raise


async def process(
    request: Request, db: DB, context: TranslationContext, mode: Mode,
    language: str | None, text: str | None = None, media: Path | None = None,
) -> TranslationOut:
    user_id, preferred_language = context
    service = request.app.state.translation_service
    result = TranslationOut(
        id=uuid4(), mode=mode, status="completed",
        model_version=service.model_version, is_mock=service.is_mock,
    )
    error = None
    try:
        async with asyncio.timeout(request.app.state.settings.processing_timeout_seconds):
            output = await service.translate(mode, language or preferred_language, text, media)
            result.output_text = output.output_text
            result.audio_base64 = output.audio_base64
            result.audio_content_type = output.audio_content_type
            result.sign_tokens = output.sign_tokens
    except TimeoutError:
        result.status = "timed_out"
        error = (504, "Translation processing timed out")
    except ProcessingError:
        result.status = "failed"
        error = (502, "Translation processing failed")
    if user_id is not None:
        await run_in_threadpool(save_result, db, user_id, result, text)
    if error:
        status, message = error
        raise HTTPException(status, {"message": message, "translation": result.model_dump(mode="json")})
    return result


def media_docs(formats: dict[str, str]) -> dict:
    return {"requestBody": {"required": True, "content": {
        mime: {"schema": {"type": "string", "format": "binary"}} for mime in formats
    }}}


@router.post("/translations/sign-to-text", response_model=TranslationOut,
             dependencies=[Depends(translation_limit)], openapi_extra=media_docs(VIDEO_FORMATS))
async def sign_to_text(request: Request, db: DB, context: TranslationContext, language: Language | None = None):
    async with temporary_media(request, VIDEO_FORMATS) as path:
        return await process(request, db, context, Mode.SIGN_TO_TEXT, language, media=path)


@router.post("/translations/sign-to-speech", response_model=TranslationOut,
             dependencies=[Depends(translation_limit)], openapi_extra=media_docs(VIDEO_FORMATS))
async def sign_to_speech(request: Request, db: DB, context: TranslationContext, language: Language | None = None):
    async with temporary_media(request, VIDEO_FORMATS) as path:
        return await process(request, db, context, Mode.SIGN_TO_SPEECH, language, media=path)


@router.post("/translations/speech-to-text", response_model=TranslationOut,
             dependencies=[Depends(translation_limit)], openapi_extra=media_docs(AUDIO_FORMATS))
async def speech_to_text(request: Request, db: DB, context: TranslationContext, language: Language | None = None):
    async with temporary_media(request, AUDIO_FORMATS) as path:
        return await process(request, db, context, Mode.SPEECH_TO_TEXT, language, media=path)


@router.post("/translations/text-to-speech", response_model=TranslationOut, dependencies=[Depends(translation_limit)])
async def text_to_speech(data: TextInput, request: Request, db: DB, context: TranslationContext):
    return await process(request, db, context, Mode.TEXT_TO_SPEECH, data.language, text=data.text)


@router.post("/translations/text-to-sign", response_model=TranslationOut, dependencies=[Depends(translation_limit)])
async def text_to_sign(data: TextInput, request: Request, db: DB, context: TranslationContext):
    return await process(request, db, context, Mode.TEXT_TO_SIGN, data.language, text=data.text)


def owned_translation(db: DB, user: CurrentUser, translation_id: UUID) -> Translation:
    record = db.scalar(select(Translation).where(Translation.user_id == user.id, Translation.id == translation_id))
    if record is None:
        raise HTTPException(404, "Translation not found")
    return record


@router.get("/history", response_model=list[HistoryOut])
def list_history(db: DB, user: CurrentUser, offset: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return list(db.scalars(select(Translation).where(Translation.user_id == user.id)
                           .order_by(Translation.created_at.desc(), Translation.id.desc()).offset(offset).limit(limit)))


@router.delete("/history", status_code=204)
def clear_history(db: DB, user: CurrentUser):
    db.execute(delete(Translation).where(Translation.user_id == user.id))
    db.commit()
    return Response(status_code=204)


@router.get("/history/{translation_id}", response_model=HistoryOut)
def get_history(translation_id: UUID, db: DB, user: CurrentUser):
    return owned_translation(db, user, translation_id)


@router.delete("/history/{translation_id}", status_code=204)
def delete_history(translation_id: UUID, db: DB, user: CurrentUser):
    db.delete(owned_translation(db, user, translation_id))
    db.commit()
    return Response(status_code=204)


@router.post("/history/{translation_id}/feedback", response_model=FeedbackIn, status_code=201)
def create_feedback(translation_id: UUID, data: FeedbackIn, db: DB, user: CurrentUser):
    owned_translation(db, user, translation_id)
    db.add(Feedback(translation_id=translation_id, **data.model_dump()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        owned_translation(db, user, translation_id)
        raise HTTPException(409, "Feedback already exists for this translation") from None
    return data
