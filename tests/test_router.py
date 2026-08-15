"""Routing: preference order, failover, hedging, and the all-down case."""
import time

import pytest

from app import breaker, config, health, providers, router

from tests.helpers import FakeResponse, always_failing, by_model, fails

MESSAGES = [{"role": "user", "content": "hi"}]


def test_interactive_uses_the_first_preferred_provider():
    out = router.route(MESSAGES, request_class="interactive", tenant="t", feature="f")
    assert out["provider"] == "groq"
    assert out["failovers"] == 0
    assert out["cost_usd"] >= 0


def test_deferrable_prefers_openrouter():
    out = router.route(MESSAGES, request_class="deferrable", tenant="t", feature="f")
    assert out["provider"] == "openrouter"


def test_failover_to_the_next_provider(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_completion",
        by_model({config.PROVIDER_MODELS["groq"]: fails()}),
    )
    out = router.route(MESSAGES, tenant="t", feature="f")
    assert out["provider"] == "gemini"
    assert out["failovers"] == 1
    assert [a["provider"] for a in out["attempts"]] == ["groq", "gemini"]
    assert out["attempts"][0]["error_type"] == "server_error"


def test_a_failed_call_is_written_to_the_health_window(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_completion",
        by_model({config.PROVIDER_MODELS["groq"]: fails("RateLimitError")}),
    )
    router.route(MESSAGES, tenant="t", feature="f")
    assert health.stats("groq")["error_counts"] == {"rate_limit": 1}
    assert health.stats("gemini")["success_rate"] == 1.0


def test_open_breakers_are_skipped():
    for _ in range(config.BREAKER_MIN_SAMPLES):
        health.record("groq", False, 10, "server_error")
        breaker.on_failure("groq", "server_error")
    assert router.candidates("interactive") == ["gemini", "openrouter"]
    assert router.route(MESSAGES, tenant="t", feature="f")["provider"] == "gemini"


def test_all_breakers_open_raises_all_open():
    for provider in config.PROVIDERS:
        for _ in range(config.BREAKER_MIN_SAMPLES):
            health.record(provider, False, 10, "server_error")
            breaker.on_failure(provider, "server_error")
    with pytest.raises(router.NoProviderAvailable) as exc:
        router.route(MESSAGES, tenant="t", feature="f")
    assert exc.value.reason == "all_open"
    assert exc.value.attempts == []  # nothing was even tried


def test_every_provider_failing_raises_all_failed(monkeypatch):
    monkeypatch.setattr(providers, "_completion", always_failing())
    with pytest.raises(router.NoProviderAvailable) as exc:
        router.route(MESSAGES, tenant="t", feature="f")
    assert exc.value.reason == "all_failed"
    assert len(exc.value.attempts) == len(config.PREFERENCE["interactive"])


def test_hedging_takes_whichever_answers_first(monkeypatch):
    monkeypatch.setattr(config, "HEDGE_MS", 50)

    def slow():
        time.sleep(0.4)
        return FakeResponse("slow")

    monkeypatch.setattr(
        providers,
        "_completion",
        by_model({config.PROVIDER_MODELS["groq"]: slow}),
    )
    out = router.route(MESSAGES, request_class="interactive", tenant="t", feature="f")
    assert out["provider"] == "gemini"  # the hedge won
    assert out["hedged"] is True


def test_hedging_is_off_for_deferrable_work(monkeypatch):
    monkeypatch.setattr(config, "HEDGE_MS", 50)
    out = router.route(MESSAGES, request_class="deferrable", tenant="t", feature="f")
    assert out["hedged"] is False  # nobody is waiting; don't pay twice


def test_no_hedge_when_the_primary_is_fast(monkeypatch):
    monkeypatch.setattr(config, "HEDGE_MS", 500)
    out = router.route(MESSAGES, request_class="interactive", tenant="t", feature="f")
    assert out["provider"] == "groq"
    assert len(out["attempts"]) == 1
