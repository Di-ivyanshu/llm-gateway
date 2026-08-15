"""Deferrable job queue — what happens when every provider is down.

Interactive requests fail fast (a human is waiting; a 503 they can retry is
honest). Deferrable ones are parked here and drained by `app.worker` with
exponential backoff + jitter once a provider comes back.

Storage is a Redis **sorted set** scored by "not before" (so a backed-off job is
simply scheduled into the future) plus plain keys for results and idempotency.
As everywhere else in this project, no `REDIS_URL` → an in-process backend with
the same API, so tests and a laptop demo need no server.

Idempotency: `X-Idempotency-Key` is claimed with SET NX. A duplicate request can
therefore never create a second job — it gets the first job's id (and its result
once it has one), which is the whole point: a retried request must not duplicate
a side effect.
"""
from __future__ import annotations

import json
import random
import threading
import time
import uuid
from typing import Any

from . import config, metrics

QUEUE_KEY = "gw:queue"
RESULT_KEY = "gw:result:{}"
IDEM_KEY = "gw:idem:{}"


class MemoryBackend:
    """In-process fallback: a scored list plus a dict with expiries."""

    def __init__(self) -> None:
        self._zset: list[tuple[float, str]] = []
        self._kv: dict[str, tuple[float, str]] = {}  # key -> (expires_at, value)
        self._lock = threading.Lock()

    def schedule(self, member: str, score: float) -> None:
        with self._lock:
            self._zset.append((score, member))

    def pop_due(self, now: float) -> str | None:
        with self._lock:
            due = [(s, m) for s, m in self._zset if s <= now]
            if not due:
                return None
            due.sort(key=lambda row: row[0])
            score, member = due[0]
            self._zset.remove((score, member))
            return member

    def count(self) -> int:
        with self._lock:
            return len(self._zset)

    def set(self, key: str, value: str, ttl: int, *, only_if_absent: bool = False) -> bool:
        now = time.time()
        with self._lock:
            existing = self._kv.get(key)
            if only_if_absent and existing and existing[0] > now:
                return False
            self._kv[key] = (now + ttl, value)
            return True

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._kv.get(key)
            if not row or row[0] <= time.time():
                self._kv.pop(key, None)
                return None
            return row[1]

    def clear(self) -> None:
        with self._lock:
            self._zset.clear()
            self._kv.clear()


class RedisBackend:
    def __init__(self, client: Any) -> None:
        self.client = client

    def schedule(self, member: str, score: float) -> None:
        self.client.zadd(QUEUE_KEY, {member: score})

    def pop_due(self, now: float) -> str | None:
        rows = self.client.zrangebyscore(QUEUE_KEY, 0, now, start=0, num=1)
        if not rows:
            return None
        member = rows[0]
        member = member.decode() if isinstance(member, bytes) else member
        # ZREM returning 1 means we won the race for this job against any other worker.
        if self.client.zrem(QUEUE_KEY, member):
            return member
        return None

    def count(self) -> int:
        return int(self.client.zcard(QUEUE_KEY))

    def set(self, key: str, value: str, ttl: int, *, only_if_absent: bool = False) -> bool:
        return bool(self.client.set(key, value, ex=ttl, nx=only_if_absent or None))

    def get(self, key: str) -> str | None:
        raw = self.client.get(key)
        return raw.decode() if isinstance(raw, bytes) else raw

    def clear(self) -> None:
        self.client.delete(QUEUE_KEY)


_backend: Any = None


def backend() -> Any:
    global _backend
    if _backend is None:
        if config.REDIS_URL:
            import redis

            _backend = RedisBackend(redis.from_url(config.REDIS_URL, decode_responses=True))
        else:
            _backend = MemoryBackend()
    return _backend


def set_backend(new: Any) -> None:
    global _backend
    _backend = new


# --- jobs --------------------------------------------------------------------

def enqueue(job: dict[str, Any], *, delay_s: float = 0.0, now: float | None = None) -> str:
    """Park a job. Returns its id."""
    now = time.time() if now is None else now
    job.setdefault("id", f"job-{uuid.uuid4().hex[:12]}")
    job.setdefault("attempts", 0)
    job.setdefault("created_at", now)
    job["not_before"] = now + delay_s
    backend().schedule(json.dumps(job), job["not_before"])
    metrics.QUEUED.labels(
        tenant=job.get("tenant", "unknown"), feature=job.get("feature", "unknown")
    ).inc()
    metrics.QUEUE_DEPTH.set(depth())
    _put(RESULT_KEY.format(job["id"]), {"status": "queued", "job_id": job["id"]})
    return job["id"]


def pop_due(*, now: float | None = None) -> dict[str, Any] | None:
    """Next job whose scheduled time has arrived, or None."""
    now = time.time() if now is None else now
    member = backend().pop_due(now)
    metrics.QUEUE_DEPTH.set(depth())
    return json.loads(member) if member else None


def depth() -> int:
    return backend().count()


def backoff_delay(attempts: int) -> float:
    """Exponential backoff with jitter — jitter stops a thundering-herd retry."""
    raw = min(
        config.QUEUE_BACKOFF_BASE_S * (2 ** max(0, attempts - 1)),
        config.QUEUE_BACKOFF_MAX_S,
    )
    spread = raw * config.QUEUE_JITTER
    return max(0.0, raw + random.uniform(-spread, spread))


def retry(job: dict[str, Any], *, now: float | None = None) -> float | None:
    """Re-schedule a failed job. Returns the delay, or None when it's exhausted."""
    job["attempts"] = job.get("attempts", 0) + 1
    if job["attempts"] >= config.QUEUE_MAX_ATTEMPTS:
        return None
    delay = backoff_delay(job["attempts"])
    metrics.QUEUE_RETRIES.inc()
    enqueue(job, delay_s=delay, now=now)
    return delay


# --- results & idempotency ---------------------------------------------------

def _put(key: str, value: dict[str, Any], *, only_if_absent: bool = False) -> bool:
    return backend().set(
        key, json.dumps(value), config.IDEMPOTENCY_TTL_S, only_if_absent=only_if_absent
    )


def _get(key: str) -> dict[str, Any] | None:
    raw = backend().get(key)
    return json.loads(raw) if raw else None


def store_result(job_id: str, payload: dict[str, Any]) -> None:
    _put(RESULT_KEY.format(job_id), payload)


def get_result(job_id: str) -> dict[str, Any] | None:
    return _get(RESULT_KEY.format(job_id))


def claim(idempotency_key: str, value: dict[str, Any]) -> bool:
    """True if this key is new (we own it); False if it was already used."""
    return _put(IDEM_KEY.format(idempotency_key), value, only_if_absent=True)


def remember(idempotency_key: str, value: dict[str, Any]) -> None:
    """Overwrite what an idempotency key maps to (e.g. once the answer exists)."""
    _put(IDEM_KEY.format(idempotency_key), value)


def lookup(idempotency_key: str) -> dict[str, Any] | None:
    return _get(IDEM_KEY.format(idempotency_key))


def clear() -> None:
    backend().clear()
    metrics.QUEUE_DEPTH.set(0)
