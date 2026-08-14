"""LLM Gateway — FastAPI entrypoint.

Phase 0: an OpenAI-compatible skeleton. `/v1/chat/completions` simply echoes the
last user message back in the OpenAI response shape, so any OpenAI client can
already talk to it. Real provider calls (LiteLLM), required metadata, health
tracking, and failover come in Phases 1-3 — see CHECKLIST.md.
"""
from __future__ import annotations

import time

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="llm-gateway", version="0.0.1")


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]


@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest) -> dict:
    """OpenAI-compatible chat endpoint.

    Phase 0 stub: echo the last user message. The response shape matches the
    OpenAI API so clients need no changes when real routing lands in Phase 1.
    """
    last_user = next(
        (m.content for m in reversed(req.messages) if m.role == "user"),
        "",
    )
    return {
        "id": "gw-0",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": f"echo: {last_user}"},
                "finish_reason": "stop",
            }
        ],
    }
