"""One place that actually talks to a provider, via LiteLLM.

`call()` is deliberately dumb: one provider, one attempt, no retries and no
failover — the router owns those decisions. It returns a normalized dict so
nothing above this module has to know a provider's response shape.

Two escape hatches live here:
  * chaos faults are applied first, so a degraded provider fails exactly where a
    real one would;
  * `GATEWAY_FAKE_PROVIDERS=1` returns canned completions, which is how the
    benchmark and the demo run without spending real quota.

litellm is imported lazily inside `_completion` (importing it costs seconds);
tests monkeypatch `_completion` and stay fully offline.
"""
from __future__ import annotations

import time
from typing import Any

from . import chaos, config
from .errors import classify


class ProviderError(RuntimeError):
    """A provider call failed. `error_type` is an app.errors label."""

    def __init__(self, provider: str, error_type: str, message: str) -> None:
        super().__init__(f"{provider}: {error_type}: {message}")
        self.provider = provider
        self.error_type = error_type
        self.message = message


def _completion(**kwargs: Any) -> Any:
    """Thin seam over litellm.completion so tests can replace it."""
    import litellm

    return litellm.completion(**kwargs)


class _FakeResponse:
    """Canned completion used when GATEWAY_FAKE_PROVIDERS=1."""

    def __init__(self, provider: str, messages: list[dict[str, str]]) -> None:
        last = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        self.choices = [{"message": {"content": f"[{provider} fake] {last}"}}]
        prompt_tokens = max(1, sum(len(m.get("content", "")) for m in messages) // 4)
        completion_tokens = max(1, len(last) // 4)
        self.usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }


def _usage(resp: Any) -> dict[str, int]:
    """Pull token counts out of a LiteLLM response (dict or object)."""
    raw = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
    if raw is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    get = raw.get if isinstance(raw, dict) else lambda k, d=0: getattr(raw, k, d)  # noqa: E731
    return {
        "prompt_tokens": int(get("prompt_tokens", 0) or 0),
        "completion_tokens": int(get("completion_tokens", 0) or 0),
        "total_tokens": int(get("total_tokens", 0) or 0),
    }


def _content(resp: Any) -> str:
    """Pull choices[0].message.content out of a LiteLLM response."""
    choices = resp["choices"] if isinstance(resp, dict) else resp.choices
    first = choices[0]
    message = first["message"] if isinstance(first, dict) else first.message
    text = message["content"] if isinstance(message, dict) else message.content
    return (text or "").strip()


def call(
    provider: str,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Call one provider once.

    Returns `{provider, model, content, latency_ms, usage}`.
    Raises `ProviderError` (with a classified `error_type`) on any failure.
    """
    if provider not in config.PROVIDER_MODELS:
        raise ProviderError(provider, "unknown", "provider not configured")

    model = model or config.PROVIDER_MODELS[provider]
    started = time.perf_counter()
    try:
        chaos.apply(provider)  # no-op unless /admin/chaos degraded this provider

        if config.FAKE_PROVIDERS:
            time.sleep(config.FAKE_LATENCY_MS / 1000)
            resp: Any = _FakeResponse(provider, messages)
        else:
            api_key = config.PROVIDER_KEYS.get(provider, "")
            if not api_key:
                raise ProviderError(provider, "auth", "no API key configured")
            resp = _completion(
                model=model,
                messages=messages,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout if timeout is not None else config.REQUEST_TIMEOUT_S,
                **config.PROVIDER_EXTRA_PARAMS.get(provider, {}),
            )
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 — every failure becomes a labelled one
        raise ProviderError(provider, classify(exc), str(exc)[:300]) from exc

    return {
        "provider": provider,
        "model": model,
        "content": _content(resp),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "usage": _usage(resp),
    }
