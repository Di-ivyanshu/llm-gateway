# Project 3 — Self-Healing LLM Gateway — Build Plan

A standalone service that every LLM call routes through. It tracks each provider's
health, and when one degrades it fails over automatically — without the calling app
ever seeing an error. Then you break a provider on purpose and watch traffic reroute
on a live dashboard.

**New repo:** `llm-gateway` (separate from Nocturne). Later, point Nocturne at it.

---

## The number this project must produce
> Sustained **99.x% availability** through simulated provider outages, with **per-tenant / per-feature cost attribution**.

That availability figure + cost breakdown is the portfolio artifact. README opens with the 90-sec demo video and this number.

**Resume line:** *Built an LLM gateway with per-provider circuit breaking and automatic failover; sustained X% availability through simulated provider outages, with per-tenant cost attribution.*

---

## Confirmed decisions
- **Stack:** Python, FastAPI, LiteLLM, Redis, Prometheus, Grafana.
- **Free + cloud-first (no Docker):**
  - **Redis → Upstash** (serverless Redis, free tier, no install).
  - **Prometheus + Grafana → local single binaries** (both ship as plain `.exe` on Windows — no Docker), *or* Grafana Cloud free tier. Local binaries are easiest to screen-record for the demo.
- **Providers:** Groq, Gemini, OpenRouter — reuse your Nocturne keys.
- **API shape:** OpenAI-compatible (`/v1/chat/completions`), so any client — including Nocturne — adopts it by changing one base-URL env var.

---

## Architecture
```
                 ┌────────────────────── llm-gateway (FastAPI) ──────────────────────┐
  App / Nocturne │  /v1/chat/completions                                             │
  ───────────────▶  1. require metadata (tenant, feature, request_id)                │
                 │  2. classify: interactive | deferrable                            │
                 │  3. pick provider by preference list for this request class       │
                 │        │                                                          │
                 │        ▼                                                          │
                 │   ┌─ circuit breaker per provider (closed / open / half-open) ─┐  │
                 │   │   Groq   │   Gemini   │  OpenRouter                          │  │
                 │   └──────────┴────────────┴──────────────────────────────────────┘  │
                 │        │ health (success%, p50/p95/p99, error taxonomy) in Redis  │
                 │        │ deferrable + all-degraded → Redis queue + backoff+retry  │
                 │        ▼                                                          │
                 │   /metrics  ──scrape──▶  Prometheus  ──▶  Grafana dashboard        │
                 └────────────────────────────────────────────────────────────────────┘
                              ▲ chaos endpoint injects errors/latency into a provider
```

---

## Repo layout
```
llm-gateway/
├── app/
│   ├── main.py            FastAPI: /v1/chat/completions, /metrics, /admin/chaos
│   ├── config.py          providers, preference lists, thresholds, Redis URL
│   ├── providers.py       LiteLLM wrapper: normalize calls + error taxonomy
│   ├── health.py          Redis sliding-window stats per provider
│   ├── breaker.py         circuit breaker state machine (closed/open/half-open)
│   ├── router.py          pick provider by class + failover + hedging
│   ├── queue.py           deferrable work: enqueue, backoff+jitter, idempotency
│   ├── cost.py            token→$ per provider, attribute by tenant/feature
│   └── metrics.py         Prometheus counters/histograms/gauges
├── chaos/inject.py        toggle failure/latency on a provider for the demo
├── bench/outage_test.py   drive traffic while killing a provider → availability %
├── dashboards/gateway.json Grafana dashboard export
├── tests/                 offline tests (mock providers + fakeredis)
├── .env.example
└── README.md              demo video + availability number at the very top
```

---

## Build sequence — 5 phases (~2–3 weeks part-time)

### Phase 0 — Skeleton (½ day)
- `git init`, venv, FastAPI hello, `.env.example`.
- Upstash Redis account → connection URL in `.env`.
- One passing test + a stub `/v1/chat/completions` that echoes.

### Phase 1 — Pass-through gateway (2–3 days)
- Accept an **OpenAI-compatible** request; call the model via **LiteLLM** (one interface for Groq/Gemini/OpenRouter).
- **Require metadata** on every request: `tenant`, `feature`, `request_id`. Reject if missing — cost attribution depends on it.
- Return the OpenAI-shaped response. **Milestone:** a `curl` (or the OpenAI SDK pointed at your URL) gets a real completion.

### Phase 2 — Health tracking (3–4 days)
- Per provider, keep a **rolling window** in Redis: success rate, p50/p95/p99 latency, and an **error taxonomy** (rate-limit vs timeout vs server-error vs content-filter vs auth). *Different errors mean different failover decisions.*
- Use a Redis **sliding window** (sorted-set by timestamp) so a restart doesn't reset your view.
- Expose **`/metrics`** for Prometheus. **Instrument before failover** — you can't tune what you can't see.
- **Milestone:** Grafana shows per-provider latency + error rate live.

### Phase 3 — Failover + self-healing (4–5 days) ← the heart
- **Circuit breaker per provider:** `closed → open` when error-rate or p95 crosses threshold in the window; route to the **next provider in the preference list** for that request class.
- **Preference lists differ by class:** a cheap classification call and a long generation shouldn't fail over the same way.
- **Half-open probes = self-healing:** send a small % of traffic back to a recovering provider; close the breaker if it succeeds, reopen instantly if not.
- **Hedged requests** (latency-sensitive class): fire a 2nd provider after N ms, take the first to return, cancel the loser. Document that hedging ~doubles spend on hedged calls.
- **Milestone:** kill a provider in code → traffic keeps flowing.

### Phase 4 — Queue deferrable work (2–3 days)
- Classify **interactive vs deferrable** at the API boundary. Interactive fails fast; deferrable survives an outage.
- When all providers are degraded, push deferrable jobs to a **Redis queue** with **exponential backoff + jitter** instead of erroring.
- **Idempotency keys** so a retry can't duplicate a side effect. *This is the "I've operated something in prod" signal.*

### Phase 5 — Dashboard, chaos, demo, number (3–4 days)
- **Grafana panels:** RPS by provider, error rate, p95, **circuit-state timeline**, failover events, queue depth, **cost/hour by tenant & feature**.
- **Chaos endpoint** (`/admin/chaos`): inject errors/latency into one provider on demand.
- **`bench/outage_test.py`:** drive steady traffic while chaos degrades a provider → compute the **availability %** (successful responses / total) → that's your number.
- **90-sec screen capture:** healthy traffic → degrade a provider → breaker trips → traffic reroutes → breaker closes on recovery. This goes at the top of the README.

---

## Connect it to Nocturne (the cohesion move)
Once the gateway is live, in Nocturne's `.env`:
```
OPENAI_BASE_URL=http://localhost:8080/v1   # point at the gateway
```
Now Nocturne's LLM calls flow through the gateway → your two projects reference each other. Mention it in both READMEs.

> **Head-start:** Nocturne's `src/llm.py` already has provider rotation + failover. Port that logic as the starting point for `providers.py` / `router.py` — you're not starting from zero.

---

## Where it usually goes wrong (avoid these)
- **No cost attribution** → removes the main reason companies build a gateway. (Metadata from Phase 1 fixes it.)
- **Retrying non-idempotent calls** → a blip becomes duplicated side effects. (Idempotency keys.)
- **No half-open state** → the breaker opens once and never heals.
- **A dashboard with no chaos test** → failover logic that's never proven.

## Done when
You can **degrade a provider live** while traffic keeps flowing, and the dashboard shows the **trip → reroute → automatic recovery** — with an availability number and a per-tenant cost panel.
