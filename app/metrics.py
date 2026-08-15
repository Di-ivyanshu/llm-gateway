"""Prometheus metrics — the numbers Grafana draws.

Everything the dashboard needs is defined here and nowhere else. Label sets are
kept deliberately small (provider / status / type, plus tenant+feature on the
cost and volume series) — every extra label multiplies the time series count.
"""
from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# --- traffic & reliability ---------------------------------------------------
REQUESTS = Counter(
    "gateway_requests_total", "Provider calls attempted", ["provider", "status"]
)
ERRORS = Counter(
    "gateway_errors_total", "Provider call failures by error type", ["provider", "type"]
)
LATENCY = Histogram(
    "gateway_latency_seconds",
    "Provider call latency",
    ["provider"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34),
)
PROVIDER_UP = Gauge(
    "gateway_provider_up", "1 when the provider's breaker is closed", ["provider"]
)
BREAKER_STATE = Gauge(
    "gateway_breaker_state", "0=closed 1=half_open 2=open", ["provider"]
)
SUCCESS_RATE = Gauge(
    "gateway_success_rate", "Success rate over the health window", ["provider"]
)
FAILOVERS = Counter(
    "gateway_failover_total", "Requests re-routed to another provider",
    ["from_provider", "to_provider"],
)
HEDGES = Counter("gateway_hedge_total", "Hedged second calls fired", ["provider"])
HEDGE_WINS = Counter("gateway_hedge_win_total", "Hedge returned first", ["provider"])

# --- client-facing outcome ---------------------------------------------------
CLIENT_REQUESTS = Counter(
    "gateway_client_requests_total",
    "Requests as the caller sees them",
    ["tenant", "feature", "status"],
)

# --- queue (Phase 4) ---------------------------------------------------------
QUEUE_DEPTH = Gauge("gateway_queue_depth", "Deferrable jobs waiting")
QUEUED = Counter("gateway_queued_total", "Jobs enqueued", ["tenant", "feature"])
QUEUE_DONE = Counter("gateway_queue_completed_total", "Jobs drained", ["status"])
QUEUE_RETRIES = Counter("gateway_queue_retries_total", "Job retries scheduled")
IDEMPOTENT_HITS = Counter(
    "gateway_idempotent_hits_total", "Requests served from the idempotency cache"
)

# --- cost attribution (Phase 5) ---------------------------------------------
TOKENS = Counter(
    "gateway_tokens_total", "Tokens used", ["provider", "tenant", "feature", "kind"]
)
COST = Counter(
    "gateway_cost_usd_total", "Estimated spend in USD",
    ["tenant", "feature", "provider"],
)


def observe_call(provider: str, ok: bool, latency_ms: int, error_type: str | None) -> None:
    """One provider call finished."""
    REQUESTS.labels(provider=provider, status="ok" if ok else "error").inc()
    LATENCY.labels(provider=provider).observe(latency_ms / 1000)
    if not ok:
        ERRORS.labels(provider=provider, type=error_type or "unknown").inc()


def render() -> tuple[bytes, str]:
    """Body + content-type for the `/metrics` endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
