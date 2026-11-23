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
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --ws websockets --ws-max-size 98304 --ws-max-queue 1 --no-access-log
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

## Live sign recognition

Connect to `ws://127.0.0.1:8000/api/v1/ws/sign-recognition` and send a JSON text message:

```json
{"type":"start","language":"en-IN"}
```

The language is optional. Guests default to `en-IN`. To authenticate, include `access_token` in this first message. Authenticated sessions default to the account's preferred language, and authentication is checked at session start. Use `wss://` for remote clients. Browser origins must be listed in `CORS_ORIGINS`.

The server replies with `type: "session"`, a `session_id`, `language`, `authenticated`, `max_frame_bytes`, and `frames_per_second`. Send one frame at a time, using the returned session ID and a base64 image without a data URL prefix:

```json
{"type":"frame","session_id":"<returned UUID>","sequence":0,"timestamp_ms":1760000000000,"content_type":"image/jpeg","data":"<base64 JPEG bytes>"}
```

Use JPEG or PNG frames of up to 64 KiB, increasing integer sequences, and nondecreasing integer capture timestamps in milliseconds. Wait for each reply and respect the advertised frame rate. A `result` echoes the session ID, sequence, and capture timestamp and wraps the translation under `translation`. Protocol errors use `{"type":"error","error":{"code":"...","message":"..."}}`. Processing errors use a `result` with the translation status `failed` or `timed_out` and a structured translation error. Oversized messages close with code 1009. Idle sessions and repeated rate violations close with code 1008. The Uvicorn command above also limits message size and receive queue depth at the transport. Frames are processed in memory, and live results are scoped to the connection.

HTTP and WebSocket translations provide input type, recognized text, optional confidence, processing time in milliseconds, model name and version, mock flag, UTC timestamp, and optional error details. The mock sign recognizer emits a fixed demonstration confidence. History continues to use its existing record format.

## Tests and integration

Run `.venv/Scripts/python -m pytest -q` for the suite with SQLite. Set `TEST_DATABASE_URL` to a PostgreSQL database whose test login can create schemas, then run the same command for PostgreSQL coverage. The suite creates and drops a unique schema per test.

Implement the async interfaces in `app/adapters.py` and pass a configured `TranslationService` to `create_app` to integrate real models later. Both HTTP and live frames use the same orchestration. The sign pipeline runs recognition, contextual retrieval, and LLM processing, then calls speech synthesis for sign-to-speech. Speech-to-text calls speech recognition directly. The two text pipelines call speech synthesis or sign representation directly. The typed LLM contract supports contextual processing, grammar correction, and sign sentence reconstruction. The retrieval contract carries source metadata and optional relevance. Gemini and FAISS can replace those adapters through the service configuration. Context stages use a separate timeout and fall back to recognized text if unavailable or too slow. Adapters should propagate cancellation and raise `ProcessingError` or `ServiceUnavailable` for expected failures. The frontend can configure its origin through `CORS_ORIGINS` and use the OpenAPI schema for integration.
