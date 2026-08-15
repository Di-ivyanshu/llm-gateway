"""Deferrable queue, backoff, idempotency, and the worker that drains it."""
import time

import fakeredis
import pytest

from app import config, providers, queue, worker

from tests.helpers import FakeResponse


@pytest.fixture(params=["memory", "redis"])
def backend(request):
    if request.param == "memory":
        queue.set_backend(queue.MemoryBackend())
    else:
        queue.set_backend(queue.RedisBackend(fakeredis.FakeRedis(decode_responses=True)))
    yield request.param
    queue.set_backend(queue.MemoryBackend())


def job(**overrides):
    base = {
        "messages": [{"role": "user", "content": "summarise this"}],
        "tenant": "acme",
        "feature": "nightly-summary",
        "request_id": "r1",
        "request_class": "deferrable",
    }
    base.update(overrides)
    return base


def test_enqueue_then_pop(backend):
    job_id = queue.enqueue(job())
    assert queue.depth() == 1
    popped = queue.pop_due()
    assert popped["id"] == job_id
    assert queue.depth() == 0


def test_a_delayed_job_is_not_due_yet(backend):
    now = time.time()
    queue.enqueue(job(), delay_s=60, now=now)
    assert queue.pop_due(now=now) is None
    assert queue.pop_due(now=now + 61) is not None


def test_backoff_grows_and_is_capped(monkeypatch):
    monkeypatch.setattr(config, "QUEUE_JITTER", 0.0)
    delays = [queue.backoff_delay(n) for n in range(1, 6)]
    assert delays[:4] == [2, 4, 8, 16]           # base 2, doubling
    monkeypatch.setattr(config, "QUEUE_BACKOFF_MAX_S", 10)
    assert queue.backoff_delay(9) == 10          # capped


def test_backoff_jitter_stays_inside_the_band():
    values = {queue.backoff_delay(3) for _ in range(50)}
    assert len(values) > 1                        # actually jittered
    assert all(8 * 0.7 <= v <= 8 * 1.3 for v in values)


def test_retry_reschedules_until_max_attempts(backend, monkeypatch):
    monkeypatch.setattr(config, "QUEUE_MAX_ATTEMPTS", 3)
    j = job()
    queue.enqueue(j)
    j = queue.pop_due()
    assert queue.retry(j) is not None            # attempt 1
    j = queue.pop_due(now=time.time() + 3600)
    assert queue.retry(j) is not None            # attempt 2
    j = queue.pop_due(now=time.time() + 3600)
    assert queue.retry(j) is None                # exhausted
    assert queue.depth() == 0


def test_idempotency_key_can_only_be_claimed_once(backend):
    assert queue.claim("key-1", {"status": "queued", "job_id": "a"}) is True
    assert queue.claim("key-1", {"status": "queued", "job_id": "b"}) is False
    assert queue.lookup("key-1")["job_id"] == "a"


# --- worker ------------------------------------------------------------------

def test_worker_drains_a_job_into_a_result(backend):
    job_id = queue.enqueue(job())
    outcome = worker.run_once()
    assert outcome == {"job_id": job_id, "status": "done", "provider": "openrouter"}
    stored = queue.get_result(job_id)
    assert stored["status"] == "done"
    assert stored["response"]["choices"][0]["message"]["content"] == "hello"


def test_worker_is_a_noop_on_an_empty_queue(backend):
    assert worker.run_once() is None


def test_worker_retries_while_every_provider_is_down(backend, monkeypatch):
    monkeypatch.setattr(
        providers, "_completion",
        lambda **kw: (_ for _ in ()).throw(type("InternalServerError", (Exception,), {})("x")),
    )
    job_id = queue.enqueue(job())
    outcome = worker.run_once()
    assert outcome["status"] == "retrying"
    assert queue.get_result(job_id)["status"] == "retrying"
    assert queue.depth() == 1                     # still parked, not lost


def test_worker_gives_up_after_max_attempts(backend, monkeypatch):
    monkeypatch.setattr(config, "QUEUE_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(config, "QUEUE_BACKOFF_BASE_S", 0)
    monkeypatch.setattr(
        providers, "_completion",
        lambda **kw: (_ for _ in ()).throw(type("InternalServerError", (Exception,), {})("x")),
    )
    job_id = queue.enqueue(job())
    for _ in range(5):
        if worker.run_once() is None:
            break
    assert queue.get_result(job_id)["status"] == "failed"


def test_a_drained_job_also_answers_its_idempotency_key(backend, monkeypatch):
    monkeypatch.setattr(providers, "_completion", lambda **kw: FakeResponse("done later"))
    queue.enqueue(job(idempotency_key="idem-9"))
    worker.run_once()
    replay = queue.lookup("idem-9")
    assert replay["status"] == "done"
    assert replay["response"]["choices"][0]["message"]["content"] == "done later"
