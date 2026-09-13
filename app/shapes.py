"""The OpenAI response shape, in one place.

Both the HTTP path (`app.main`) and the queue worker (`app.worker`) hand results
back to callers, and both must produce byte-identical bodies — a client should
not be able to tell whether its answer came straight through or via the queue.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterator
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


def _event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def completion_chunks(body: dict[str, Any]) -> Iterator[str]:
    """Re-emit a finished completion as OpenAI `chat.completion.chunk` SSE events.

    The gateway **buffers**: it routes the request to completion first — failing
    over if it has to — and only then streams the text out. Real token-by-token
    passthrough would mean committing to a provider with the first byte, and you
    cannot fail over mid-stream. Clients see the same wire format either way.
    """
    head = {
        "id": body["id"],
        "object": "chat.completion.chunk",
        "created": body["created"],
        "model": body["model"],
    }

    def frame(delta: dict[str, Any], finish: str | None) -> str:
        return _event(
            {**head, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        )

    yield frame({"role": "assistant"}, None)
    content = body["choices"][0]["message"]["content"]
    for piece in re.findall(r"\S+\s*", content):  # word + its trailing space
        yield frame({"content": piece}, None)
    yield frame({}, "stop")
    yield "data: [DONE]\n\n"
