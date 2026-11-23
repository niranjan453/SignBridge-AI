# SignBridge AI

FastAPI backend for the SignBridge AI Indian Sign Language communication project. All five translation modes accept guest requests and run with replaceable mock adapters. The speech mock returns a short silent WAV, recognition returns fixed sample text, and the sign representation returns uppercase word tokens.

## Local setup

Use Python 3.11 or newer and create a PostgreSQL database named `signbridge` with a login that owns it. Run these PowerShell commands from the repository root:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv/Scripts/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `JWT_SECRET` in `.env` to the generated value and set `DATABASE_URL` to your PostgreSQL connection URL. URL-encode reserved characters in credentials. On macOS or Linux use `.venv/bin/python` and `cp` in place of the corresponding PowerShell commands.

```powershell
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Swagger is at `http://127.0.0.1:8000/docs` and the OpenAPI schema is at `/openapi.json`. The liveness endpoint is `/api/v1/health`. Database readiness is checked at `/api/v1/ready`. With the JWT secret unset, guest translations and liveness remain available and account authentication returns 503.

## API

All routes below use the `/api/v1` prefix.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/auth/register`, `/auth/login`, `/auth/logout` | Register, obtain a JWT, or invalidate current account tokens |
| GET, PATCH, DELETE | `/users/me` | Read or update the display name, or delete the account and its data |
| PUT | `/users/me/password` | Change the password and invalidate current account tokens |
| GET, PUT | `/users/me/settings` | Read or replace language and history preferences |
| POST | `/translations/text-to-speech`, `/translations/text-to-sign` | Submit JSON with `text` and optional `language` |
| POST | `/translations/sign-to-text`, `/translations/sign-to-speech` | Upload MP4 or WebM with optional `language` query parameter |
| POST | `/translations/speech-to-text` | Upload WAV, MP3, or Ogg with optional `language` query parameter |
| GET, DELETE | `/history` | List saved translations with `offset` and `limit`, or clear history |
| GET, DELETE | `/history/{translation_id}` | Read or delete an owned record |
| POST | `/history/{translation_id}/feedback` | Submit one rating from 1 to 5 and an optional comment |

Send a raw request body for media routes, with the matching `Content-Type` header. For example:

```powershell
curl.exe -H "Content-Type: audio/wav" --data-binary "@sample.wav" http://127.0.0.1:8000/api/v1/translations/speech-to-text
```

The upload limit defaults to 10 MiB. Temporary files are scoped to the request and removed after processing. History stores text and processing metadata. The response includes any mock WAV or sign tokens. Use the `Authorization: Bearer <token>` header to save translations when history is enabled. Failed and timed-out signed-in requests return a translation status under `error.details` and can also appear in history. Submit `[mock-error]` to either text route to exercise the simulated failure. Rate limits apply per client IP and server process, so use one worker for the demo.

## Tests and integration

Run `.venv/Scripts/python -m pytest -q` for the suite with SQLite. Set `TEST_DATABASE_URL` to a PostgreSQL database whose test login can create schemas, then run the same command for PostgreSQL coverage. The suite creates and drops a unique schema per test.

Implement the async interfaces in `app/adapters.py` and pass a configured `TranslationService` to `create_app` to integrate real models later. Adapters should propagate cancellation for processing timeouts and raise `ProcessingError` for expected processing failures. The frontend can configure its origin through `CORS_ORIGINS` and use the OpenAPI schema for integration.
