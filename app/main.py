"""LLM Gateway — FastAPI entrypoint.

Everything a caller can reach:

    GET  /health                 liveness
    GET  /metrics                Prometheus scrape target
    POST /v1/chat/completions    OpenAI-compatible; routed, failed over, priced
    GET  /v1/jobs/{job_id}       result of a deferred job
    GET  /admin/status           breaker + health + queue, as JSON
    POST /admin/chaos            degrade a provider on purpose (the demo)
    POST /admin/reset            clear breakers, health window and queue

Every request MUST carry `X-Tenant`, `X-Feature` and `X-Request-Id`. Without
them there is no cost attribution, and cost attribution is the reason to put a
gateway in front of your LLM calls in the first place — so it is a 400, not a
default.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import breaker, chaos, config, health, metrics, queue, router
from .shapes import completion_body

app = FastAPI(title="llm-gateway", version="0.5.0")
log = logging.getLogger("gateway")


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "gateway-auto"
    messages: list[Message]
    temperature: float = 0.2
    max_tokens: int | None = None


class ChaosRequest(BaseModel):
    provider: str
    mode: str = "error"           # "error" | "latency" | "off"
    error_type: str = "server_error"
    latency_ms: int = 0
    rate: float = 1.0


# --- helpers -----------------------------------------------------------------

def _error(status: int, message: str, err_type: str) -> JSONResponse:
    """OpenAI-shaped error body, so existing clients parse it unchanged."""
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "code": err_type}},
    )


def _admin_ok(token: str | None) -> bool:
    return not config.ADMIN_TOKEN or token == config.ADMIN_TOKEN


# --- public endpoints --------------------------------------------------------

@app.get("/health")
def liveness() -> dict:
    """Liveness probe — deliberately says nothing about providers."""
    return {"status": "ok"}


@app.get("/metrics")
def prometheus_metrics() -> Response:
    """Scrape target. Gauges are refreshed here, at scrape time."""
    breaker.export_gauges()
    metrics.QUEUE_DEPTH.set(queue.depth())
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


@app.post("/v1/chat/completions")
def chat_completions(
    req: ChatRequest,
    response: Response,
    x_tenant: str | None = Header(default=None, alias="X-Tenant"),
    x_feature: str | None = Header(default=None, alias="X-Feature"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    x_class: str | None = Header(default=None, alias="X-Class"),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
):
    """OpenAI-compatible chat endpoint with routing, failover and attribution."""
    missing = [
        name
        for name, value in (
            ("X-Tenant", x_tenant),
            ("X-Feature", x_feature),
            ("X-Request-Id", x_request_id),
        )
        if not value
    ]
    if missing:
        return _error(
            400,
            f"missing required attribution header(s): {', '.join(missing)}",
            "missing_metadata",
        )

    request_class = (x_class or config.DEFAULT_CLASS).lower()
    if request_class not in config.PREFERENCE:
        return _error(
            400,
            f"unknown X-Class '{request_class}'; expected one of {', '.join(config.PREFERENCE)}",
            "invalid_class",
        )

    response.headers["X-Request-Id"] = x_request_id

    # A retried request must never produce a second answer (or a second job).
    if x_idempotency_key:
        seen = queue.lookup(x_idempotency_key)
        if seen:
            metrics.IDEMPOTENT_HITS.inc()
            metrics.CLIENT_REQUESTS.labels(
                tenant=x_tenant, feature=x_feature, status="replayed"
            ).inc()
            response.headers["X-Idempotent-Replay"] = "true"
            if seen.get("response"):
                return seen["response"]
            return JSONResponse(status_code=202, content=seen)

    messages = [m.model_dump() for m in req.messages]

    try:
        result = router.route(
            messages,
            request_class=request_class,
            tenant=x_tenant,
            feature=x_feature,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except router.NoProviderAvailable as exc:
        return _degraded(
            exc, req, messages, request_class,
            x_tenant, x_feature, x_request_id, x_idempotency_key, response,
        )

    body = completion_body(result)
    log.info(
        "request_id=%s tenant=%s feature=%s class=%s provider=%s status=ok "
        "latency_ms=%d failovers=%d tokens=%d cost_usd=%.6f",
        x_request_id, x_tenant, x_feature, request_class, result["provider"],
        result["latency_ms"], result["failovers"], result["usage"]["total_tokens"],
        result["cost_usd"],
    )
    metrics.CLIENT_REQUESTS.labels(tenant=x_tenant, feature=x_feature, status="ok").inc()
    response.headers["X-Gateway-Provider"] = result["provider"]
    response.headers["X-Gateway-Latency-Ms"] = str(result["latency_ms"])
    response.headers["X-Gateway-Failovers"] = str(result["failovers"])
    if x_idempotency_key:
        queue.remember(x_idempotency_key, {"status": "done", "response": body})
    return body


def _degraded(
    exc: router.NoProviderAvailable,
    req: ChatRequest,
    messages: list[dict[str, str]],
    request_class: str,
    tenant: str,
    feature: str,
    request_id: str,
    idempotency_key: str | None,
    response: Response,
):
    """Nothing served the request: queue it (deferrable) or fail fast (interactive)."""
    log.warning(
        "request_id=%s tenant=%s feature=%s class=%s status=degraded reason=%s attempts=%s",
        request_id, tenant, feature, request_class, exc.reason,
        [a["provider"] for a in exc.attempts],
    )

    if request_class != "deferrable":
        metrics.CLIENT_REQUESTS.labels(
            tenant=tenant, feature=feature, status="failed"
        ).inc()
        return _error(
            503,
            "all providers are unavailable; retry shortly "
            "(send X-Class: deferrable to have the gateway queue the work instead)",
            "no_provider_available",
        )

    job = {
        "messages": messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "tenant": tenant,
        "feature": feature,
        "request_id": request_id,
        "request_class": request_class,
        "idempotency_key": idempotency_key,
    }
    job_id = queue.enqueue(job)
    payload = {
        "status": "queued",
        "job_id": job_id,
        "reason": exc.reason,
        "poll": f"/v1/jobs/{job_id}",
    }
    if idempotency_key:
        queue.claim(idempotency_key, payload)
    metrics.CLIENT_REQUESTS.labels(tenant=tenant, feature=feature, status="queued").inc()
    return JSONResponse(status_code=202, content=payload, headers=dict(response.headers))


@app.get("/v1/jobs/{job_id}")
def job_status(job_id: str):
    """Where a deferred answer shows up once a worker drains it."""
    result = queue.get_result(job_id)
    if result is None:
        return _error(404, f"unknown job {job_id}", "not_found")
    return result


# --- admin -------------------------------------------------------------------

@app.get("/admin/status")
def admin_status(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    """One JSON view of the gateway: breakers, health window, queue, chaos."""
    if not _admin_ok(x_admin_token):
        return _error(403, "bad admin token", "auth")
    return {
        "providers": breaker.snapshot(),
        "queue_depth": queue.depth(),
        "chaos": chaos.snapshot(),
        "preference": config.PREFERENCE,
        "fake_providers": config.FAKE_PROVIDERS,
    }


@app.post("/admin/chaos")
def admin_chaos(
    req: ChaosRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Degrade a provider on demand — errors or added latency."""
    if not _admin_ok(x_admin_token):
        return _error(403, "bad admin token", "auth")
    if req.provider not in config.PROVIDERS:
        return _error(400, f"unknown provider {req.provider}", "invalid_provider")
    fault = chaos.inject(
        req.provider,
        mode=req.mode,
        error_type=req.error_type,
        latency_ms=req.latency_ms,
        rate=req.rate,
    )
    log.warning("chaos %s", fault)
    return {"chaos": chaos.snapshot(), "applied": fault}


@app.post("/admin/reset")
def admin_reset(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    """Back to a clean slate: no chaos, no breaker state, empty window and queue."""
    if not _admin_ok(x_admin_token):
        return _error(403, "bad admin token", "auth")
    chaos.clear()
    breaker.reset()
    health.clear()
    queue.clear()
    return {"status": "reset"}
