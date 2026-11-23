# SignBridge AI

FastAPI backend for the SignBridge AI Indian Sign Language communication project. All five translation modes accept guest requests and run with replaceable mock adapters. The speech mock returns a short silent WAV, recognition returns fixed sample text, and the sign representation returns uppercase word tokens.

## Docker Setup — PostgreSQL & FastAPI Backend

### Prerequisites

Install Git and Docker Desktop (Windows or macOS), or Docker Engine with the Compose plugin (Linux). Use Linux containers. Check that `docker info` reaches the engine and `docker compose version` prints a version. Allow roughly 2 GB of available RAM for Docker and 2 GB of free disk space for the images and local database. The mock adapters work without a GPU.

### First run

1. Clone the repository and open its directory.

   ```sh
   git clone https://github.com/niranjan453/SignBridge-AI.git
   cd SignBridge-AI
   ```

2. Copy `.env.example` to `.env` using `Copy-Item .env.example .env` in PowerShell or `cp .env.example .env` on macOS/Linux. Keep existing values if you already have a configured local database. Add the new settings from the template as needed.
3. Generate two distinct random values. Use the first for `POSTGRES_PASSWORD` and the second for `JWT_SECRET`. Keep the password URL-safe, as produced by this command.

   ```sh
   docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(32)); print(secrets.token_urlsafe(48))"
   ```

   Keep `POSTGRES_DB=signbridge_db`, `POSTGRES_USER=signbridge_user`, and `APP_ENV=development` for the default setup. For Python running directly on your host, update the password and port in `DATABASE_URL` as well. Compose constructs its own database URL with the internal hostname `db` and port `5432`. The host database port and backend port are published on loopback. Choose `POSTGRES_HOST_PORT=5433` or `BACKEND_PORT=8001` if either default port is occupied. Keep `.env` private.
4. Validate Compose, build the backend image, and start both development services. Compose waits for PostgreSQL, then runs Alembic before starting Uvicorn.

   ```sh
   docker compose config --quiet
   docker compose build backend
   docker compose up -d --wait db backend
   docker compose ps
   ```

5. Request liveness and database readiness, then open Swagger at `http://localhost:8000/docs`. In Windows PowerShell use `curl.exe` if `curl` is aliased. Replace `8000` in the URLs if you changed `BACKEND_PORT`.

   ```sh
   curl -fsS http://localhost:8000/api/v1/health
   curl -fsS http://localhost:8000/api/v1/ready
   ```

   Expect `{"status":"ok"}` and `{"status":"ready","database":"ok"}`. If you need to apply or verify migrations manually with the stack running:

   ```sh
   docker compose exec backend alembic upgrade head
   docker compose exec backend alembic current
   docker compose exec backend alembic check
   ```

### Tests and live API checks

Run the complete Pytest suite inside the backend image:

```sh
docker compose run --rm tests
docker compose --profile test stop test-db
```

The `tests` service starts a separate PostgreSQL 17 instance with the `signbridge_test` database on a test-only network. Its storage is temporary, and Pytest creates a unique schema per database test. The suite exercises PostgreSQL migrations including downgrade and upgrade, accounts, guest translations, history, uploads, mock adapters, and WebSockets. It can run with the development stack stopped. Rebuild the backend image after changing code or dependencies.

For a live check, use Swagger to send `POST /api/v1/auth/register` with a new email, display name, and a password of at least 12 characters. Send the email and password to `POST /api/v1/auth/login`, use the returned JWT in Swagger's **Authorize** dialog, then call `GET /api/v1/users/me`. For a guest check, clear authorization and send `{"text":"Hello India"}` to `POST /api/v1/translations/text-to-sign`. Expect `status: completed`, `is_mock: true`, and the sign tokens `HELLO` and `INDIA`.

For the live WebSocket check, connect a WebSocket client to `ws://localhost:8000/api/v1/ws/sign-recognition`, send `{"type":"start"}`, and submit a small base64 JPEG or PNG using the returned session ID. Expect a `result` with `translation.status: completed`. The [live sign recognition section](#live-sign-recognition) describes the message fields and optional token.

```sh
docker compose logs --tail=100 db backend
docker compose logs -f backend
```

### Database and container management

```sh
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

At the `psql` prompt, run `\dt` to list tables. Expect `users`, `user_settings`, `translations`, `translation_feedback`, and `alembic_version`. Run `\q` to exit. Use `docker compose exec backend alembic current` to check the migration revision.

To restart both development services in dependency order, run `docker compose stop db backend` followed by `docker compose up -d --wait db backend`. Use `docker compose stop` to stop the stack while retaining the containers, or `docker compose --profile test down` to remove containers and networks while retaining development data. The named `postgres_data` volume survives both stopping and recreating containers.

**A volume reset permanently deletes this Compose project's development database.** Before resetting, confirm the project with `docker compose ls`, back up data you need, and agree the reset with teammates if data is shared. For an intentional local reset only, run `docker compose --profile test down --volumes`, then `docker compose up -d --wait db backend`. PostgreSQL initializes a fresh database and the backend applies migrations.

### Troubleshooting

| Symptom | What to check |
| --- | --- |
| Docker is unavailable | Start Docker Desktop or Docker Engine, then retry `docker info`. |
| Port already in use | Change `BACKEND_PORT` or `POSTGRES_HOST_PORT` in `.env` and rerun `docker compose up -d --wait db backend`. Use the updated port in browser or host database URLs. |
| Database connection refused | Check `docker compose ps` and `docker compose logs db`. Inside Compose the host is `db:5432`. From the host it is `localhost` and the configured mapped port. |
| Database stays unhealthy | Inspect `docker compose logs db` for initialization or storage errors. PostgreSQL initializes the configured role, password, and database on its first boot with an empty volume. For an existing volume, use its existing credentials or update the role password in PostgreSQL. |
| Missing or invalid environment values | Check that `.env` exists and contains a URL-safe `POSTGRES_PASSWORD` and a random `JWT_SECRET` of at least 32 bytes. Run `docker compose config --quiet` to check interpolation without printing the rendered configuration. |
| Alembic or backend startup fails | Inspect `docker compose logs backend`. Check database credentials and schema before retrying `docker compose up -d --wait db backend`. Treat a migration failure as a database issue and keep the existing volume while investigating. |
| Dependency installation fails | Check Docker's network and proxy settings and retry `docker compose build backend`. The build downloads Python packages from the configured package index. |

## Local setup without Docker

Use Python 3.11 or newer and create a PostgreSQL database named `signbridge_db` with a login that owns it. Run these PowerShell commands from the repository root:

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
