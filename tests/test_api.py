"""End-to-end through the HTTP layer — still no network."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import config, providers, queue, worker
from app.main import app

from tests.helpers import FakeResponse, always_failing, by_model, fails

client = TestClient(app)

HEADERS = {"X-Tenant": "acme", "X-Feature": "chat", "X-Request-Id": "req-123"}
BODY = {"model": "gateway-auto", "messages": [{"role": "user", "content": "hi"}]}


def post(body=None, **extra_headers):
    return client.post(
        "/v1/chat/completions", json=body or BODY, headers={**HEADERS, **extra_headers}
    )


def all_providers_down(monkeypatch):
    monkeypatch.setattr(providers, "_completion", always_failing())


# --- basics ------------------------------------------------------------------

def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_metrics_endpoint_exposes_the_gateway_series():
    body = client.get("/metrics").text
    assert "gateway_requests_total" in body
    assert "gateway_breaker_state" in body
    assert "gateway_cost_usd_total" in body


# --- mandatory attribution ---------------------------------------------------

@pytest.mark.parametrize("drop", ["X-Tenant", "X-Feature", "X-Request-Id"])
def test_missing_metadata_is_400(drop):
    headers = {k: v for k, v in HEADERS.items() if k != drop}
    r = client.post("/v1/chat/completions", json=BODY, headers=headers)
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "missing_metadata"
    assert drop in r.json()["error"]["message"]


def test_unknown_request_class_is_400():
    r = post(**{"X-Class": "urgent"})
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_class"


# --- happy path --------------------------------------------------------------

def test_completion_is_openai_shaped_with_a_gateway_block():
    r = post()
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["model"] == config.PROVIDER_MODELS["groq"]
    assert body["usage"]["total_tokens"] == 8
    assert body["gateway"]["provider"] == "groq"
    assert body["gateway"]["failovers"] == 0
    assert r.headers["X-Gateway-Provider"] == "groq"
    assert r.headers["X-Request-Id"] == "req-123"


def test_failover_is_reported_in_the_response(monkeypatch):
    monkeypatch.setattr(
        providers, "_completion", by_model({config.PROVIDER_MODELS["groq"]: fails()})
    )
    r = post()
    assert r.status_code == 200
    assert r.headers["X-Gateway-Provider"] == "gemini"
    assert r.headers["X-Gateway-Failovers"] == "1"
    assert r.json()["gateway"]["attempts"][0]["error_type"] == "server_error"


# --- streaming ---------------------------------------------------------------

def _sse_deltas(text: str) -> list[str]:
    """Pull the content deltas out of an SSE body, the way a client would."""
    out = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        out.append(json.loads(payload)["choices"][0]["delta"].get("content"))
    return [c for c in out if c]


def test_stream_true_returns_openai_sse(monkeypatch):
    monkeypatch.setattr(providers, "_completion", lambda **kw: FakeResponse("hello there world"))
    r = post({**BODY, "stream": True})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "".join(_sse_deltas(r.text)) == "hello there world"
    assert r.text.rstrip().endswith("data: [DONE]")


def test_streamed_chunks_are_chat_completion_chunks(monkeypatch):
    monkeypatch.setattr(providers, "_completion", lambda **kw: FakeResponse("one two"))
    frames = [
        json.loads(line[5:])
        for line in post({**BODY, "stream": True}).text.splitlines()
        if line.startswith("data:") and line[5:].strip() != "[DONE]"
    ]
    assert {f["object"] for f in frames} == {"chat.completion.chunk"}
    assert frames[0]["choices"][0]["delta"] == {"role": "assistant"}   # opening frame
    assert frames[-1]["choices"][0]["finish_reason"] == "stop"         # closing frame


def test_streaming_still_fails_over(monkeypatch):
    monkeypatch.setattr(
        providers, "_completion", by_model({config.PROVIDER_MODELS["groq"]: fails()})
    )
    r = post({**BODY, "stream": True})
    assert r.status_code == 200
    assert r.headers["X-Gateway-Provider"] == "gemini"   # buffered routing, so failover works


def test_streaming_errors_are_still_json(monkeypatch):
    all_providers_down(monkeypatch)
    r = post({**BODY, "stream": True})
    assert r.status_code == 503
    assert r.json()["error"]["type"] == "no_provider_available"


def test_an_idempotent_replay_of_a_stream_is_also_a_stream(monkeypatch):
    monkeypatch.setattr(providers, "_completion", lambda **kw: FakeResponse("cached answer"))
    post({**BODY, "stream": True}, **{"X-Idempotency-Key": "k-stream"})
    replay = post({**BODY, "stream": True}, **{"X-Idempotency-Key": "k-stream"})
    assert replay.headers["X-Idempotent-Replay"] == "true"
    assert "".join(_sse_deltas(replay.text)) == "cached answer"


# --- everything down ---------------------------------------------------------

def test_interactive_fails_fast_when_nothing_works(monkeypatch):
    all_providers_down(monkeypatch)
    r = post()
    assert r.status_code == 503
    assert r.json()["error"]["type"] == "no_provider_available"


def test_deferrable_work_is_queued_instead(monkeypatch):
    all_providers_down(monkeypatch)
    r = post(**{"X-Class": "deferrable"})
    assert r.status_code == 202
    payload = r.json()
    assert payload["status"] == "queued"
    assert queue.depth() == 1

    status = client.get(payload["poll"]).json()
    assert status["status"] == "queued"


def test_a_queued_job_drains_once_a_provider_returns(monkeypatch):
    all_providers_down(monkeypatch)
    job_id = post(**{"X-Class": "deferrable"}).json()["job_id"]

    monkeypatch.setattr(providers, "_completion", lambda **kw: FakeResponse("recovered"))
    assert worker.run_once()["status"] == "done"

    result = client.get(f"/v1/jobs/{job_id}").json()
    assert result["status"] == "done"
    assert result["response"]["choices"][0]["message"]["content"] == "recovered"


def test_the_inline_worker_drains_the_queue_by_itself(monkeypatch):
    """With no Redis the API process drains its own queue — see config.INLINE_WORKER."""
    monkeypatch.setattr(config, "INLINE_WORKER", True)
    monkeypatch.setattr(config, "WORKER_POLL_S", 0.05)
    all_providers_down(monkeypatch)

    with TestClient(app) as live:          # `with` is what runs the lifespan
        job_id = live.post(
            "/v1/chat/completions",
            json=BODY,
            headers={**HEADERS, "X-Class": "deferrable"},
        ).json()["job_id"]
        monkeypatch.setattr(providers, "_completion", lambda **kw: FakeResponse("drained"))

        deadline = time.time() + 5
        while time.time() < deadline:
            result = live.get(f"/v1/jobs/{job_id}").json()
            if result["status"] == "done":
                break
            time.sleep(0.05)

    assert result["status"] == "done"
    assert result["response"]["choices"][0]["message"]["content"] == "drained"


def test_unknown_job_is_404():
    assert client.get("/v1/jobs/job-nope").status_code == 404


# --- idempotency -------------------------------------------------------------

def test_a_repeated_idempotency_key_replays_the_first_answer():
    first = post(**{"X-Idempotency-Key": "k-1"})
    second = post(**{"X-Idempotency-Key": "k-1"})
    assert second.headers.get("X-Idempotent-Replay") == "true"
    assert second.json()["id"] == first.json()["id"]


def test_a_repeated_key_cannot_create_a_second_job(monkeypatch):
    all_providers_down(monkeypatch)
    first = post(**{"X-Class": "deferrable", "X-Idempotency-Key": "k-2"})
    second = post(**{"X-Class": "deferrable", "X-Idempotency-Key": "k-2"})
    assert second.status_code == 202
    assert second.json()["job_id"] == first.json()["job_id"]
    assert queue.depth() == 1  # not two


# --- admin -------------------------------------------------------------------

def test_chaos_degrades_one_provider_and_traffic_reroutes():
    r = client.post(
        "/admin/chaos", json={"provider": "groq", "mode": "error", "error_type": "rate_limit"}
    )
    assert r.status_code == 200
    assert post().headers["X-Gateway-Provider"] == "gemini"


def test_chaos_off_restores_the_provider():
    client.post("/admin/chaos", json={"provider": "groq", "mode": "error"})
    client.post("/admin/chaos", json={"provider": "groq", "mode": "off"})
    assert post().headers["X-Gateway-Provider"] == "groq"


def test_chaos_rejects_an_unknown_provider():
    r = client.post("/admin/chaos", json={"provider": "nope"})
    assert r.status_code == 400


def test_admin_status_reports_breakers_and_queue():
    post()
    status = client.get("/admin/status").json()
    assert set(status["providers"]) == set(config.PROVIDERS)
    assert status["providers"]["groq"]["state"] == "closed"
    assert status["queue_depth"] == 0


def test_admin_reset_clears_chaos_and_state(monkeypatch):
    client.post("/admin/chaos", json={"provider": "groq", "mode": "error"})
    all_providers_down(monkeypatch)
    post(**{"X-Class": "deferrable"})
    assert queue.depth() == 1

    assert client.post("/admin/reset").json() == {"status": "reset"}
    status = client.get("/admin/status").json()
    assert status["chaos"] == {}
    assert status["queue_depth"] == 0


def test_admin_requires_the_token_when_one_is_set(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
    assert client.get("/admin/status").status_code == 403
    assert client.get("/admin/status", headers={"X-Admin-Token": "s3cret"}).status_code == 200


# --- live dashboard ----------------------------------------------------------

def test_dashboard_is_served():
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "LLM Gateway" in r.text
    assert "/admin/feed" in r.text          # the page polls the feed


def test_feed_reports_providers_queue_and_requests():
    post()
    feed = client.get("/admin/feed").json()
    assert set(feed["providers"]) == set(config.PROVIDERS)
    assert feed["queue_depth"] == 0
    assert feed["models"]["groq"] == config.PROVIDER_MODELS["groq"]
    assert len(feed["requests"]) == 1
    row = feed["requests"][0]
    assert (row["tenant"], row["feature"], row["status"]) == ("acme", "chat", "ok")
    assert row["attempts"] == [{"provider": "groq", "ok": True, "error_type": None}]


def test_feed_since_returns_only_what_is_new():
    post()
    first = client.get("/admin/feed").json()["requests"]
    seq = first[0]["seq"]
    assert client.get(f"/admin/feed?since={seq}").json()["requests"] == []
    post()
    fresh = client.get(f"/admin/feed?since={seq}").json()["requests"]
    assert len(fresh) == 1 and fresh[0]["seq"] > seq


def test_feed_records_a_failover_trail(monkeypatch):
    monkeypatch.setattr(
        providers, "_completion", by_model({config.PROVIDER_MODELS["groq"]: fails()})
    )
    post()
    row = client.get("/admin/feed").json()["requests"][0]
    assert row["provider"] == "gemini"
    assert row["failovers"] == 1
    assert [a["provider"] for a in row["attempts"]] == ["groq", "gemini"]
    assert row["attempts"][0]["error_type"] == "server_error"


def test_feed_records_failed_and_queued_outcomes(monkeypatch):
    all_providers_down(monkeypatch)
    post()                                  # interactive -> failed
    post(**{"X-Class": "deferrable"})       # deferrable  -> queued
    statuses = [r["status"] for r in client.get("/admin/feed").json()["requests"]]
    assert statuses == ["queued", "failed"]   # newest first


def test_reset_clears_the_feed():
    post()
    client.post("/admin/reset")
    assert client.get("/admin/feed").json()["requests"] == []
