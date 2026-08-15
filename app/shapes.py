"""The OpenAI response shape, in one place.

Both the HTTP path (`app.main`) and the queue worker (`app.worker`) hand results
back to callers, and both must produce byte-identical bodies — a client should
not be able to tell whether its answer came straight through or via the queue.
"""
from __future__ import annotations

import time
import uuid
from typing import Any


def completion_body(result: dict[str, Any]) -> dict[str, Any]:
    """Wrap a router result in the OpenAI chat-completion shape.

    The extra `gateway` block is additive, so OpenAI clients ignore it while a
    curious operator can see which provider answered and what it cost.
    """
    return {
        "id": f"gw-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": result["model"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result["content"]},
                "finish_reason": "stop",
            }
        ],
        "usage": result["usage"],
        "gateway": {
            "provider": result["provider"],
            "latency_ms": result["latency_ms"],
            "failovers": result["failovers"],
            "hedged": result["hedged"],
            "cost_usd": round(result["cost_usd"], 8),
            "attempts": result["attempts"],
        },
    }
