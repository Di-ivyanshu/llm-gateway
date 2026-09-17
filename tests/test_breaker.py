"""Circuit breaker — trip, cool down, probe, heal."""
import time

import pytest

from app import breaker, config, health


def _fail(provider="groq", n=5, error_type="server_error", now=None):
    for _ in range(n):
        health.record(provider, False, 100, error_type, now=now)
        breaker.on_failure(provider, error_type, now=now)


def test_starts_closed():
    assert breaker.state("groq") == breaker.CLOSED
    assert breaker.allow("groq")


def test_one_bad_call_does_not_trip_it():
    _fail(n=1)
    assert breaker.state("groq") == breaker.CLOSED  # below BREAKER_MIN_SAMPLES


def test_error_rate_over_threshold_opens_it():
    _fail(n=config.BREAKER_MIN_SAMPLES)
    assert breaker.state("groq") == breaker.OPEN
    assert not breaker.allow("groq")


def test_slow_but_successful_also_opens_it(monkeypatch):
    monkeypatch.setattr(config, "BREAKER_P95_BUDGET_MS", 500)
    for _ in range(config.BREAKER_MIN_SAMPLES):
        health.record("gemini", True, 5000)
        breaker.on_success("gemini")
    assert breaker.state("gemini") == breaker.OPEN


def test_auth_errors_open_immediately():
    health.record("groq", False, 10, "auth")
    breaker.on_failure("groq", "auth")  # retrying a bad key never helps
    assert breaker.state("groq") == breaker.OPEN


def test_open_becomes_half_open_after_the_cooldown():
    now = time.time()
    _fail(n=config.BREAKER_MIN_SAMPLES, now=now)
    assert breaker.state("groq", now=now) == breaker.OPEN
    later = now + config.BREAKER_COOLDOWN_S + 1
    assert breaker.state("groq", now=later) == breaker.HALF_OPEN


def test_half_open_admits_only_a_bounded_number_of_probes():
    now = time.time()
    _fail(n=config.BREAKER_MIN_SAMPLES, now=now)
    later = now + config.BREAKER_COOLDOWN_S + 1
    allowed = [breaker.allow("groq", now=later) for _ in range(5)]
    assert allowed.count(True) == config.BREAKER_HALF_OPEN_PROBES
    assert allowed.count(False) == 5 - config.BREAKER_HALF_OPEN_PROBES


def test_a_successful_probe_closes_it_and_clears_the_window():
    now = time.time()
    _fail(n=config.BREAKER_MIN_SAMPLES, now=now)
    later = now + config.BREAKER_COOLDOWN_S + 1
    assert breaker.allow("groq", now=later)
    breaker.on_success("groq", now=later)
    assert breaker.state("groq", now=later) == breaker.CLOSED
    # the old failures must be forgotten, or it would re-open on the next call
    assert health.stats("groq", now=later)["count"] == 0


def test_a_failed_probe_re_opens_it():
    now = time.time()
    _fail(n=config.BREAKER_MIN_SAMPLES, now=now)
    later = now + config.BREAKER_COOLDOWN_S + 1
    assert breaker.allow("groq", now=later)
    breaker.on_failure("groq", "server_error", now=later)
    assert breaker.state("groq", now=later) == breaker.OPEN
    # ...and the cooldown restarts from the failed probe
    assert breaker.state("groq", now=later + config.BREAKER_COOLDOWN_S - 1) == breaker.OPEN


def test_snapshot_reports_every_provider():
    snap = breaker.snapshot()
    assert set(snap) == set(config.PROVIDERS)
    assert snap["groq"]["state"] == breaker.CLOSED


@pytest.mark.parametrize("provider", ["groq", "gemini", "openrouter"])
def test_breakers_are_independent(provider):
    _fail(provider=provider, n=config.BREAKER_MIN_SAMPLES)
    others = [p for p in config.PROVIDERS if p != provider]
    assert breaker.state(provider) == breaker.OPEN
    assert all(breaker.state(other) == breaker.CLOSED for other in others)


def test_an_unused_probe_token_is_given_back():
    """A request answered by someone else must not eat a half-open probe token.

    Regression: `candidates()` asks every provider on the preference list, so a
    deferrable request (openrouter → gemini → groq) took groq's only probe token
    and then never called it. Nobody probed groq again, so it sat in half_open
    forever — neither healing nor re-opening.
    """
    now = time.time()
    _fail(n=config.BREAKER_MIN_SAMPLES, now=now)
    later = now + config.BREAKER_COOLDOWN_S + 1
    assert breaker.state("groq", now=later) == breaker.HALF_OPEN

    assert breaker.allow("groq", now=later) is True      # token taken...
    assert breaker.allow("groq", now=later) is False     # ...and it was the only one
    breaker.release("groq")                              # never actually called
    assert breaker.allow("groq", now=later) is True      # so the next request can probe


def test_release_is_a_noop_for_a_closed_breaker():
    breaker.release("gemini")
    assert breaker.state("gemini") == breaker.CLOSED
    assert breaker.allow("gemini")
