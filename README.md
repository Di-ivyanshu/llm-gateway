# LLM Gateway — self-healing, OpenAI-compatible

One FastAPI service that every LLM call goes through. It watches each provider's
health, **fails over automatically** when one degrades, **queues** work that can
wait when everything is down, and attributes **cost per tenant and feature** —
all behind an OpenAI-compatible API, so a client adopts it by changing one
base-URL environment variable.

> **100.00% availability across 988 requests while providers were failing.**
> The same traffic pinned to a single provider: **28.14%** (28–48% across runs —
> which provider carries the recovery phase varies).
> Nothing was lost: 869 answered immediately, 119 accepted into the deferrable
> queue during a total outage, 0 errors reached a caller.
> Reproduce it in ~25 seconds, no keys required: `python -m bench.outage_test`
> — full numbers in [`bench/results.md`](bench/results.md).

<!-- 90-second screen capture: healthy traffic → degrade a provider → breaker
     trips → traffic reroutes → breaker closes on recovery.
     Recording script: docs/DEMO.md -->
_Demo video: **TODO — record with `docs/DEMO.md` and paste the link here.**_

---

## What it does

```
             ┌──────────── one OpenAI-compatible endpoint ────────────┐
client ──▶   │  POST /v1/chat/completions   (X-Tenant, X-Feature,     │
             │                               X-Request-Id required)   │
             └───────────────────────┬───────────────────────────────┘
                                     ▼
                     router ── preference list per request class
                        │      (interactive: groq → gemini → openrouter)
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
    breaker[groq]  breaker[gemini]  breaker[openrouter]
    closed/open/half-open, driven by a Redis sliding window
        │               │               │
        └────────── first one that answers wins ──────────┐
                                                          ▼
                        health window · Prometheus metrics · cost per tenant
                                                          │
   all providers open?  ── interactive → 503 fail fast    │
                        └─ deferrable  → Redis queue ─▶ worker drains with
                                                        exponential backoff
```

| Capability | Where |
| --- | --- |
| Real completions via LiteLLM (Groq / Gemini / OpenRouter) | `app/providers.py` |
| Error taxonomy (`rate_limit`, `timeout`, `auth`, …) | `app/errors.py` |
| Sliding-window health per provider (Redis sorted set) | `app/health.py` |
| Circuit breaker with **half-open** self-healing | `app/breaker.py` |
| Failover + optional hedged requests | `app/router.py` |
| Deferrable queue, backoff + jitter, idempotency | `app/queue.py`, `app/worker.py` |
| Cost attribution by tenant × feature | `app/cost.py` |
| Chaos injection for the demo | `app/chaos.py`, `POST /admin/chaos` |
| Prometheus metrics + Grafana dashboard | `app/metrics.py`, `dashboards/gateway.json` |

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI-compatible. Requires `X-Tenant`, `X-Feature`, `X-Request-Id`. |
| `GET /v1/jobs/{id}` | Result of a job that was queued during an outage. |
| `GET /health` | Liveness. |
| `GET /metrics` | Prometheus scrape target. |
| `GET /admin/status` | Breaker state, health window, queue depth, active chaos. |
| `POST /admin/chaos` | Degrade a provider on purpose (`{"provider":"groq","mode":"error"}`). |
| `POST /admin/reset` | Clear chaos, breakers, health window and queue. |

Request headers:

| Header | Required | Meaning |
| --- | --- | --- |
| `X-Tenant` | yes | Who to bill. Missing → 400. |
| `X-Feature` | yes | Which feature spent it. Missing → 400. |
| `X-Request-Id` | yes | Trace id, echoed back. Missing → 400. |
| `X-Class` | no | `interactive` (default, fail fast) or `deferrable` (queue when down). |
| `X-Idempotency-Key` | no | A retry with the same key can never duplicate work. |

The response adds a non-standard `gateway` block (which provider answered, how
many failovers, the estimated cost). OpenAI clients ignore it.

## Run it

```
python -m venv .venv
.venv\Scripts\activate                       # Windows
pip install -r requirements.txt
copy .env.example .env                       # then paste your provider keys
python -m uvicorn app.main:app --port 8080
```

```
curl -X POST http://localhost:8080/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -H "X-Tenant: acme" -H "X-Feature: chat" -H "X-Request-Id: r1" ^
  -d "{\"model\":\"gateway-auto\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
```

Nothing is mandatory beyond one provider key: with no `REDIS_URL` the health
window, queue and idempotency store fall back to in-process equivalents, and the
API drains its own deferrable queue in a background thread.

Once you do set `REDIS_URL`, run real workers instead — as many as you like:

```
python -m app.worker
```

## Tests & benchmark

```
pytest -q                        # 105 tests, fully offline: no keys, no Redis, no network
python -m bench.outage_test      # ~25s availability benchmark, fake providers
```

The benchmark drives steady traffic while `/admin/chaos` degrades providers one
by one, then compares the gateway against a single-provider baseline. Providers
are faked, so it costs nothing and is repeatable.

## Dashboard

```
prometheus.exe --config.file=D:\llm-gateway\ops\prometheus.yml     # scrapes :8080/metrics
grafana-server.exe                                                  # add Prometheus as a datasource
```
Import [`dashboards/gateway.json`](dashboards/gateway.json): availability, RPS
and p95 by provider, error rate, **circuit-state timeline**, failover events,
queue depth, and cost/hour by tenant and feature.

The demo script — healthy → degrade → trip → reroute → heal — is in
[`docs/DEMO.md`](docs/DEMO.md).

## Design notes

* **Attribution is mandatory, not optional.** A gateway without per-tenant cost
  data is just a proxy, so a request missing `X-Tenant`/`X-Feature` is a 400.
* **Instrument before failing over.** Health and metrics (Phase 2) landed before
  the breaker (Phase 3): you cannot tune a threshold you cannot see.
* **half-open matters.** Without it a breaker opens once and never heals. A
  bounded number of probes goes to the recovering provider; a success closes it
  and clears its window so stale failures don't re-trip it.
* **Retries need idempotency.** Anything replayed carries `X-Idempotency-Key`,
  claimed with `SET NX`, so a blip cannot duplicate a side effect.
* **Interactive vs deferrable.** A human waiting gets a fast, honest 503. A
  nightly summarisation gets queued and retried with backoff + jitter.
* **Known trade-off: label cardinality.** `tenant` and `feature` come straight
  from client headers onto Prometheus labels, so a careless caller can create
  unbounded time series. In a real deployment you'd validate them against a
  known tenant list at the edge before they reach the metric.

## Status

- [x] **Phase 0** — FastAPI skeleton + OpenAI-compatible echo endpoint + tests
- [x] **Phase 1** — LiteLLM pass-through + required request metadata + error taxonomy
- [x] **Phase 2** — per-provider health window (Redis) + Prometheus `/metrics`
- [x] **Phase 3** — circuit breaker + automatic failover + half-open + hedging
- [x] **Phase 4** — deferrable queue + backoff/jitter + idempotency + worker
- [x] **Phase 5** — cost attribution + Grafana dashboard + chaos + availability benchmark

## Stack

Python · FastAPI · LiteLLM · Redis (Upstash, optional) · Prometheus + Grafana ·
Groq / Gemini / OpenRouter. No Docker.

## Related

Part of a six-project portfolio. Its sibling
[Nocturne (Knowledge-Graph RAG)](https://github.com/Di-ivyanshu/nocturne-RAG)
routes its LLM calls through this gateway by setting one variable:

```
OPENAI_BASE_URL=http://localhost:8080/v1
```
