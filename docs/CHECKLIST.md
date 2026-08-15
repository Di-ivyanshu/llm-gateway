# LLM Gateway — Granular Build Checklist

Task-by-task. Tick each box as you go. Every phase ends with a **milestone** you can
see working and a **commit**. Stack: Python · FastAPI · LiteLLM · Redis (Upstash) ·
Prometheus + Grafana · providers Groq / Gemini / OpenRouter. API is OpenAI-compatible.

> Legend: 🧑 = only you can do it (account/keys)   ⌨️ = code   ✅ = verify

---

## Prerequisites (do these once, before Phase 1)
- [x] 🧑 A GitHub repo `llm-gateway` under your account (create empty, no README).
- [x] 🧑 Provider keys ready in a notepad: `GROQ_API_KEY`, `GEMINI_API_KEY`, and an
      OpenRouter key (`OPENAI_API_KEY` + base url). You already have these from Nocturne.
- [ ] 🧑 An Upstash account (free) → create a Redis database → copy its `REDIS_URL`.
      (Needed from Phase 2, not before.)
- [ ] 🧑 (Phase 5) Grafana + Prometheus — download the Windows binaries, OR a free
      Grafana Cloud account. Not needed until Phase 5.

---

## Phase 0 — Skeleton & repo  (½ day)
Goal: a running FastAPI service with an OpenAI-shaped **echo** endpoint + a green test.

- [x] ⌨️ Create folder `D:\llm-gateway` and open a terminal there.
- [x] ⌨️ `git init`
- [x] ⌨️ `python -m venv .venv` then `.venv\Scripts\activate`
- [x] ⌨️ Create **`requirements.txt`**:
      ```
      fastapi
      uvicorn[standard]
      pydantic
      python-dotenv
      httpx
      pytest
      ```
- [x] ⌨️ `pip install -r requirements.txt`
- [x] ⌨️ Create **`app/__init__.py`** (empty).
- [x] ⌨️ Create **`app/config.py`**: load `.env` with `python-dotenv`; expose
      `PROVIDERS = ["groq","gemini","openrouter"]` and `REDIS_URL = os.getenv("REDIS_URL","")`.
- [x] ⌨️ Create **`app/main.py`**:
      - `FastAPI(title="llm-gateway")`
      - `GET /health` → `{"status":"ok"}`
      - Pydantic models `Message{role,content}`, `ChatRequest{model, messages}`
      - `POST /v1/chat/completions` → **echo** the last user message in OpenAI response
        shape (`choices[0].message.content = "echo: <text>"`).
- [x] ⌨️ Create **`tests/test_smoke.py`**: use `fastapi.testclient.TestClient`;
      test `/health` == ok, and echo returns `"echo: hi"`.
- [x] ⌨️ Create **`.env.example`** (keys blank), **`.gitignore`** (`.venv/ __pycache__/ *.pyc .env`),
      **`README.md`** (title + a Status checklist of the 6 phases).
- [x] ✅ `pytest -q` → green.
- [x] ✅ `python -m uvicorn app.main:app --port 8080` → open `http://localhost:8080/health`;
      `curl` the chat endpoint → see the echo.
- [x] ⌨️ `git add -A && git commit -m "Phase 0: FastAPI skeleton + echo + tests"`
- [x] 🧑 Add the GitHub remote and push (auth as your account).

**Milestone:** the service runs and echoes an OpenAI-shaped response; tests pass.

---

## Phase 1 — Real pass-through via LiteLLM + metadata  (2–3 days)
Goal: real completions from a provider, and every request must carry attribution metadata.

- [x] ⌨️ Add `litellm` to `requirements.txt`; `pip install`.
- [x] 🧑 Put real keys in `.env` (copy from `.env.example`).
- [x] ⌨️ **`app/providers.py`**: `call(provider, model, messages) -> dict` using
      `litellm.completion(...)`; return a normalized `{content, latency_ms, usage}`.
- [x] ⌨️ **`app/config.py`**: add a `PROVIDER_MODELS` map (provider → default model),
      and `PREFERENCE = {"interactive":[...], "deferrable":[...]}` (per request class).
