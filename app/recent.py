"""A short in-memory log of the last few requests, for the live dashboard.

Prometheus keeps counters, not events: it can tell you the error *rate*, never
"which request just failed over to Gemini". A ring buffer of the last few hundred
outcomes covers that, costs nothing, and is deliberately in-process and lossy —
it is a window onto right now, not an audit trail. (Persisting it is what the
Redis health window and a real log pipeline are for.)
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

MAX_EVENTS = 500

_lock = threading.Lock()
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_counter = 0


def record(
    *,
    tenant: str,
    feature: str,
    request_class: str,
    status: str,
    provider: str | None = None,
    latency_ms: int = 0,
    failovers: int = 0,
    cost_usd: float = 0.0,
    attempts: list[dict[str, Any]] | None = None,
    error_type: str | None = None,
    job_id: str | None = None,
) -> None:
    """Append one finished request. `status` is ok / failed / queued / replayed."""
    global _counter
    with _lock:
        _counter += 1
        _events.append({
            "seq": _counter,
            "ts": time.time(),
            "tenant": tenant,
            "feature": feature,
            "class": request_class,
            "status": status,
            "provider": provider,
            "latency_ms": latency_ms,
            "failovers": failovers,
            "cost_usd": round(cost_usd, 8),
            # just the trail: [{provider, ok, error_type}] — enough to render
            # "groq(server_error) → gemini" without shipping the whole payload
            "attempts": [
                {"provider": a["provider"], "ok": a["ok"], "error_type": a["error_type"]}
                for a in (attempts or [])
            ],
            "error_type": error_type,
            "job_id": job_id,
        })


def events(limit: int = MAX_EVENTS, since_seq: int = 0) -> list[dict[str, Any]]:
    """Most recent first. `since_seq` lets a poller ask only for what's new."""
    with _lock:
        rows = [e for e in _events if e["seq"] > since_seq]
    return list(reversed(rows))[:limit]


def clear() -> None:
    global _counter
    with _lock:
        _events.clear()
        _counter = 0
