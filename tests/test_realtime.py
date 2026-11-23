import base64
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect

from app.adapters import CameraFrame, ServiceUnavailable
from app.realtime import MAX_FRAME_BYTES
from test_backend import API, account

PATH = f"{API}/ws/sign-recognition"
JPEG = b"\xff\xd8\xffframe\xff\xd9"


def start(socket, **kwargs):
    socket.send_json({"type": "start", **kwargs})
    return socket.receive_json()


def frame(session, sequence=0, data=JPEG, **overrides):
    return {
        "type": "frame", "session_id": session["session_id"], "sequence": sequence,
        "timestamp_ms": 100 + sequence, "content_type": "image/jpeg",
        "data": base64.b64encode(data).decode("ascii"), **overrides,
    }


def test_guest_sessions_and_metadata(client):
    with client.websocket_connect(PATH) as first, client.websocket_connect(PATH) as second:
        session = start(first)
        other = start(second, language="hi-IN")
        assert session["authenticated"] is False
        assert session["session_id"] != other["session_id"]
        assert other["language"] == "hi-IN"
        first.send_json(frame(session))
        result = first.receive_json()
        assert result["type"] == "result"
        assert result["session_id"] == session["session_id"]
        assert result["sequence"] == 0
        assert result["timestamp_ms"] == 100
        translation = result["translation"]
        assert translation["input_type"] == "frame"
        assert translation["mode"] == "sign-to-text"
        assert translation["recognized_text"] == translation["output_text"]
        assert translation["model_name"] == "signbridge-mock"
        assert translation["model_version"] == "mock-1"
        assert translation["processing_time_ms"] >= 0
        assert translation["timestamp"].endswith("Z")
        assert translation["is_mock"] is True
        assert translation["confidence"] == 0.95
    assert not client.app.state.sign_sessions


def test_authenticated_session_and_invalid_token(client):
    headers = account(client)
    assert client.put(f"{API}/users/me/settings", headers=headers, json={
        "preferred_language": "hi-IN", "save_history": True,
    }).status_code == 200
    with client.websocket_connect(PATH) as socket:
        session = start(socket, access_token=headers["Authorization"].split()[1])
        assert session["authenticated"] and session["language"] == "hi-IN"
        socket.send_json(frame(session))
        assert socket.receive_json()["translation"]["status"] == "completed"
    assert client.get(f"{API}/history", headers=headers).json() == []
    with client.websocket_connect(PATH) as socket:
        assert start(socket, access_token="bad")["error"]["code"] == "invalid_credentials"
        with pytest.raises(WebSocketDisconnect) as disconnected:
            socket.receive_json()
        assert disconnected.value.code == 1008


@pytest.mark.parametrize("overrides,error", [
    ({"session_id": str(uuid4())}, "invalid_session"),
    ({"data": "%%%"}, "invalid_frame"),
    ({"content_type": "image/png"}, "invalid_frame"),
    ({"sequence": "0"}, "invalid_frame"),
    ({"timestamp_ms": -1}, "invalid_frame"),
])
def test_invalid_frame_recovery(client, overrides, error):
    with client.websocket_connect(PATH) as socket:
        session = start(socket)
        socket.send_json({**frame(session), **overrides})
        assert socket.receive_json()["error"]["code"] == error
        time.sleep(1 / session["frames_per_second"] + 0.015)
        socket.send_json(frame(session))
        assert socket.receive_json()["translation"]["status"] == "completed"


@pytest.mark.parametrize("size", [MAX_FRAME_BYTES + 1, MAX_FRAME_BYTES + 1024])
def test_oversized_frame_closes_connection(client, size):
    with client.websocket_connect(PATH) as socket:
        socket.send_json(frame(start(socket), data=b"x" * size))
        assert socket.receive_json()["error"]["code"] == "frame_too_large"
        with pytest.raises(WebSocketDisconnect) as disconnected:
            socket.receive_json()
        assert disconnected.value.code == 1009
    assert not client.app.state.sign_sessions


def test_sequence_rate_limit_and_idle_cleanup(create_client):
    client, _ = create_client(ws_idle_seconds=1, ws_frames_per_second=3)
    with client, client.websocket_connect(PATH) as socket:
        session = start(socket)
        socket.send_json(frame(session))
        assert socket.receive_json()["type"] == "result"
        socket.send_json(frame(session, 1))
        assert socket.receive_json()["error"]["code"] == "rate_limit"
        time.sleep(0.36)
        socket.send_json(frame(session))
        assert socket.receive_json()["error"]["code"] == "invalid_sequence"
        assert socket.receive_json()["error"]["code"] == "idle_timeout"
        with pytest.raises(WebSocketDisconnect) as disconnected:
            socket.receive_json()
        assert disconnected.value.code == 1008
    assert not client.app.state.sign_sessions


def test_session_capacity_and_origin(create_client):
    client, _ = create_client(ws_max_sessions=1)
    with client:
        with pytest.raises(WebSocketDisconnect) as disconnected:
            with client.websocket_connect(PATH, headers={"Origin": "http://unlisted.example"}):
                pass
        assert disconnected.value.code == 1008
        with client.websocket_connect(PATH) as socket:
            assert start(socket)["type"] == "session"
            with pytest.raises(WebSocketDisconnect) as disconnected:
                with client.websocket_connect(PATH):
                    pass
            assert disconnected.value.code == 1013
        assert not client.app.state.sign_sessions


def test_processing_failure_hides_details(client):
    class UnavailableSign:
        async def recognize_sign(self, media, language):
            assert isinstance(media, CameraFrame) and media.data == JPEG
            raise ServiceUnavailable("private provider details")

    service = client.app.state.translation_service
    client.app.state.translation_service = replace(service, sign=UnavailableSign())
    with client.websocket_connect(PATH) as socket:
        socket.send_json(frame(start(socket)))
        result = socket.receive_json()["translation"]
        assert result["status"] == "failed"
        assert result["error"]["code"] == "service_unavailable"
        assert "private" not in str(result)
