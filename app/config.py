"""Central settings for the gateway.

Phase 0 keeps this tiny: just load .env and name the providers. Provider→model
maps, preference lists per request class, and circuit-breaker thresholds arrive
in later phases (see CHECKLIST.md).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # read a local .env if present (never committed)

# Providers the gateway can route to. Real keys are read per-provider in Phase 1.
PROVIDERS: list[str] = ["groq", "gemini", "openrouter"]

# Redis (Upstash) — used from Phase 2 for the health sliding-window. Empty for now.
REDIS_URL: str = os.getenv("REDIS_URL", "")
