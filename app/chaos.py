"""Chaos injection — degrade a provider on demand, from `POST /admin/chaos`.

This is what makes the demo (and the availability benchmark) reproducible: you
can break Groq for 30 seconds without touching Groq. Faults are applied inside
`providers.call`, i.e. exactly where a real provider fault would surface, so the
breaker and the failover path see nothing artificial.
"""
from __future__ import annotations

import random
import time
from typing import Any

# Error labels → an exception class name `errors.classify` already understands.
_ERROR_CLASSES = {
    "rate_limit": "RateLimitError",
    "timeout": "APITimeoutError",
    "auth": "AuthenticationError",
    "content_filter": "ContentPolicyViolationError",
    "server_error": "InternalServerError",
    "unknown": "ChaosError",
}

_state: dict[str, dict[str, Any]] = {}


def inject(
    provider: str,
    *,
    mode: str = "error",
    error_type: str = "server_error",
    latency_ms: int = 0,
    rate: float = 1.0,
) -> dict[str, Any]:
    """Start degrading `provider`. mode: "error" | "latency" | "off"."""
    if mode == "off":
        clear(provider)
        return {"provider": provider, "mode": "off"}
    fault = {
        "provider": provider,
        "mode": mode,
        "error_type": error_type,
        "latency_ms": int(latency_ms),
        "rate": max(0.0, min(1.0, float(rate))),
    }
    _state[provider] = fault
    return fault


def clear(provider: str | None = None) -> None:
    if provider is None:
        _state.clear()
    else:
        _state.pop(provider, None)


def snapshot() -> dict[str, dict[str, Any]]:
    return {p: dict(f) for p, f in _state.items()}


def apply(provider: str) -> None:
    """Raise or stall if this provider is currently being degraded."""
    fault = _state.get(provider)
    if not fault or random.random() >= fault["rate"]:
        return
    if fault["mode"] == "latency":
        time.sleep(fault["latency_ms"] / 1000)
        return
    name = _ERROR_CLASSES.get(fault["error_type"], "ChaosError")
    raise type(name, (Exception,), {})(
        f"chaos: injected {fault['error_type']} on {provider}"
    )
