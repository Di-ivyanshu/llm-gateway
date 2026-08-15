"""Central settings for the gateway — every knob lives here.

Nothing else reads `os.environ`, so one look at this file tells you how the
gateway will behave. Anything can be overridden in `.env`.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # read a local .env if present (never committed)


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _b(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


# --- providers ---------------------------------------------------------------
PROVIDERS: list[str] = ["groq", "gemini", "openrouter"]

# Default model per provider (LiteLLM ids: "<provider>/<model>").
PROVIDER_MODELS: dict[str, str] = {
    "groq": os.getenv("GROQ_MODEL", "groq/llama-3.3-70b-versatile"),
    "gemini": os.getenv("GEMINI_MODEL", "gemini/gemini-2.5-flash-lite"),
    "openrouter": os.getenv(
        "OPENROUTER_MODEL", "openrouter/meta-llama/llama-3.3-70b-instruct:free"
    ),
}

# OpenRouter falls back to OPENAI_API_KEY so a .env copied from Nocturne works.
PROVIDER_KEYS: dict[str, str] = {
    "groq": os.getenv("GROQ_API_KEY", ""),
    "gemini": os.getenv("GEMINI_API_KEY", ""),
    "openrouter": os.getenv("OPENROUTER_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""),
}

# Order to try providers in, per request class (header X-Class).
#   interactive — a user is waiting: fastest free tier first (Groq).
#   deferrable  — nobody is waiting: spend the paid OpenRouter allowance first
#                 and keep the free daily caps for interactive traffic.
PREFERENCE: dict[str, list[str]] = {
    "interactive": ["groq", "gemini", "openrouter"],
    "deferrable": ["openrouter", "gemini", "groq"],
}
DEFAULT_CLASS = "interactive"

REQUEST_TIMEOUT_S: float = _f("REQUEST_TIMEOUT_S", 30)

# Return canned completions instead of calling a provider. Used by the chaos
# demo and the availability benchmark so they never burn real quota.
FAKE_PROVIDERS: bool = _b("GATEWAY_FAKE_PROVIDERS")
FAKE_LATENCY_MS: int = _i("GATEWAY_FAKE_LATENCY_MS", 120)

# --- health window (Phase 2) -------------------------------------------------
HEALTH_WINDOW_S: float = _f("HEALTH_WINDOW_S", 300)  # 5 minutes of history

# --- circuit breaker (Phase 3) ----------------------------------------------
BREAKER_MIN_SAMPLES: int = _i("BREAKER_MIN_SAMPLES", 5)      # don't judge on 1 bad call
BREAKER_ERROR_RATE: float = _f("BREAKER_ERROR_RATE", 0.5)    # >50% errors → trip
# Slow counts as broken — but free-tier LLMs are genuinely slow (measured: Groq
# ~5s, Gemini ~10s on a cold call), so the budget sits above that, not at a
# web-service number. Lower it if you route to faster models.
BREAKER_P95_BUDGET_MS: float = _f("BREAKER_P95_BUDGET_MS", 15000)
BREAKER_COOLDOWN_S: float = _f("BREAKER_COOLDOWN_S", 30)     # open → half_open after
BREAKER_HALF_OPEN_PROBES: int = _i("BREAKER_HALF_OPEN_PROBES", 1)  # probes in flight

# --- hedging (Phase 3) — interactive class only ------------------------------
HEDGE_MS: float = _f("HEDGE_MS", 0)  # 0 disables. ~2x spend on hedged calls.

# --- queue (Phase 4) ---------------------------------------------------------
QUEUE_MAX_ATTEMPTS: int = _i("QUEUE_MAX_ATTEMPTS", 5)
QUEUE_BACKOFF_BASE_S: float = _f("QUEUE_BACKOFF_BASE_S", 2)
QUEUE_BACKOFF_MAX_S: float = _f("QUEUE_BACKOFF_MAX_S", 300)
QUEUE_JITTER: float = _f("QUEUE_JITTER", 0.3)          # ±30% of the delay
IDEMPOTENCY_TTL_S: int = _i("IDEMPOTENCY_TTL_S", 86400)
WORKER_POLL_S: float = _f("WORKER_POLL_S", 1.0)

# --- cost (Phase 5) ----------------------------------------------------------
# USD per 1M tokens (input, output). Free tiers are 0 — override in .env when a
# provider starts charging you.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "groq/llama-3.3-70b-versatile": (0.59, 0.79),
    "gemini/gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini/gemini-2.5-flash": (0.30, 2.50),
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": (0.0, 0.0),
    "openrouter/meta-llama/llama-3.3-70b-instruct": (0.12, 0.30),
}
DEFAULT_PRICE: tuple[float, float] = (0.0, 0.0)

# --- infra -------------------------------------------------------------------
REDIS_URL: str = os.getenv("REDIS_URL", "")
ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "")  # empty = /admin/* open locally

# Drain the deferrable queue from inside the API process. Without Redis the queue
# lives in memory, so a separate `python -m app.worker` would be looking at a
# different (empty) queue — hence: on by default when there is no REDIS_URL, off
# when there is one, because then you want real worker processes.
INLINE_WORKER: bool = _b("GATEWAY_INLINE_WORKER", not REDIS_URL)
