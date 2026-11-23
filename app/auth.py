from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, UserSettings
from app.schemas import ChangePassword, Login, Preferences, Register, TokenOut, UpdateProfile, UserOut

router = APIRouter()
bearer = HTTPBearer(auto_error=False)
hasher = PasswordHasher()
dummy_hash = hasher.hash("dummy hash for unknown accounts")
DB = Annotated[Session, Depends(get_db)]


def signing_key(request: Request) -> str:
    secret = request.app.state.settings.jwt_secret
    if secret is None:
        raise HTTPException(503, "Account authentication is not configured")
    return secret.get_secret_value()


def credentials_error() -> HTTPException:
    return HTTPException(401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"})


def verify_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except VerifyMismatchError:
        return False


def optional_user(
    request: Request, db: DB,
    token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User | None:
    if token is None:
        if "authorization" in request.headers:
            raise credentials_error()
        return None
    try:
        claims = jwt.decode(
            token.credentials, signing_key(request), algorithms=["HS256"],
            issuer="signbridge", options={"require": ["sub", "iat", "exp", "ver"]},
        )
        user_id = UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError):
        raise credentials_error() from None
    user = db.get(User, user_id)
    if user is None or user.token_version != claims["ver"]:
        raise credentials_error()
    return user


OptionalUser = Annotated[User | None, Depends(optional_user)]


def require_user(user: OptionalUser) -> User:
    if user is None:
        raise credentials_error()
    return user


CurrentUser = Annotated[User, Depends(require_user)]


@router.post("/auth/register", response_model=UserOut, status_code=201)
def register(data: Register, request: Request, db: DB):
    request.app.state.limiter.check(request, "auth", request.app.state.settings.login_attempts_per_minute)
    signing_key(request)
    user = User(name=data.name, email=data.email.lower(), password_hash=hasher.hash(data.password),
                settings=UserSettings())
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email is already registered") from None
    return user


@router.post("/auth/login", response_model=TokenOut)
def login(data: Login, request: Request, db: DB):
    request.app.state.limiter.check(request, "auth", request.app.state.settings.login_attempts_per_minute)
    key = signing_key(request)
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    valid = verify_password(user.password_hash if user else dummy_hash, data.password)
    if user is None or not valid:
        raise credentials_error()
    now = datetime.now(timezone.utc)
    duration = timedelta(minutes=request.app.state.settings.access_token_minutes)
    token = jwt.encode(
        {"sub": str(user.id), "iss": "signbridge", "iat": now, "exp": now + duration,
         "ver": user.token_version}, key, algorithm="HS256",
    )
    return TokenOut(access_token=token, expires_in=int(duration.total_seconds()))


def invalidate_tokens(db: Session, user: User, **values: str) -> None:
    result = db.execute(
        update(User).where(User.id == user.id, User.token_version == user.token_version)
        .values(token_version=User.token_version + 1, **values)
    )
    if result.rowcount != 1:
        db.rollback()
        raise credentials_error()
    db.commit()


@router.post("/auth/logout", status_code=204)
def logout(db: DB, user: CurrentUser):
    invalidate_tokens(db, user)
    return Response(status_code=204)


@router.get("/users/me", response_model=UserOut)
def profile(user: CurrentUser):
    return user


@router.patch("/users/me", response_model=UserOut)
def update_profile(data: UpdateProfile, db: DB, user: CurrentUser):
    user.name = data.name
    db.commit()
    return user


@router.put("/users/me/password", status_code=204)
def change_password(data: ChangePassword, db: DB, user: CurrentUser):
    if not verify_password(user.password_hash, data.current_password):
        raise HTTPException(400, "Current password is incorrect")
    invalidate_tokens(db, user, password_hash=hasher.hash(data.new_password))
    return Response(status_code=204)


@router.get("/users/me/settings", response_model=Preferences)
def settings(user: CurrentUser):
    return Preferences.model_validate(user.settings, from_attributes=True)


@router.put("/users/me/settings", response_model=Preferences)
def update_settings(data: Preferences, db: DB, user: CurrentUser):
    user.settings.preferred_language = data.preferred_language
    user.settings.save_history = data.save_history
    db.commit()
    return data


@router.delete("/users/me", status_code=204)
def delete_account(db: DB, user: CurrentUser):
    db.delete(user)
    db.commit()
    return Response(status_code=204)
