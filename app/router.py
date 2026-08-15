"""Routing — pick a provider, fail over, optionally hedge.

The rules, in order:

1. Take the preference list for the request class and drop every provider whose
   breaker won't let traffic through.
2. (interactive only, and only if `HEDGE_MS > 0`) start the first provider; if it
   hasn't answered within `HEDGE_MS`, start the second one too and keep whichever
   answers first. Costs ~2x on the hedged calls — that is the trade.
3. Otherwise walk the list: first success wins, every move to the next provider
   is a recorded failover event.

Every attempt — win or lose — is written to the health window and to Prometheus,
which is what lets the breaker make its next decision.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from typing import Any

from . import breaker, config, cost, health, metrics, providers


class NoProviderAvailable(RuntimeError):
    """Nothing served the request.

    `reason` is "all_open" (every breaker was open, nothing was tried) or
    "all_failed" (everything we tried errored).
    """

    def __init__(self, reason: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(f"no provider available ({reason})")
        self.reason = reason
        self.attempts = attempts


def candidates(request_class: str) -> list[str]:
    """Preference list for the class, minus providers whose breaker is open."""
    preferred = config.PREFERENCE.get(request_class, config.PREFERENCE[config.DEFAULT_CLASS])
    return [p for p in preferred if breaker.allow(p)]


def _attempt(provider: str, messages: list[dict[str, str]], call_kwargs: dict[str, Any]):
    """One provider call, fully instrumented. Returns (result, error)."""
    started = time.perf_counter()
    try:
        result = providers.call(provider, messages, **call_kwargs)
    except providers.ProviderError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        health.record(provider, False, latency_ms, exc.error_type)
        metrics.observe_call(provider, False, latency_ms, exc.error_type)
        breaker.on_failure(provider, exc.error_type)
        return None, {
            "provider": provider, "ok": False,
            "error_type": exc.error_type, "latency_ms": latency_ms,
        }
    health.record(provider, True, result["latency_ms"])
    metrics.observe_call(provider, True, result["latency_ms"], None)
    breaker.on_success(provider)
    return result, {
        "provider": provider, "ok": True, "error_type": None,
        "latency_ms": result["latency_ms"],
    }


def _hedge(primary: str, backup: str, messages, call_kwargs):
    """Race `backup` against a slow `primary`. Returns (result, attempts)."""
    attempts: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_attempt, primary, messages, call_kwargs)
        wait([first], timeout=config.HEDGE_MS / 1000)
        if first.done():
            result, record = first.result()
            attempts.append(record)
            return result, attempts  # answered inside the hedge window

        metrics.HEDGES.labels(provider=backup).inc()
        second = pool.submit(_attempt, backup, messages, call_kwargs)
        winner = None
        for future in as_completed([first, second]):
            result, record = future.result()
            attempts.append(record)
            if result is not None and winner is None:
                winner = result
                if result["provider"] == backup:
                    metrics.HEDGE_WINS.labels(provider=backup).inc()
                break  # the loser keeps running; we just stop waiting on it
        return winner, attempts


def route(
    messages: list[dict[str, str]],
    *,
    request_class: str = config.DEFAULT_CLASS,
    tenant: str = "unknown",
    feature: str = "unknown",
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Serve one completion. Raises `NoProviderAvailable` if nothing worked."""
    order = candidates(request_class)
    attempts: list[dict[str, Any]] = []
    if not order:
        raise NoProviderAvailable("all_open", attempts)

    call_kwargs = {"temperature": temperature, "max_tokens": max_tokens}
    start_index = 0
    hedged = False

    if request_class == "interactive" and config.HEDGE_MS > 0 and len(order) >= 2:
        hedged = True
        result, hedge_attempts = _hedge(order[0], order[1], messages, call_kwargs)
        attempts.extend(hedge_attempts)
        if result is not None:
            return _finish(result, attempts, hedged, tenant, feature)
        start_index = len(hedge_attempts)  # 1 if the primary failed fast, else 2

    for index in range(start_index, len(order)):
        provider = order[index]
        if attempts:
            metrics.FAILOVERS.labels(
                from_provider=attempts[-1]["provider"], to_provider=provider
            ).inc()
        result, record = _attempt(provider, messages, call_kwargs)
        attempts.append(record)
        if result is not None:
            return _finish(result, attempts, hedged, tenant, feature)

    raise NoProviderAvailable("all_failed", attempts)


def _finish(
    result: dict[str, Any],
    attempts: list[dict[str, Any]],
    hedged: bool,
    tenant: str,
    feature: str,
) -> dict[str, Any]:
    """Attach cost attribution and the routing trail to a successful call."""
    usd = cost.record(tenant, feature, result["provider"], result["model"], result["usage"])
    return {
        **result,
        "cost_usd": usd,
        "hedged": hedged,
        "attempts": attempts,
        "failovers": max(0, len(attempts) - 1),
    }
