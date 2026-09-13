# Handoff — what was built, what is left for you

Phases 0 → 5 of `docs/CHECKLIST.md` are done, plus the Nocturne finale, SSE
streaming, a Groq model migration, and a built-in live dashboard. Everything is
committed locally on `main` in both repos — **the push is yours to make**.

## What now exists

| File | What it does |
| --- | --- |
| `app/providers.py` | One real provider call via LiteLLM → `{content, latency_ms, usage}`. Chaos + fake-provider hooks live here. |
| `app/errors.py` | Any exception → `rate_limit / timeout / auth / content_filter / server_error / unknown`. |
| `app/health.py` | Sliding window per provider (Redis sorted set, in-process fallback) → success rate, p50/p95/p99. |
| `app/metrics.py` | Every Prometheus series the dashboard draws. |
| `app/breaker.py` | closed → open → half-open → closed, driven by the health window. |
| `app/router.py` | Preference list per request class, failover, optional hedging, cost booking. |
| `app/queue.py` + `app/worker.py` | Deferrable jobs, exponential backoff + jitter, idempotency (SET NX). Without `REDIS_URL` the API drains its own queue in a background thread; with Redis, run `python -m app.worker` as separate processes. |
| `app/cost.py` | Tokens → USD, attributed to tenant × feature. |
| `app/chaos.py` + `POST /admin/chaos` | Degrade a provider on demand — the demo and benchmark run on this. |
| `bench/outage_test.py` | The availability number. `python -m bench.outage_test` (~25s, free). |
| `app/static/dashboard.html` + `app/recent.py` | **Built-in live console at `/dashboard`** — provider states, rps by provider, a live request table with the routing trail, and chaos buttons. No Grafana required. |
| `bench/traffic.py` | Steady demo traffic (`python -m bench.traffic --rate 6`) for recording. |
| `dashboards/gateway.json`, `ops/prometheus.yml` | Import-ready Grafana dashboard + scrape config (optional, for history). |
| `tests/` | 117 offline tests: no keys, no Redis, no network. |

## Verified

* `pytest -q` → **117 passed**.
* `python -m bench.outage_test` → **100.00% availability over 988 requests**, 0 lost;
  single-provider baseline 28.14% (varies 28-48% between runs). See `bench/results.md`.
* Real calls through the gateway on the new Groq model (`openai/gpt-oss-120b`
  with `reasoning_effort=low`): **~0.6s** per answer, versus 5-10s on the retired
  llama-3.3-70b.
* The live dashboard was driven with real traffic: provider states, the rps chart,
  the request table with `groq(server_error) → gemini` trails, and the chaos
  buttons all behave.
* **Nocturne really runs on it.** With the gateway up, `llm.generate()` and
  `llm.generate_stream()` from `D:\rag1` both returned answers through it, the
  cost landed on `tenant="nocturne", feature="rag"`, and with Groq chaos-broken
  Nocturne still got its answer (from Gemini) without noticing.

## Two things worth knowing

1. **Your `.env` had a BOM problem.** The first key line (`GROQ_API_KEY`) was
   being read as `\ufeffGROQ_API_KEY`, so Groq silently looked "unconfigured"
   and every request failed over to Gemini. `.env` was rewritten as clean UTF-8;
   if you ever regenerate it from PowerShell, use `-Encoding utf8NoBOM` (or just
   edit it in an editor) or the same thing will happen again.
2. **The Groq/Gemini free tiers are slow** — measured 5.4s and 9.9s on a cold
   call. `BREAKER_P95_BUDGET_MS` therefore defaults to 15000, not the 8000 a
   normal web service would use; otherwise the breaker would open on healthy
   providers.
3. **Groq retired `llama-3.3-70b-versatile` on 2026-08-16.** Both projects now
   default to `openai/gpt-oss-120b` — same 131k context, cheaper list price, and
   still on the free tier. It is a *reasoning* model, so `GROQ_REASONING_EFFORT`
   defaults to `low`: measured 25 tokens / 0.4s versus 47 tokens / 10.1s for the
   same one-word answer at the default effort.

## Left for you (the 🧑 items)

- [ ] **Push**: `git push origin main` — needs browser auth as `Di-ivyanshu`
      (the `developer-integrowai` login must not be used for this repo).
- [ ] **Upstash Redis** (optional but nice): create the free DB, put `REDIS_URL`
      in `.env`. Without it the health window/queue live in-process, which is
      fine for one instance but doesn't survive a restart.
- [ ] **Prometheus + Grafana** — now OPTIONAL. `/dashboard` shows live state without
      them; install them only if you want history beyond the last few hundred requests.
- [ ] **Record the 90-second demo**: step-by-step script in `docs/DEMO.md`, then
      replace the TODO line at the top of `README.md` with the video link.

## The finale — done, and how to undo it

`D:\rag1` (Nocturne) now generates through this gateway:

* `.env`: `OPENAI_BASE_URL=http://localhost:8080/v1` (it already said
  `LLM_PROVIDER=openai`, so that is the only value that actually changed). The
  direct OpenRouter URL sits **commented on the line right above** — swap the
  two lines to bypass the gateway again.
  A copy of the original file is at
  `%LOCALAPPDATA%\Temp\claude\D--llm-gateway\<session>\scratchpad\rag1.env.before-gateway.bak`.
* `src/llm.py`: `_compat_headers()` now sends `X-Tenant` / `X-Feature` /
  `X-Request-Id` (the gateway rejects a request without them; every other
  OpenAI-compatible endpoint ignores them).
* `src/config.py`: `GATEWAY_TENANT` / `GATEWAY_FEATURE`, default `nocturne` / `rag`.
* Both READMEs describe the link.

**Consequence to remember:** Nocturne now needs the gateway running. Start it
first (`python -m uvicorn app.main:app --port 8080`) or generation fails.

Streaming was added to the gateway for this: Nocturne's answer UI streams over
SSE, so `/v1/chat/completions` now accepts `"stream": true` and emits standard
`chat.completion.chunk` events. It routes to completion first and then streams —
buffered on purpose, because failover is impossible once the first byte is out.

## Quick commands

```powershell
cd D:\llm-gateway
.venv\Scripts\activate

pytest -q                                  # offline suite
python -m bench.outage_test                # availability number (fake providers, free)
python -m uvicorn app.main:app --port 8080 # the gateway
python -m app.worker                       # drains queued deferrable jobs

Invoke-RestMethod http://localhost:8080/admin/status | ConvertTo-Json -Depth 5
```
