# The 90-second demo (recording script)

Goal of the clip: **healthy traffic → a provider degrades → the breaker trips →
traffic reroutes → the breaker heals**, with the Grafana panels moving while it
happens.

Everything below runs with **fake providers**, so the demo costs nothing and
behaves identically every time. Nothing here needs a real API key.

---

## Setup (before you hit record)

Four terminals + a browser.

**1 — gateway** (fake providers, short cooldown so recovery fits in the clip):

```powershell
cd D:\llm-gateway
.venv\Scripts\activate
$env:GATEWAY_FAKE_PROVIDERS = "1"
$env:GATEWAY_FAKE_LATENCY_MS = "120"
$env:BREAKER_COOLDOWN_S = "10"
$env:HEALTH_WINDOW_S = "60"
python -m uvicorn app.main:app --port 8080
```

**2 — worker** (drains anything queued during the total outage):

```powershell
cd D:\llm-gateway
.venv\Scripts\activate
$env:GATEWAY_FAKE_PROVIDERS = "1"
python -m app.worker
```

**3 — Prometheus:**

```powershell
.\prometheus.exe --config.file=D:\llm-gateway\ops\prometheus.yml
```

**4 — Grafana:** start `grafana-server.exe`, open http://localhost:3000, add the
Prometheus datasource (`http://localhost:9090`), then import
`dashboards/gateway.json`. Set the time range to **Last 15 minutes**, refresh
**5s**, and put the **Circuit state per provider** panel where the camera can
see it.

---

## The clip

| t | You do | What the viewer sees |
| --- | --- | --- |
| 0:00 | Start traffic (terminal 4, below) | RPS climbs, all requests served by **groq**, availability 100% |
| 0:15 | Groq starts failing | error rate for groq spikes, circuit-state row turns **red (open)** |
| 0:20 | — | RPS by provider shows **gemini** picking it all up, availability stays 100% |
| 0:35 | Gemini fails too | second row goes red, **openrouter** carries the traffic |
| 0:50 | Everything fails, deferrable traffic | client outcomes shows **queued**, queue depth rises — no errors reach the caller |
| 1:00 | Chaos cleared | rows go **orange (half-open)** then **green (closed)** on their own |
| 1:15 | Worker drains | queue depth falls back to 0 |
| 1:25 | Zoom on the cost panel | spend per **tenant × feature** |

Terminal 4 — one command drives the whole timeline (traffic + chaos):

```powershell
cd D:\llm-gateway
.venv\Scripts\activate
python -m bench.outage_test --url http://localhost:8080 --rate 20
```

It prints the availability number at the end — that is the number for the README.

---

## Driving it by hand instead

If you want to narrate each step yourself:

```powershell
# steady traffic in one terminal
while ($true) {
  Invoke-RestMethod -Method Post http://localhost:8080/v1/chat/completions `
    -Headers @{ "X-Tenant"="acme"; "X-Feature"="chat"; "X-Request-Id"=[guid]::NewGuid() } `
    -ContentType "application/json" `
    -Body '{"model":"gateway-auto","messages":[{"role":"user","content":"hi"}]}' |
    Select-Object -ExpandProperty gateway | Select-Object provider, failovers
  Start-Sleep -Milliseconds 200
}
```

```powershell
# break groq
Invoke-RestMethod -Method Post http://localhost:8080/admin/chaos -ContentType "application/json" `
  -Body '{"provider":"groq","mode":"error","error_type":"server_error"}'

# make gemini slow instead of broken
Invoke-RestMethod -Method Post http://localhost:8080/admin/chaos -ContentType "application/json" `
  -Body '{"provider":"gemini","mode":"latency","latency_ms":9000}'

# watch the breakers
Invoke-RestMethod http://localhost:8080/admin/status | ConvertTo-Json -Depth 5

# heal everything
Invoke-RestMethod -Method Post http://localhost:8080/admin/reset
```

Queued work during a full outage:

```powershell
$r = Invoke-RestMethod -Method Post http://localhost:8080/v1/chat/completions `
  -Headers @{ "X-Tenant"="acme"; "X-Feature"="nightly"; "X-Request-Id"="job-1"; "X-Class"="deferrable" } `
  -ContentType "application/json" `
  -Body '{"model":"gateway-auto","messages":[{"role":"user","content":"summarise"}]}'
$r.job_id
Invoke-RestMethod "http://localhost:8080/v1/jobs/$($r.job_id)"   # queued → done once the worker drains it
```

---

## After recording

1. Trim to ~90 seconds.
2. Put the link at the very top of `README.md` (replace the TODO line).
3. Re-run `python -m bench.outage_test` and paste the fresh availability number
   next to it — the number and the video should tell the same story.
