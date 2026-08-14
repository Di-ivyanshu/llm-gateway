# LLM Gateway

A self-healing gateway that routes every LLM call through one service — tracking
per-provider health and failing over automatically when a provider degrades, with
per-tenant cost attribution.

> 🚧 Work in progress. Phase 0 (skeleton) is done.

## Status
- [x] **Phase 0** — FastAPI skeleton + OpenAI-compatible echo endpoint + tests
- [ ] **Phase 1** — LiteLLM pass-through + required request metadata
- [ ] **Phase 2** — per-provider health tracking (Redis) + `/metrics`
- [ ] **Phase 3** — circuit breaker + automatic failover + self-healing
- [ ] **Phase 4** — queue + retry for deferrable work
- [ ] **Phase 5** — Grafana dashboard + chaos test + availability benchmark

## Run
```
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8080
```
Then `GET http://localhost:8080/health`, or POST an OpenAI-shaped request to
`/v1/chat/completions`.

## Test
```
pytest -q
```
Tests are offline and need no API keys.

## Stack
Python · FastAPI · LiteLLM · Redis (Upstash) · Prometheus + Grafana ·
Groq / Gemini / OpenRouter. OpenAI-compatible API.
