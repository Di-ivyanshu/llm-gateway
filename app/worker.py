"""Queue worker — drains deferrable jobs once a provider is healthy again.

Run it alongside the API:

    python -m app.worker

It pops one due job at a time, routes it exactly like an HTTP request would, and
on failure re-schedules it with exponential backoff + jitter until
`QUEUE_MAX_ATTEMPTS`. The finished answer is stored under the job id (and under
the idempotency key, if one was supplied) so the caller can pick it up from
`GET /v1/jobs/{id}`.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from . import config, metrics, queue, router
from .shapes import completion_body

log = logging.getLogger("gateway.worker")


def _finish(job: dict[str, Any], payload: dict[str, Any]) -> None:
    queue.store_result(job["id"], payload)
    if job.get("idempotency_key"):
        queue.remember(job["idempotency_key"], payload)


def run_once(*, now: float | None = None) -> dict[str, Any] | None:
    """Process at most one due job. Returns a small outcome dict, or None."""
    job = queue.pop_due(now=now)
    if job is None:
        return None

    try:
        result = router.route(
            job["messages"],
            request_class=job.get("request_class", "deferrable"),
            tenant=job.get("tenant", "unknown"),
            feature=job.get("feature", "unknown"),
            temperature=job.get("temperature", 0.2),
            max_tokens=job.get("max_tokens"),
        )
    except router.NoProviderAvailable as exc:
        delay = queue.retry(job, now=now)
        if delay is None:
            payload = {
                "status": "failed",
                "job_id": job["id"],
                "attempts": job["attempts"],
                "reason": exc.reason,
            }
            _finish(job, payload)
            metrics.QUEUE_DONE.labels(status="failed").inc()
            log.error("job=%s giving up after %d attempts", job["id"], job["attempts"])
            return {"job_id": job["id"], "status": "failed"}

        queue.store_result(
            job["id"],
            {
                "status": "retrying",
                "job_id": job["id"],
                "attempts": job["attempts"],
                "next_attempt_in_s": round(delay, 2),
                "reason": exc.reason,
            },
        )
        log.warning(
            "job=%s attempt %d failed (%s); retrying in %.1fs",
            job["id"], job["attempts"], exc.reason, delay,
        )
        return {"job_id": job["id"], "status": "retrying", "delay_s": delay}

    payload = {"status": "done", "job_id": job["id"], "response": completion_body(result)}
    _finish(job, payload)
    metrics.QUEUE_DONE.labels(status="done").inc()
    log.info(
        "job=%s drained via %s in %dms", job["id"], result["provider"], result["latency_ms"]
    )
    return {"job_id": job["id"], "status": "done", "provider": result["provider"]}


def drain(max_jobs: int = 100) -> list[dict[str, Any]]:
    """Process every job that is due right now. Used by tests and the benchmark."""
    outcomes = []
    for _ in range(max_jobs):
        outcome = run_once()
        if outcome is None:
            break
        outcomes.append(outcome)
    return outcomes


def main() -> None:  # pragma: no cover - process entrypoint
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    log.info("worker started (redis=%s)", bool(config.REDIS_URL))
    while True:
        if run_once() is None:
            time.sleep(config.WORKER_POLL_S)


if __name__ == "__main__":  # pragma: no cover
    main()
