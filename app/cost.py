"""Cost attribution — the reason a gateway exists.

Every completion is priced from `config.MODEL_PRICES` (USD per 1M tokens) and
booked against the tenant + feature that the caller declared in headers. The
numbers land in Prometheus, so Grafana can show "$/hour by tenant" without any
extra plumbing.

Free tiers price at 0, which is honest: the point of the panel is to show WHO
would be spending, and it starts costing the moment you leave the free tier.
"""
from __future__ import annotations

from . import config, metrics


def price(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens for a LiteLLM model id."""
    if model in config.MODEL_PRICES:
        return config.MODEL_PRICES[model]
    # tolerate an id written without its provider prefix, and vice versa
    tail = model.split("/", 1)[-1]
    for known, prices in config.MODEL_PRICES.items():
        if known.split("/", 1)[-1] == tail:
            return prices
    return config.DEFAULT_PRICE


def estimate(model: str, usage: dict[str, int]) -> float:
    """USD for one completion."""
    per_in, per_out = price(model)
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return (prompt * per_in + completion * per_out) / 1_000_000


def record(
    tenant: str, feature: str, provider: str, model: str, usage: dict[str, int]
) -> float:
    """Book tokens and USD against a tenant/feature. Returns the USD amount."""
    usd = estimate(model, usage)
    metrics.TOKENS.labels(
        provider=provider, tenant=tenant, feature=feature, kind="prompt"
    ).inc(usage.get("prompt_tokens", 0))
    metrics.TOKENS.labels(
        provider=provider, tenant=tenant, feature=feature, kind="completion"
    ).inc(usage.get("completion_tokens", 0))
    metrics.COST.labels(tenant=tenant, feature=feature, provider=provider).inc(usd)
    return usd
