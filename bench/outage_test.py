"""Availability benchmark — drive steady traffic through a provider outage.

    python -m bench.outage_test                     # in-process, ~25s, no keys, no server
    python -m bench.outage_test --url http://localhost:8080   # against a running gateway
                                                              # (watch Grafana while it runs)

Providers are faked (`GATEWAY_FAKE_PROVIDERS=1`), so this costs nothing and is
reproducible — the outages come from `/admin/chaos`, not from a real provider
having a bad day.

The number this produces is the one on the README: what fraction of client
requests were answered while providers were failing, versus what a client
pinned to a single provider would have got.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import Counter
from pathlib import Path

# Must be set before `app.config` is imported.
os.environ.setdefault("GATEWAY_FAKE_PROVIDERS", "1")
os.environ.setdefault("GATEWAY_FAKE_LATENCY_MS", "5")
os.environ.setdefault("BREAKER_COOLDOWN_S", "5")     # so recovery fits in a short run
os.environ.setdefault("HEALTH_WINDOW_S", "60")

HEADERS = {"X-Tenant": "bench", "X-Feature": "outage-test", "X-Request-Id": "bench"}
BODY = {"model": "gateway-auto", "messages": [{"role": "user", "content": "ping"}]}

OFF = [{"provider": p, "mode": "off"} for p in ("groq", "gemini", "openrouter")]

# The timeline. `cls` is the X-Class the phase's traffic is sent with.
PHASES = [
    {"label": "healthy", "seconds": 3.0, "chaos": OFF, "cls": "interactive"},
    {"label": "groq down", "seconds": 6.0, "cls": "interactive", "chaos": [
        {"provider": "groq", "mode": "error", "error_type": "server_error"}]},
    {"label": "groq + gemini down", "seconds": 4.0, "cls": "interactive", "chaos": [
        {"provider": "gemini", "mode": "error", "error_type": "rate_limit"}]},
    # Everything is down and the work is deferrable: it must be queued, not lost.
    {"label": "total outage (deferrable)", "seconds": 3.0, "cls": "deferrable", "chaos": [
        {"provider": "openrouter", "mode": "error", "error_type": "server_error"}]},
    {"label": "recovering", "seconds": 9.0, "chaos": OFF, "cls": "interactive"},
]


class InProcess:
    """Drive the app directly — no server, no ports."""

    def __init__(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)

    def chat(self, request_id: str, request_class: str):
        return self.client.post(
            "/v1/chat/completions",
            json=BODY,
            headers={**HEADERS, "X-Request-Id": request_id, "X-Class": request_class},
        )

    def admin(self, path: str, payload: dict | None = None):
        return self.client.post(path, json=payload or {})


class OverHttp:
    """Drive a gateway that is already running (so Grafana sees the traffic)."""

    def __init__(self, base_url: str) -> None:
        import httpx

        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30)

    def chat(self, request_id: str, request_class: str):
        return self.client.post(
            f"{self.base}/v1/chat/completions",
            json=BODY,
            headers={**HEADERS, "X-Request-Id": request_id, "X-Class": request_class},
        )

    def admin(self, path: str, payload: dict | None = None):
        return self.client.post(f"{self.base}{path}", json=payload or {})


def run(driver, rate_per_s: float) -> list[dict]:
    """Fire requests at a steady rate through every phase. Returns per-request rows."""
    driver.admin("/admin/reset")
    gap = 1 / rate_per_s
    rows: list[dict] = []
    index = 0

    for phase in PHASES:
        for command in phase["chaos"]:
            driver.admin("/admin/chaos", command)
        print(f"  phase: {phase['label']:<26} ({phase['seconds']:.0f}s, {phase['cls']})")

        phase_end = time.time() + phase["seconds"]
        while time.time() < phase_end:
            index += 1
            started = time.perf_counter()
            try:
                response = driver.chat(f"bench-{index}", phase["cls"])
                status = response.status_code
                provider = response.headers.get("X-Gateway-Provider", "-")
            except Exception as exc:  # noqa: BLE001 — a dead gateway is a failed request
                status, provider = 0, f"error:{type(exc).__name__}"
            rows.append({
                "phase": phase["label"],
                "status": status,
                # 200 = answered now, 202 = accepted and queued. Both mean the
                # caller did not lose its work; only an error is a lost request.
                "ok": status in (200, 202),
                "queued": status == 202,
                "provider": provider if status == 200 else ("queued" if status == 202 else "-"),
                "latency_ms": (time.perf_counter() - started) * 1000,
                "t": time.time(),
            })
            time.sleep(max(0.0, gap - (time.perf_counter() - started)))
    return rows


def summarise(rows: list[dict]) -> dict:
    """Availability overall and per phase, plus what a single provider would have given."""
    total = len(rows)
    ok = sum(1 for r in rows if r["ok"])
    degraded = [r for r in rows if r["phase"] != "healthy"]
    # A client pinned to Groq only succeeds when Groq itself answered.
    groq_only_ok = sum(1 for r in rows if r["provider"] == "groq")

    per_phase = {}
    for phase in PHASES:
        label = phase["label"]
        phase_rows = [r for r in rows if r["phase"] == label]
        if not phase_rows:
            continue
        per_phase[label] = {
            "requests": len(phase_rows),
            "availability": sum(1 for r in phase_rows if r["ok"]) / len(phase_rows),
            "providers": dict(Counter(r["provider"] for r in phase_rows)),
            "p95_ms": round(
                statistics.quantiles([r["latency_ms"] for r in phase_rows], n=20)[18], 1
            ) if len(phase_rows) > 20 else None,
        }

    return {
        "requests": total,
        "availability": ok / total if total else 0.0,
        "answered_immediately": sum(1 for r in rows if r["status"] == 200),
        "queued_during_total_outage": sum(1 for r in rows if r["queued"]),
        "availability_while_degraded": (
            sum(1 for r in degraded if r["ok"]) / len(degraded) if degraded else 0.0
        ),
        "single_provider_availability": groq_only_ok / total if total else 0.0,
        "failed": total - ok,
        "providers": dict(Counter(r["provider"] for r in rows)),
        "per_phase": per_phase,
    }


def report(summary: dict) -> str:
    lines = [
        "# Availability under a provider outage",
        "",
        "Generated by `python -m bench.outage_test` (fake providers, chaos-injected outages).",
        "",
        f"- **Requests:** {summary['requests']}",
        f"- **Availability (whole run): {summary['availability'] * 100:.2f}%**",
        f"- Availability while providers were degraded: "
        f"{summary['availability_while_degraded'] * 100:.2f}%",
        f"- Same traffic pinned to a single provider (Groq only): "
        f"{summary['single_provider_availability'] * 100:.2f}%",
        f"- Answered immediately: {summary['answered_immediately']} · "
        f"accepted into the queue during the total outage: "
        f"{summary['queued_during_total_outage']}",
        f"- Lost requests (an error reached the caller): {summary['failed']}",
        "",
        "\"Available\" = the caller did not lose its work: a 200 answered now, or a 202 "
        "accepted into the deferrable queue while every provider was down. Queued jobs are "
        "drained by `python -m app.worker` once a provider returns.",
        "",
        "| phase | requests | availability | providers that answered |",
        "| --- | ---: | ---: | --- |",
    ]
    for label, info in summary["per_phase"].items():
        mix = ", ".join(f"{k} {v}" for k, v in sorted(info["providers"].items()))
        lines.append(
            f"| {label} | {info['requests']} | {info['availability'] * 100:.1f}% | {mix} |"
        )
    lines += ["", "Raw counts: " + json.dumps(summary["providers"]), ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="drive a running gateway instead of an in-process app")
    parser.add_argument("--rate", type=float, default=40, help="requests per second")
    parser.add_argument("--out", default="bench/results.md")
    args = parser.parse_args()

    driver = OverHttp(args.url) if args.url else InProcess()
    print(f"running outage benchmark ({'http ' + args.url if args.url else 'in-process'})")
    rows = run(driver, args.rate)
    summary = summarise(rows)

    driver.admin("/admin/reset")

    text = report(summary)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    Path(args.out).with_suffix(".json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print("\n" + text)
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
