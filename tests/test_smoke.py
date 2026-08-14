"""Phase 0 smoke tests — offline, no network, no keys."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_echo_returns_openai_shape():
    r = client.post(
        "/v1/chat/completions",
        json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "test-model"
    assert body["choices"][0]["message"]["content"] == "echo: hi"
    assert body["choices"][0]["finish_reason"] == "stop"


def test_echo_picks_last_user_message():
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "m",
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second"},
            ],
        },
    )
    assert r.json()["choices"][0]["message"]["content"] == "echo: second"