- [x] ⌨️ **`app/errors.py`**: `classify(exc) -> "rate_limit"|"timeout"|"server_error"|`
      `"auth"|"content_filter"|"unknown"` — map LiteLLM/provider exceptions.
- [x] ⌨️ **`app/main.py`**: require metadata on every request — `tenant`, `feature`,
      `request_id` (read from headers `X-Tenant` / `X-Feature` / `X-Request-Id`).
      Return **400** if any is missing.
- [x] ⌨️ **`app/main.py`**: call the **first** provider from the preference list; return
      its completion in OpenAI shape. (Failover comes in Phase 3 — for now, one provider.)
- [x] ⌨️ **`tests/test_providers.py`**: monkeypatch `litellm.completion`; test happy path,
      test metadata-missing → 400, test each error classification.
- [x] ✅ Point the OpenAI SDK (or `curl`) at `http://localhost:8080/v1` → get a REAL answer.
- [x] ⌨️ `git commit -m "Phase 1: LiteLLM pass-through + required metadata + error taxonomy"`

**Milestone:** a real client gets a real completion through the gateway.

---

## Phase 2 — Per-provider health + /metrics  (3–4 days)
Goal: you can see each provider's success rate and latency live on Grafana.

- [ ] 🧑 Create the Upstash Redis DB; put `REDIS_URL` in `.env`.
- [x] ⌨️ Add `redis` and `prometheus-client` to `requirements.txt`; install.
- [x] ⌨️ **`app/health.py`**: Redis **sorted-set sliding window** per provider
      (score = timestamp). `record(provider, ok, latency_ms, error_type)`;
      `stats(provider) -> {success_rate, p50, p95, p99, error_counts}`; trim old entries.
      (Sliding window in Redis = survives a restart.)
- [x] ⌨️ **`app/metrics.py`**: Prometheus `Counter requests_total{provider}`,
      `Counter errors_total{provider,type}`, `Histogram latency_seconds{provider}`,
      `Gauge provider_up{provider}`.
- [x] ⌨️ **`app/main.py`**: after each provider call → `health.record(...)` + update metrics.
- [x] ⌨️ **`app/main.py`**: add `GET /metrics` → `prometheus_client.generate_latest()`.
- [ ] 🧑 Run Prometheus (local binary) with a scrape job for `localhost:8080/metrics`.
- [ ] 🧑 Run Grafana (local binary), add Prometheus datasource.
- [x] ⌨️ Build 2 Grafana panels: per-provider **latency (p95)** and **error rate**.
- [x] ⌨️ **`tests/test_health.py`**: use `fakeredis`; test record + the percentile math.
- [ ] ✅ Send some traffic → watch the panels move.
- [x] ⌨️ `git commit -m "Phase 2: Redis health window + Prometheus /metrics + Grafana"`

**Milestone:** Grafana shows live per-provider health. (Instrument BEFORE failover.)

---

## Phase 3 — Circuit breaker + failover + self-heal  (4–5 days) ← the heart
Goal: kill a provider and traffic keeps flowing; it heals on its own.

- [x] ⌨️ **`app/breaker.py`**: `CircuitBreaker` per provider, states
      `closed / open / half_open`.
      - trip `closed→open` when error-rate > threshold OR p95 > budget in the window
      - `open→half_open` after a cooldown
      - `half_open` probe: success → `closed`, failure → `open`
- [x] ⌨️ **`app/router.py`**: `route(request_class, messages) ->` pick the first provider
      in the preference list whose breaker is **not open**; on failure, move to the next;
      emit a **failover event** metric.
- [x] ⌨️ Half-open probe: route a small % of traffic to a recovering provider.
- [x] ⌨️ Hedged requests (only for the latency-sensitive class): fire a 2nd provider after
      `HEDGE_MS`, take whichever returns first, cancel the loser. (Note: ~2× spend on hedged calls.)
- [x] ⌨️ **`app/config.py`**: thresholds, window size, cooldown, `HEDGE_MS`, preference lists.
- [x] ⌨️ **`app/main.py`**: call `router.route(...)` instead of a single provider.
- [x] ⌨️ **`tests/test_breaker.py`**: force failures → breaker opens → next provider used;
      after cooldown → half-open → success closes it; hedge picks the faster mock.
