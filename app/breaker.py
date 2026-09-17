"""Circuit breaker — one per provider, driven by the health window.

    closed ──(error rate or p95 over budget)──▶ open
      ▲                                          │
      │                                    (cooldown elapsed)
      │                                          ▼
      └────────(probe succeeds)────────── half_open ──(probe fails)──▶ open

Two details that are easy to get wrong and matter here:

* **half_open exists.** Without it a breaker opens once and never heals. A
  bounded number of probes (`BREAKER_HALF_OPEN_PROBES`) is let through; everyone
  else is still routed elsewhere.
* **Closing resets the window.** Otherwise the failures that opened the breaker
  are still inside the sliding window and it re-opens on the very next call.

State is per process. That is the right scope for a demo and for one instance;
with several gateway instances you would move this dict into Redis (the health
window already lives there).
"""
from __future__ import annotations

import threading
import time
from typing import Any

from . import config, health, metrics

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"
_NUMERIC = {CLOSED: 0, HALF_OPEN: 1, OPEN: 2}

_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {}


def _entry(provider: str) -> dict[str, Any]:
    return _state.setdefault(
        provider, {"state": CLOSED, "opened_at": 0.0, "probes": 0, "trips": 0}
    )


def _open(entry: dict[str, Any], now: float) -> None:
    entry["state"] = OPEN
    entry["opened_at"] = now
    entry["probes"] = 0
    entry["trips"] += 1


def state(provider: str, *, now: float | None = None) -> str:
    """Current state, moving `open → half_open` once the cooldown has elapsed."""
    now = time.time() if now is None else now
    with _lock:
        entry = _entry(provider)
        if entry["state"] == OPEN and now - entry["opened_at"] >= config.BREAKER_COOLDOWN_S:
            entry["state"] = HALF_OPEN
            entry["probes"] = 0
        return entry["state"]


def allow(provider: str, *, now: float | None = None) -> bool:
    """May this request use the provider?

    closed → yes. open → no. half_open → yes for a bounded number of probes.
    """
    current = state(provider, now=now)
    if current == CLOSED:
        return True
    if current == OPEN:
        return False
    with _lock:
        entry = _entry(provider)
        if entry["probes"] < config.BREAKER_HALF_OPEN_PROBES:
            entry["probes"] += 1
            return True
        return False


def release(provider: str) -> None:
    """Give a half-open probe token back, unused.

    `allow()` is asked about every provider in a preference list, but only the
    ones actually called consume a probe. Without this, a request that was
    answered by an earlier provider would quietly eat a later provider's only
    probe token — and a half-open breaker with nobody probing it never heals and
    never re-opens. It just sits there.
    """
    with _lock:
        entry = _entry(provider)
        if entry["state"] == HALF_OPEN:
            entry["probes"] = max(0, entry["probes"] - 1)


def _should_trip(provider: str, now: float) -> bool:
    stats = health.stats(provider, now=now)
    if stats["count"] < config.BREAKER_MIN_SAMPLES:
        return False  # too little evidence to condemn a provider
    return (
        stats["error_rate"] > config.BREAKER_ERROR_RATE
        or stats["p95"] > config.BREAKER_P95_BUDGET_MS
    )


def on_success(provider: str, *, now: float | None = None) -> None:
    """A call succeeded — close a half-open breaker, or re-check a closed one."""
    now = time.time() if now is None else now
    with _lock:
        entry = _entry(provider)
        was_half_open = entry["state"] == HALF_OPEN
        if was_half_open:
            entry.update(state=CLOSED, opened_at=0.0, probes=0)
    if was_half_open:
        # Forget the outage: stale failures in the window would re-trip us at once.
        health.clear(provider)
        return
    if _should_trip(provider, now):  # slow-but-successful still counts as broken
        with _lock:
            _open(_entry(provider), now)


def on_failure(provider: str, error_type: str, *, now: float | None = None) -> None:
    """A call failed — a failed probe re-opens immediately, otherwise re-evaluate."""
    now = time.time() if now is None else now
    with _lock:
        entry = _entry(provider)
        if entry["state"] == HALF_OPEN:
            _open(entry, now)
            return
    if error_type == "auth" or _should_trip(provider, now):
        # auth failures never fix themselves by retrying — open at once.
        with _lock:
            _open(_entry(provider), now)


def snapshot(*, now: float | None = None) -> dict[str, dict[str, Any]]:
    """Per-provider state + health, for `/admin/status` and the gauges."""
    now = time.time() if now is None else now
    out: dict[str, dict[str, Any]] = {}
    for provider in config.PROVIDERS:
        current = state(provider, now=now)
        stats = health.stats(provider, now=now)
        out[provider] = {
            "state": current,
            "trips": _entry(provider)["trips"],
            "opened_at": _entry(provider)["opened_at"],
            **{k: stats[k] for k in ("count", "success_rate", "p50", "p95", "p99", "error_counts")},
        }
    return out


def export_gauges(*, now: float | None = None) -> None:
    """Push breaker + health state into Prometheus (called before a scrape)."""
    for provider, info in snapshot(now=now).items():
        metrics.BREAKER_STATE.labels(provider=provider).set(_NUMERIC[info["state"]])
        metrics.PROVIDER_UP.labels(provider=provider).set(
            1 if info["state"] != OPEN else 0
        )
        metrics.SUCCESS_RATE.labels(provider=provider).set(info["success_rate"])


def reset(provider: str | None = None) -> None:
    """Forget breaker state (tests, and `/admin/chaos` cleanup)."""
    with _lock:
        if provider is None:
            _state.clear()
        else:
            _state.pop(provider, None)
