"""Health sliding window — same assertions against both backends."""
import time

import fakeredis
import pytest

from app import config, health


@pytest.fixture(params=["memory", "redis"])
def backend(request):
    if request.param == "memory":
        health.set_backend(health.MemoryBackend())
    else:
        health.set_backend(health.RedisBackend(fakeredis.FakeRedis(decode_responses=True)))
    yield request.param
    health.set_backend(health.MemoryBackend())


def test_empty_window_reads_as_healthy(backend):
    stats = health.stats("groq")
    assert stats["count"] == 0
    assert stats["success_rate"] == 1.0  # a cold start must not trip the breaker
    assert stats["p95"] == 0.0


def test_success_rate_and_error_counts(backend):
    for _ in range(3):
        health.record("groq", True, 100)
    health.record("groq", False, 50, "rate_limit")
    stats = health.stats("groq")
    assert stats["count"] == 4
    assert stats["success_rate"] == 0.75
    assert stats["error_rate"] == 0.25
    assert stats["error_counts"] == {"rate_limit": 1}


def test_percentiles_are_nearest_rank(backend):
    for latency in range(100, 1100, 100):  # 100..1000, ten samples
        health.record("gemini", True, latency)
    stats = health.stats("gemini")
    assert stats["p50"] == 500
    assert stats["p95"] == 1000
    assert stats["p99"] == 1000


def test_old_entries_fall_out_of_the_window(backend):
    now = time.time()
    health.record("groq", False, 10, "server_error", now=now - config.HEALTH_WINDOW_S - 60)
    health.record("groq", True, 10, now=now)
    stats = health.stats("groq", now=now)
    assert stats["count"] == 1
    assert stats["success_rate"] == 1.0


def test_providers_are_tracked_separately(backend):
    health.record("groq", False, 10, "timeout")
    health.record("gemini", True, 10)
    assert health.stats("groq")["success_rate"] == 0.0
    assert health.stats("gemini")["success_rate"] == 1.0
    assert set(health.all_stats()) == set(config.PROVIDERS)


def test_clear_forgets_a_provider(backend):
    health.record("groq", False, 10, "timeout")
    health.clear("groq")
    assert health.stats("groq")["count"] == 0
