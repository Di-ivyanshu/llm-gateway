"""Steady demo traffic — what you run while recording the dashboard.

    python -m bench.traffic                    # ~4 req/s, mixed tenants
    python -m bench.traffic --rate 10 --url http://localhost:8080

It sends a realistic mix: two tenants, a few features, and some deferrable work,
so the dashboard's cost-by-tenant and queue panels have something to show. The
gateway should be running with GATEWAY_FAKE_PROVIDERS=1 for a demo, or with real
keys if you want real answers (and real quota burn).
"""
from __future__ import annotations

import argparse
import itertools
import random
import time

import httpx

# (tenant, feature, class) — the mix that makes the dashboard legible
MIX = [
    ("acme", "chat", "interactive"),
    ("acme", "chat", "interactive"),
    ("acme", "summarise", "interactive"),
    ("globex", "chat", "interactive"),
    ("globex", "nightly-report", "deferrable"),
]
PROMPTS = [
    "What is our refund policy?",
    "Summarise this ticket in one line.",
    "Draft a reply to the customer.",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--rate", type=float, default=4, help="requests per second")
    args = parser.parse_args()

    gap = 1 / args.rate
    client = httpx.Client(timeout=60)
    print(f"sending ~{args.rate}/s to {args.url} — Ctrl+C to stop")

    for i in itertools.count(1):
        tenant, feature, request_class = random.choice(MIX)
        started = time.perf_counter()
        try:
            r = client.post(
                f"{args.url}/v1/chat/completions",
                json={
                    "model": "gateway-auto",
                    "messages": [{"role": "user", "content": random.choice(PROMPTS)}],
                },
                headers={
                    "X-Tenant": tenant,
                    "X-Feature": feature,
                    "X-Class": request_class,
                    "X-Request-Id": f"demo-{i}",
                },
            )
            mark = {200: ".", 202: "q", 503: "!"}.get(r.status_code, "?")
        except Exception:  # noqa: BLE001 — a dead gateway is just a failed tick
            mark = "x"
        print(mark, end="", flush=True)
        if i % 80 == 0:
            print()
        time.sleep(max(0.0, gap - (time.perf_counter() - started)))


if __name__ == "__main__":
    main()
