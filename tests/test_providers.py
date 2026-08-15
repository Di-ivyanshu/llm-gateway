"""Provider adapter + error taxonomy — offline."""
import pytest

from app import chaos, config, errors, providers

from tests.helpers import FakeResponse

MESSAGES = [{"role": "user", "content": "x"}]


def test_call_normalizes_the_provider_response(monkeypatch):
    monkeypatch.setattr(providers, "_completion", lambda **kw: FakeResponse("  hi  "))
    out = providers.call("groq", MESSAGES)
    assert out["provider"] == "groq"
    assert out["model"] == config.PROVIDER_MODELS["groq"]
    assert out["content"] == "hi"
    assert out["usage"]["prompt_tokens"] == 3
    assert out["latency_ms"] >= 0


def test_call_passes_the_right_model_and_key(monkeypatch):
    seen = {}

    def stub(**kwargs):
        seen.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(providers, "_completion", stub)
    providers.call("gemini", MESSAGES, temperature=0.7, max_tokens=64)
    assert seen["model"] == config.PROVIDER_MODELS["gemini"]
    assert seen["api_key"] == "test-key"
    assert seen["temperature"] == 0.7
    assert seen["max_tokens"] == 64


def test_failure_is_wrapped_with_a_classified_type(monkeypatch):
    def boom(**kw):
        raise type("RateLimitError", (Exception,), {})("slow down")

    monkeypatch.setattr(providers, "_completion", boom)
    with pytest.raises(providers.ProviderError) as exc:
        providers.call("groq", MESSAGES)
    assert exc.value.error_type == "rate_limit"
    assert exc.value.provider == "groq"


def test_missing_key_is_an_auth_error(monkeypatch):
    monkeypatch.setitem(config.PROVIDER_KEYS, "groq", "")
    with pytest.raises(providers.ProviderError) as exc:
        providers.call("groq", MESSAGES)
    assert exc.value.error_type == "auth"


def test_unknown_provider_is_rejected():
    with pytest.raises(providers.ProviderError):
        providers.call("mystery-llm", MESSAGES)


def test_fake_mode_needs_no_key_and_no_network(monkeypatch):
    monkeypatch.setattr(config, "FAKE_PROVIDERS", True)
    monkeypatch.setattr(config, "FAKE_LATENCY_MS", 0)
    monkeypatch.setitem(config.PROVIDER_KEYS, "groq", "")
    monkeypatch.setattr(providers, "_completion", lambda **kw: pytest.fail("network!"))
    out = providers.call("groq", [{"role": "user", "content": "ping"}])
    assert "ping" in out["content"]
    assert out["usage"]["total_tokens"] > 0


def test_chaos_error_surfaces_as_a_provider_error(monkeypatch):
    chaos.inject("groq", mode="error", error_type="rate_limit")
    with pytest.raises(providers.ProviderError) as exc:
        providers.call("groq", MESSAGES)
    assert exc.value.error_type == "rate_limit"


def test_chaos_rate_zero_never_fires():
    chaos.inject("groq", mode="error", rate=0.0)
    assert providers.call("groq", MESSAGES)["provider"] == "groq"


# --- error taxonomy ----------------------------------------------------------

def _exc(name, message="", **attrs):
    return type(name, (Exception,), attrs)(message)


@pytest.mark.parametrize(
    "exc,expected",
    [
        (_exc("RateLimitError", "slow down"), "rate_limit"),
        (_exc("APITimeoutError", "..."), "timeout"),
        (_exc("AuthenticationError", "..."), "auth"),
        (_exc("ContentPolicyViolationError", "..."), "content_filter"),
        (_exc("InternalServerError", "..."), "server_error"),
        (_exc("Whatever", "429 Too Many Requests"), "rate_limit"),
        (_exc("Whatever", "request timed out"), "timeout"),
        (_exc("Whatever", "invalid api key"), "auth"),
        (_exc("Whatever", "blocked by the safety filter"), "content_filter"),
        (_exc("Whatever", "connection error"), "server_error"),
        (_exc("Whatever", "boom", status_code=503), "server_error"),
        (_exc("Whatever", "boom", status_code=429), "rate_limit"),
        (_exc("Whatever", "something odd"), "unknown"),
    ],
)
def test_classify(exc, expected):
    assert errors.classify(exc) == expected
