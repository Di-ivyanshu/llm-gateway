"""Shared test setup.

Two invariants for the whole suite:

* **No network, ever.** `providers._completion` is stubbed by default, so a test
  that forgets to stub it still cannot spend real provider quota.
* **No shared state between tests.** The health window, the queue, breaker state
  and chaos faults are all reset before each test.
"""
from __future__ import annotations

import pytest

from app import breaker, chaos, config, health, providers, queue, recent
from tests.helpers import FakeResponse


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    health.set_backend(health.MemoryBackend())
    queue.set_backend(queue.MemoryBackend())
    breaker.reset()
    chaos.clear()
    recent.clear()
    for provider in config.PROVIDERS:
        monkeypatch.setitem(config.PROVIDER_KEYS, provider, "test-key")
    monkeypatch.setattr(config, "HEDGE_MS", 0)          # opt-in per test
    monkeypatch.setattr(config, "FAKE_PROVIDERS", False)
    monkeypatch.setattr(providers, "_completion", lambda **kw: FakeResponse())
    yield
    breaker.reset()
    chaos.clear()
    recent.clear()
