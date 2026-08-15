"""Per-provider health, as a sliding window of recent call outcomes.

Backed by a Redis **sorted set** per provider (score = unix timestamp), so the
window survives a gateway restart and is shared if you ever run more than one
instance. With no `REDIS_URL` configured it falls back to an in-process store —
same API, so everything above this module (and the whole test suite) works
without a Redis at all.

The breaker (Phase 3) reads `stats()` and nothing else.
"""
from __future__ import annotations

import math
import time
import uuid
from typing import Any, Protocol

from . import config

_SEP = "|"


def _encode(ok: bool, latency_ms: int, error_type: str | None) -> str:
    # uuid prefix keeps members unique — a sorted set would otherwise dedupe
    # two identical outcomes recorded in the same millisecond.
    return _SEP.join([uuid.uuid4().hex[:8], "1" if ok else "0", str(int(latency_ms)), error_type or ""])


def _decode(member: str) -> tuple[bool, int, str | None]:
    _, ok, latency, error_type = member.split(_SEP, 3)
    return ok == "1", int(latency), error_type or None


class _Backend(Protocol):
    def add(self, provider: str, ts: float, member: str, window_s: float) -> None: ...
    def members(self, provider: str, since: float) -> list[str]: ...
    def clear(self, provider: str | None = None) -> None: ...


class MemoryBackend:
    """In-process fallback — fine for a single instance and for tests."""

    def __init__(self) -> None:
        self._data: dict[str, list[tuple[float, str]]] = {}

    def add(self, provider: str, ts: float, member: str, window_s: float) -> None:
        rows = self._data.setdefault(provider, [])
        rows.append((ts, member))
        cutoff = ts - window_s
        if len(rows) > 64:  # amortise the trim
            self._data[provider] = [r for r in rows if r[0] >= cutoff]

    def members(self, provider: str, since: float) -> list[str]:
        return [m for ts, m in self._data.get(provider, []) if ts >= since]

    def clear(self, provider: str | None = None) -> None:
        if provider is None:
            self._data.clear()
        else:
            self._data.pop(provider, None)


class RedisBackend:
    """Sorted set per provider: `gw:health:<provider>`, score = unix seconds."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def _key(provider: str) -> str:
        return f"gw:health:{provider}"

    def add(self, provider: str, ts: float, member: str, window_s: float) -> None:
        key = self._key(provider)
        pipe = self.client.pipeline()
        pipe.zadd(key, {member: ts})
        pipe.zremrangebyscore(key, 0, ts - window_s)  # trim what fell out of the window
        pipe.expire(key, int(window_s * 2) + 60)
        pipe.execute()

    def members(self, provider: str, since: float) -> list[str]:
        raw = self.client.zrangebyscore(self._key(provider), since, "+inf")
        return [m.decode() if isinstance(m, bytes) else m for m in raw]

    def clear(self, provider: str | None = None) -> None:
        keys = [self._key(provider)] if provider else [
            self._key(p) for p in config.PROVIDERS
        ]
        for key in keys:
            self.client.delete(key)


_backend: _Backend | None = None


def backend() -> _Backend:
    """The active backend — Redis when `REDIS_URL` is set, memory otherwise."""
    global _backend
    if _backend is None:
        if config.REDIS_URL:
            import redis  # imported lazily: no Redis needed for tests

            _backend = RedisBackend(redis.from_url(config.REDIS_URL, decode_responses=True))
        else:
            _backend = MemoryBackend()
    return _backend


def set_backend(new: _Backend | None) -> None:
    """Swap the backend (tests use this to inject fakeredis / a fresh memory store)."""
    global _backend
    _backend = new


def record(
    provider: str,
    ok: bool,
    latency_ms: int,
    error_type: str | None = None,
    *,
    now: float | None = None,
) -> None:
    """Append one call outcome to the provider's window."""
    ts = time.time() if now is None else now
    backend().add(provider, ts, _encode(ok, latency_ms, error_type), config.HEALTH_WINDOW_S)


def _percentile(sorted_values: list[int], pct: float) -> float:
    """Nearest-rank percentile. Empty → 0.0."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return float(sorted_values[rank - 1])


def stats(provider: str, *, now: float | None = None) -> dict[str, Any]:
    """Summarise the provider's window.

    Returns count / success_rate / error_rate / p50 / p95 / p99 (ms) /
    error_counts. An empty window reads as perfectly healthy (success_rate 1.0)
    so a cold start never trips the breaker.
    """
    ts = time.time() if now is None else now
    rows = [_decode(m) for m in backend().members(provider, ts - config.HEALTH_WINDOW_S)]
    count = len(rows)
    if not count:
        return {
            "provider": provider, "count": 0, "success_rate": 1.0, "error_rate": 0.0,
            "p50": 0.0, "p95": 0.0, "p99": 0.0, "error_counts": {},
        }

    ok_count = sum(1 for ok, _, _ in rows if ok)
    latencies = sorted(latency for _, latency, _ in rows)
    error_counts: dict[str, int] = {}
    for ok, _, error_type in rows:
        if not ok:
            key = error_type or "unknown"
            error_counts[key] = error_counts.get(key, 0) + 1

    return {
        "provider": provider,
        "count": count,
        "success_rate": ok_count / count,
        "error_rate": 1 - ok_count / count,
        "p50": _percentile(latencies, 50),
        "p95": _percentile(latencies, 95),
        "p99": _percentile(latencies, 99),
        "error_counts": error_counts,
    }


def all_stats(*, now: float | None = None) -> dict[str, dict[str, Any]]:
    return {p: stats(p, now=now) for p in config.PROVIDERS}


def clear(provider: str | None = None) -> None:
    backend().clear(provider)
