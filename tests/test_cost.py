"""Cost attribution."""
from app import config, cost, metrics


def _counter(tenant, feature, provider):
    return metrics.COST.labels(tenant=tenant, feature=feature, provider=provider)._value.get()


def test_price_lookup_falls_back_to_free():
    assert cost.price("groq/llama-3.3-70b-versatile") == (0.59, 0.79)
    assert cost.price("some/model-nobody-priced") == config.DEFAULT_PRICE


def test_price_tolerates_a_missing_provider_prefix():
    assert cost.price("llama-3.3-70b-versatile") == (0.59, 0.79)


def test_estimate_uses_input_and_output_rates():
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    assert cost.estimate("groq/llama-3.3-70b-versatile", usage) == 0.59 + 0.79


def test_free_models_cost_nothing():
    usage = {"prompt_tokens": 5000, "completion_tokens": 5000}
    assert cost.estimate(
        "openrouter/meta-llama/llama-3.3-70b-instruct:free", usage
    ) == 0.0


def test_record_books_spend_against_the_tenant():
    before = _counter("acme", "chat", "groq")
    usd = cost.record(
        "acme", "chat", "groq", "groq/llama-3.3-70b-versatile",
        {"prompt_tokens": 1000, "completion_tokens": 1000},
    )
    assert usd > 0
    assert _counter("acme", "chat", "groq") == before + usd


def test_tokens_are_split_by_kind():
    cost.record(
        "beta", "search", "gemini", "gemini/gemini-2.5-flash-lite",
        {"prompt_tokens": 40, "completion_tokens": 10},
    )
    prompt = metrics.TOKENS.labels(
        provider="gemini", tenant="beta", feature="search", kind="prompt"
    )._value.get()
    completion = metrics.TOKENS.labels(
        provider="gemini", tenant="beta", feature="search", kind="completion"
    )._value.get()
    assert (prompt, completion) == (40, 10)
