import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import Feedback, Translation, User, UserSettings
from conftest import SECRET
from vision_fixtures import write_video

API = "/api/v1"
EMAIL = "demo@example.com"
PASSWORD = "valid password for testing"
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "


def register(client, email=EMAIL):
    return client.post(f"{API}/auth/register", json={"name": "Demo", "email": email, "password": PASSWORD})


def login(client, email=EMAIL, password=PASSWORD):
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


def account(client, email=EMAIL):
    assert register(client, email).status_code == 201
    response = login(client, email)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health_docs_cors_and_migrations(create_client):
    client, config = create_client()
    with client:
        assert client.get(f"{API}/health").json() == {"status": "ok"}
        assert client.get(f"{API}/ready").status_code == 200
        assert client.get("/docs").status_code == 200
        assert f"{API}/translations/speech-to-text" in client.get("/openapi.json").json()["paths"]
        response = client.options(f"{API}/translations/text-to-sign", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        })
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        command.check(config)
        command.downgrade(config, "base")
        assert client.get(f"{API}/ready").status_code == 503
        command.upgrade(config, "head")
        assert client.get(f"{API}/ready").status_code == 200


def test_guest_without_database_or_secret(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'empty.sqlite'}", jwt_secret=None)
    with TestClient(create_app(settings)) as client:
        assert client.get(f"{API}/ready").status_code == 503
        assert client.post(f"{API}/translations/text-to-sign", json={"text": "Hello"}).status_code == 200
        assert register(client).status_code == 503
    with pytest.raises(ValidationError):
        Settings(jwt_secret="replace-with-a-random-secret-at-least-32-bytes")


def test_authentication_and_account(client):
    assert client.get(f"{API}/users/me").status_code == 401
    assert register(client, "invalid").status_code == 422
    assert register(client, EMAIL).status_code == 201
    assert register(client, "DEMO@EXAMPLE.COM").status_code == 409
    assert login(client, password="incorrect").status_code == 401
    token = login(client).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    assert token["expires_in"] == 1800
    profile = client.get(f"{API}/users/me", headers=headers).json()
    assert profile["name"] == "Demo"
    assert "password_hash" not in profile
    with client.app.state.sessions() as db:
        user = db.scalar(select(User))
        assert user.password_hash.startswith("$argon2id$")
    assert client.patch(f"{API}/users/me", headers=headers, json={"name": "New name"}).json()["name"] == "New name"
    assert client.get(f"{API}/users/me/settings", headers=headers).json()["save_history"]
    assert client.put(f"{API}/users/me/settings", headers=headers, json={
        "preferred_language": "hi-IN", "save_history": False,
    }).status_code == 200
    assert client.post(f"{API}/translations/text-to-sign", headers=headers, json={"text": "private"}).status_code == 200
    assert client.get(f"{API}/history", headers=headers).json() == []
    assert client.put(f"{API}/users/me/password", headers=headers, json={
        "current_password": "wrong", "new_password": "new secure password",
    }).status_code == 400
    assert client.put(f"{API}/users/me/password", headers=headers, json={
        "current_password": PASSWORD, "new_password": "new secure password",
    }).status_code == 204
    assert client.get(f"{API}/users/me", headers=headers).status_code == 401
    assert login(client).status_code == 401
    headers = {"Authorization": "Bearer " + login(client, password="new secure password").json()["access_token"]}
    assert client.post(f"{API}/auth/logout", headers=headers).status_code == 204
    assert client.get(f"{API}/users/me", headers=headers).status_code == 401
    headers = {"Authorization": "Bearer " + login(client, password="new secure password").json()["access_token"]}
    assert client.delete(f"{API}/users/me", headers=headers).status_code == 204
    assert client.get(f"{API}/users/me", headers=headers).status_code == 401
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(UserSettings)) == 0


def test_invalid_and_expired_tokens(client):
    now = datetime.now(timezone.utc)
    expired = jwt.encode({
        "sub": str(uuid4()), "iss": "signbridge", "iat": now - timedelta(minutes=2),
        "exp": now - timedelta(minutes=1), "ver": 0,
    }, SECRET, algorithm="HS256")
    for header in ("Basic abc", "Bearer invalid", f"Bearer {expired}"):
        response = client.post(f"{API}/translations/text-to-sign", json={"text": "Hello"},
                               headers={"Authorization": header})
        assert response.status_code == 401


