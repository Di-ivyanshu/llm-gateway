# Handoff — what was built, what is left for you

Built in one overnight session: **Phases 1 → 5 of `docs/CHECKLIST.md`**, on top of
the Phase 0 skeleton. Everything below is committed locally on `main`; the push
is yours to make.

## What now exists

| File | What it does |
| --- | --- |
| `app/providers.py` | One real provider call via LiteLLM → `{content, latency_ms, usage}`. Chaos + fake-provider hooks live here. |
| `app/errors.py` | Any exception → `rate_limit / timeout / auth / content_filter / server_error / unknown`. |
| `app/health.py` | Sliding window per provider (Redis sorted set, in-process fallback) → success rate, p50/p95/p99. |
| `app/metrics.py` | Every Prometheus series the dashboard draws. |
| `app/breaker.py` | closed → open → half-open → closed, driven by the health window. |
| `app/router.py` | Preference list per request class, failover, optional hedging, cost booking. |
| `app/queue.py` + `app/worker.py` | Deferrable jobs, exponential backoff + jitter, idempotency (SET NX). |
| `app/cost.py` | Tokens → USD, attributed to tenant × feature. |
| `app/chaos.py` + `POST /admin/chaos` | Degrade a provider on demand — the demo and benchmark run on this. |
| `bench/outage_test.py` | The availability number. `python -m bench.outage_test` (~25s, free). |
| `dashboards/gateway.json`, `ops/prometheus.yml` | Import-ready Grafana dashboard + scrape config. |
| `tests/` | 104 offline tests: no keys, no Redis, no network. |

## Verified

* `pytest -q` → **104 passed**.
* `python -m bench.outage_test` → **100.00% availability over 986 requests**;
  single-provider baseline 47.97%. See `bench/results.md`.
* One **real** call through the gateway (Groq, then Gemini on failover) returned
  a real completion — so LiteLLM, the keys and the failover path all work for real.

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

## Left for you (the 🧑 items)

- [ ] **Push**: `git push origin main` — needs browser auth as `Di-ivyanshu`
      (the `developer-integrowai` login must not be used for this repo).
- [ ] **Upstash Redis** (optional but nice): create the free DB, put `REDIS_URL`
      in `.env`. Without it the health window/queue live in-process, which is
      fine for one instance but doesn't survive a restart.
- [ ] **Prometheus + Grafana**: download the Windows binaries, run
      `prometheus.exe --config.file=D:\llm-gateway\ops\prometheus.yml`, add the
      datasource, import `dashboards/gateway.json`.
- [ ] **Record the 90-second demo**: step-by-step script in `docs/DEMO.md`, then
      replace the TODO line at the top of `README.md` with the video link.
- [ ] **The finale** — point Nocturne at the gateway: in `D:\rag1\.env` set
      `OPENAI_BASE_URL=http://localhost:8080/v1`. Deliberately NOT done for you:
      Nocturne would then fail whenever the gateway isn't running, and that
      should be your choice to flip.

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
