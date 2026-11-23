import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from app import auth, translations
from app.adapters import TranslationService, mock_service
from app.body_limits import SmallBodyLimit
from app.config import Settings, get_settings
from app.errors import error_response
from app.limits import RateLimiter

log = logging.getLogger("uvicorn.error")


def create_app(settings: Settings | None = None, service: TranslationService | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("signbridge_start model_version=%s", app.state.translation_service.model_version)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="SignBridge AI", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.sessions = sessionmaker(engine, expire_on_commit=False)
    app.state.translation_service = service or mock_service(settings.ai_model_version)
    app.state.limiter = RateLimiter()
    app.include_router(auth.router, prefix="/api/v1", tags=["accounts"])
    app.include_router(translations.router, prefix="/api/v1")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return error_response(exc.status_code, exc.detail["message"], exc.detail["translation"], exc.headers)
        return error_response(exc.status_code, exc.detail, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"location": list(item["loc"][:2]), "type": item["type"]} for item in exc.errors()]
        return error_response(422, "Request validation failed", details)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        log.warning("signbridge_database_error error_type=%s", type(exc).__name__)
        return error_response(503, "Database operation unavailable")

    @app.exception_handler(ClientDisconnect)
    async def disconnected(request: Request, exc: ClientDisconnect):
        return error_response(400, "Upload connection closed")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        log.error("signbridge_unexpected_error error_type=%s", type(exc).__name__)
        return error_response(500, "Internal server error")

    @app.get("/api/v1/health", tags=["health"])
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/ready", tags=["health"])
    def readiness():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM users WHERE 1 = 0"))
        return {"status": "ready", "database": "ok"}

    app.add_middleware(SmallBodyLimit)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    return app


app = create_app()