@pytest.mark.parametrize("mode,media,mime,output", [
    ("sign-to-text", None, "video/mp4", "output_text"),
    ("sign-to-speech", None, "video/mp4", "audio_base64"),
    ("speech-to-text", WAV, "audio/wav", "output_text"),
    ("text-to-speech", None, None, "audio_base64"),
    ("text-to-sign", None, None, "sign_tokens"),
])
def test_guest_modes(client, tmp_path, mode, media, mime, output):
    if mime == "video/mp4":
        media = write_video(tmp_path / "guest.mp4").read_bytes()
    args = {"content": media, "headers": {"Content-Type": mime}} if media else {"json": {"text": "Hello India"}}
    response = client.post(f"{API}/translations/{mode}", **args)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed"
    assert result["is_mock"] is True
    assert result["model_version"] == "mock-1"
    assert result[output]
    if output == "audio_base64":
        assert base64.b64decode(result[output]).startswith(b"RIFF")
    assert client.get(f"{API}/history/{result['id']}").status_code == 401
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Translation)) == 0


def test_history_ownership_feedback_and_cascade(client):
    first = account(client)
    second = account(client, "another@example.com")
    def translate(text):
        return client.post(f"{API}/translations/text-to-sign", headers=first, json={"text": text}).json()["id"]

    older = translate("Older")
    newer = translate("Newer")
    history = client.get(f"{API}/history", headers=first).json()
    assert {record["id"] for record in history} == {older, newer}
    assert client.get(f"{API}/history?limit=1&offset=1", headers=first).json() == history[1:]
    path = f"{API}/history/{newer}"
    assert client.get(path, headers=first).json()["input_text"] == "Newer"
    assert client.get(path, headers=second).status_code == 404
    assert client.delete(path, headers=second).status_code == 404
    assert client.post(path + "/feedback", headers=second, json={"rating": 5}).status_code == 404
    assert client.post(path + "/feedback", headers=first, json={"rating": 0}).status_code == 422
    assert client.post(path + "/feedback", headers=first, json={"rating": 5}).status_code == 201
    assert client.post(path + "/feedback", headers=first, json={"rating": 4}).status_code == 409
    assert client.delete(path, headers=first).status_code == 204
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Feedback)) == 0
    assert client.delete(f"{API}/history", headers=first).status_code == 204
    assert client.get(f"{API}/history", headers=first).json() == []
    translate("Will be removed with account")
    assert client.delete(f"{API}/users/me", headers=first).status_code == 204
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Translation)) == 0


def test_validation_processing_failure_and_upload_limits(client):
    headers = account(client)
    response = client.post(f"{API}/translations/text-to-sign", headers=headers, json={"text": "[mock-error]"})
    assert response.status_code == 502
    result = response.json()["error"]["details"]
    assert result["status"] == "failed"
    assert client.get(f"{API}/history/{result['id']}", headers=headers).json()["status"] == "failed"
    assert client.post(f"{API}/translations/text-to-sign", json={"text": " "}).status_code == 422
    assert client.post(f"{API}/translations/text-to-sign", json={"text": "x" * 32768}).status_code == 413
    path = f"{API}/translations/speech-to-text"
    assert client.post(path, headers={"Content-Type": "text/plain"}, content=WAV).status_code == 415
    assert client.post(path, headers={"Content-Type": "audio/wav"}, content=b"bad").status_code == 415
    assert client.post(path, headers={"Content-Type": "audio/wav"}, content=b"").status_code == 422
    assert client.post(path, headers={"Content-Type": "audio/wav"}, content=WAV * 1000).status_code == 413
    assert client.post(path, headers={"Content-Type": "audio/wav"}, content=iter([WAV, b"a" * 4096])).status_code == 413


def test_timeout_and_media_cleanup(create_client):
    client, _ = create_client(processing_timeout_seconds=0.01)
    paths: list[Path] = []

    class Speech:
        async def recognize_speech(self, media, language):
            assert media.read_bytes() == WAV
            paths.append(media)
            await asyncio.sleep(1)

    with client:
        client.app.state.translation_service = replace(client.app.state.translation_service, speech=Speech())
        response = client.post(f"{API}/translations/speech-to-text", content=WAV,
                               headers={"Content-Type": "audio/wav", "X-Filename": "../secret.wav"})
        assert response.status_code == 504
        assert response.json()["error"]["details"]["status"] == "timed_out"
        assert len(paths) == 1
        assert paths[0].suffix == ".wav"
        assert not paths[0].parent.exists()
        assert client.get(f"{API}/media/{paths[0].name}").status_code == 404


def test_rate_limits(create_client):
    client, _ = create_client(login_attempts_per_minute=2, translations_per_minute=1)
    with client:
        assert login(client, password="incorrect").status_code == 401
        assert login(client, password="incorrect").status_code == 401
        blocked = login(client, password="incorrect")
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
        assert client.post(f"{API}/translations/text-to-sign", json={"text": "Hello"}).status_code == 200
        assert client.post(f"{API}/translations/text-to-sign", json={"text": "Hello"}).status_code == 429
