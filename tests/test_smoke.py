"""Smoke tests — offline, no network, no keys."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_openapi_exposes_the_chat_endpoint():
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/v1/chat/completions" in r.json()["paths"]
