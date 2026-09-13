# The 90-second demo (recording script)

The story the clip tells: **healthy traffic → a provider breaks → the breaker
trips → traffic reroutes → everything breaks → work is queued instead of lost →
providers return → the breaker heals itself.**

Everything runs with **fake providers**, so it costs nothing, never touches your
quota, and behaves the same every take. Nothing to install — the dashboard is
built into the gateway.

---

## Setup — two terminals and a browser tab

**Terminal 1 — the gateway.** Short cooldown so recovery fits inside the clip:

```powershell
cd D:\llm-gateway
.venv\Scripts\activate
$env:GATEWAY_FAKE_PROVIDERS = "1"
$env:GATEWAY_FAKE_LATENCY_MS = "120"
$env:BREAKER_COOLDOWN_S = "8"
$env:BREAKER_MIN_SAMPLES = "3"
$env:HEALTH_WINDOW_S = "60"
python -m uvicorn app.main:app --port 8080
```

**Terminal 2 — traffic:**

```powershell
cd D:\llm-gateway
.venv\Scripts\activate
python -m bench.traffic --rate 6
```

**Browser — <http://localhost:8080/dashboard>.** Full screen. Everything you
need to show is on this one page: the tiles, the provider list, the
requests-per-second chart, the live request table, and the chaos buttons in the
header. Hit **reset all** once before recording so the window starts clean.

---

## The clip

| t | You do | What the viewer sees |
| --- | --- | --- |
| 0:00 | nothing — let it run 10s | bars climb in **blue (groq)**, availability 100%, cost ticking up per tenant |
| 0:12 | click **break groq** | the table fills with `groq(server_error) → gemini`, bars turn **orange (gemini)** — availability stays 100% |
| 0:25 | point at the groq row | it flips `✓ closed` → `✕ open`: the gateway stopped even trying, failovers counter climbing |
| 0:35 | click **break gemini** | **aqua (openrouter)** takes over, second row goes red |
| 0:50 | click **break openrouter** | interactive rows go **failed** (honest 503), but `globex / nightly-report` rows go **queued** — queue depth rises instead of losing work |
| 1:05 | click **fix** on all three | rows go `◐ probing` then `✓ closed` **by themselves** — nobody restarted anything |
| 1:15 | — | queue depth drains back to 0 as the parked jobs run |
| 1:25 | scroll the request table | every row shows tenant, feature, route taken, latency, cost |

Say this over the top: *"No client code changed. The app still thinks it is
talking to OpenAI."*

---

## Optional second half — the real product on top of it

If you want to prove it is not a toy, start Nocturne (the RAG project) with its
`.env` pointing at `http://localhost:8080/v1`, ask it a question, and show the
dashboard picking up `tenant=nocturne, feature=rag` rows in real time — then
break a provider mid-answer and show the answer still arriving.

---

## The numbers to say out loud

```powershell
python -m bench.outage_test        # ~25s, prints the availability number
```

> 100% availability across 988 requests through a staged outage; the same
> traffic pinned to a single provider: 28%.

---

## After recording

1. Trim to ~90 seconds.
2. Replace the TODO line at the top of `README.md` with the video link.
3. Re-run `python -m bench.outage_test` and make sure the number you quote
   matches `bench/results.md`.