- [x] ✅ Manually make one provider throw → requests still succeed via the next.
- [x] ⌨️ `git commit -m "Phase 3: circuit breaker + failover + half-open + hedging"`

**Milestone:** degrade a provider → traffic keeps flowing → it recovers automatically.

---

## Phase 4 — Queue + retry deferrable work  (2–3 days)
Goal: when everything is degraded, deferrable jobs survive instead of erroring.

- [x] ⌨️ Classify **interactive vs deferrable** at the API boundary (header `X-Class`).
- [x] ⌨️ **`app/queue.py`**: when all breakers open, push deferrable jobs to a Redis
      list/stream; a worker consumes with **exponential backoff + jitter**.
- [x] ⌨️ **Idempotency key** per job (`X-Idempotency-Key`) → dedup so a retry can't
      duplicate a side effect.
- [x] ⌨️ Interactive requests **fail fast** (503) when all providers are open.
- [x] ⌨️ Worker entrypoint (`python -m app.worker`) or a background task.
- [x] ⌨️ **`tests/test_queue.py`**: all-degraded → job enqueued; backoff schedule; idempotency dedup.
- [x] ✅ Take all providers down → fire a deferrable job → it drains when a provider returns.
- [x] ⌨️ `git commit -m "Phase 4: deferrable queue + backoff + idempotency"`

**Milestone:** deferrable work survives a full outage; no duplicated side effects.

---

## Phase 5 — Dashboard, chaos, demo & the NUMBER  (3–4 days)
Goal: the portfolio artifact — a live dashboard, a chaos demo, and an availability number.

- [x] ⌨️ **`app/cost.py`**: token→$ per provider/model; attribute by tenant/feature;
      expose `cost_usd_total{tenant,feature}` metric. (Cost attribution = the point of a gateway.)
- [x] ⌨️ Grafana panels: RPS by provider · error rate · p95 · **circuit-state timeline** ·
      failover events · queue depth · **cost/hour by tenant & feature**.
- [x] ⌨️ **`app/main.py`**: `POST /admin/chaos` → inject errors/latency into one provider on demand.
- [x] ⌨️ **`bench/outage_test.py`**: drive steady traffic while chaos degrades a provider;
      compute **availability %** = successful responses / total.
- [x] ⌨️ Export the dashboard → **`dashboards/gateway.json`**.
- [ ] 🧑 Record a **90-sec screen capture**: healthy traffic → degrade a provider →
      breaker trips → traffic reroutes → breaker closes on recovery.
- [ ] ⌨️ **`README.md`**: put the video + the availability number at the very TOP,
      architecture second, setup last.
- [x] ⌨️ `git commit -m "Phase 5: cost attribution + dashboards + chaos + availability benchmark"`

**Milestone / Done-when:** you can degrade a provider live while traffic flows, and the
dashboard shows the trip → reroute → recovery, with an availability % and a per-tenant cost panel.

---

## Finale — connect it to Nocturne (the cohesion move)
- [x] ⌨️ In **Nocturne's `.env`**: `OPENAI_BASE_URL=http://localhost:8080/v1`
- [x] ✅ Nocturne's LLM calls now flow through the gateway → the two projects reference each other.
- [x] ⌨️ Mention this link in both READMEs.

**Resume line:** *Built an LLM gateway with per-provider circuit breaking and automatic
failover; sustained X% availability through simulated provider outages, with per-tenant
cost attribution.*

---

## Reuse from Nocturne (head-start)
`Nocturne/src/llm.py` already has provider rotation + failover. Port that logic as the
starting point for `app/providers.py` and `app/router.py` — you are not starting from zero.

## Four ways this usually goes wrong (avoid)
1. No cost attribution → removes the reason to build a gateway. (Metadata from Phase 1.)
2. Retrying non-idempotent calls → a blip becomes duplicated side effects. (Phase 4 keys.)
3. No half-open state → the breaker opens once and never heals. (Phase 3.)
4. A dashboard with no chaos test → failover that was never proven. (Phase 5.)
