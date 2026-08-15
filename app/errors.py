"""Error taxonomy — turn any provider/LiteLLM exception into one short label.

Phase 1 only needs the label for logging and the API response. Phases 2-3 feed
the same label into the health window and the circuit breaker, so keep the set
small and stable: rate_limit / timeout / server_error / auth / content_filter /
unknown.

Deliberately does NOT import litellm — classification works off the exception's
class name, its status code, and its message, so tests stay offline and fast.
"""
from __future__ import annotations

# Exception class names (LiteLLM mirrors the OpenAI SDK names) → our label.
_BY_CLASS_NAME: dict[str, str] = {
    "RateLimitError": "rate_limit",
    "Timeout": "timeout",
    "APITimeoutError": "timeout",
    "ReadTimeout": "timeout",
    "ConnectTimeout": "timeout",
    "AuthenticationError": "auth",
    "PermissionDeniedError": "auth",
    "ContentPolicyViolationError": "content_filter",
    "InternalServerError": "server_error",
    "ServiceUnavailableError": "server_error",
    "APIConnectionError": "server_error",
    "APIError": "server_error",
}

_BY_STATUS: dict[int, str] = {
    401: "auth",
    403: "auth",
    408: "timeout",
    429: "rate_limit",
    500: "server_error",
    502: "server_error",
    503: "server_error",
    504: "server_error",
}

# Checked in order — first hit wins, so put the specific words first.
_BY_KEYWORD: list[tuple[tuple[str, ...], str]] = [
    (("rate limit", "ratelimit", "too many requests", "quota", "resource_exhausted", "429"), "rate_limit"),
    (("timed out", "timeout", "deadline exceeded"), "timeout"),
    (("api key", "unauthorized", "invalid_api_key", "authentication", "401", "403"), "auth"),
    (("content_filter", "content policy", "safety", "blocked by"), "content_filter"),
    (("connection", "internal server error", "bad gateway", "unavailable", "500", "502", "503"), "server_error"),
]


def classify(exc: BaseException) -> str:
    """Map an exception to one of the six error labels."""
    label = _BY_CLASS_NAME.get(type(exc).__name__)
    if label:
        return label

    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, str) and status.isdigit():
        status = int(status)
    if isinstance(status, int):
        label = _BY_STATUS.get(status)
        if label:
            return label
        if status >= 500:
            return "server_error"

    text = str(exc).lower()
    for words, name in _BY_KEYWORD:
        if any(w in text for w in words):
            return name
    return "unknown"
