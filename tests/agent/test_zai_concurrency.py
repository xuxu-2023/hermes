"""Tests for the Z.AI process-local concurrency gate.

Covers: detection, non-Z.AI pass-through, real capping under concurrency,
the env disable knob, and non-fatal behavior when the gate is starved.
These are unit tests against ``agent.zai_concurrency`` in isolation — they
do not exercise the streaming client.
"""

import threading
import time

import pytest

from agent import zai_concurrency
from agent.zai_concurrency import (
    acquire_zai_slot,
    configured_max_concurrent,
    is_zai_request,
)


def _reset(max_concurrent, timeout=30.0):
    """Rebuild the module-level gate with explicit settings."""
    zai_concurrency._reset_for_tests(max_concurrent, timeout)


@pytest.fixture(autouse=True)
def _stable_gate():
    # Default to a small cap for every test; tests that need a different
    # value call _reset() themselves.
    _reset(4)
    yield
    _reset(4)


class TestDetection:
    """``is_zai_request`` must key on host/provider, never the bare model."""

    def test_coding_plan_host_is_zai(self):
        assert is_zai_request(
            provider="zai",
            model="glm-5.2",
            base_url="https://api.z.ai/api/coding/paas/v4",
        )

    def test_china_mirror_is_zai(self):
        assert is_zai_request(
            provider="zai",
            model="glm-5",
            base_url="https://open.bigmodel.cn/api/paas/v4",
        )

    @pytest.mark.parametrize(
        "provider",
        ["zai", "ZAI", "zhipu", "glm", "z-ai", "Z.AI"],
    )
    def test_provider_aliases(self, provider):
        assert is_zai_request(provider=provider, model="glm-5.2", base_url="")

    def test_openai_host_is_not_zai(self):
        assert not is_zai_request(
            provider="openai",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
        )

    def test_anthropic_host_is_not_zai(self):
        assert not is_zai_request(
            provider="anthropic",
            model="claude-sonnet-4",
            base_url="https://api.anthropic.com",
        )

    def test_bare_glm_model_without_zai_host_is_not_gated(self):
        # A glm-* model served from a local/proxy host must not be silently
        # throttled just because of the model name.
        assert not is_zai_request(
            provider="custom",
            model="glm-4.6",
            base_url="http://localhost:8080/v1",
        )

    def test_none_inputs_are_safe(self):
        assert not is_zai_request(provider=None, model=None, base_url=None)


class TestPassThrough:
    """Non-Z.AI requests must take the no-op path — no slot, no release."""

    def test_non_zai_handle_is_noop(self):
        handle = acquire_zai_slot(
            provider="openai",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
        )
        assert type(handle).__name__ == "_NoopHandle"
        with handle:
            pass  # must not raise

    def test_disabled_gate_is_noop_even_for_zai(self):
        _reset(0)  # disabled
        handle = acquire_zai_slot(
            provider="zai",
            model="glm-5.2",
            base_url="https://api.z.ai/api/coding/paas/v4",
        )
        assert type(handle).__name__ == "_NoopHandle"


class TestRealCapping:
    """Under a Z.AI swarm, in-flight calls must not exceed the configured cap."""

    def test_never_exceeds_cap(self):
        _reset(2)
        sem = zai_concurrency._gate._semaphore()
        assert sem is not None

        observed_peak = {"value": 0}
        in_flight = {"value": 0}
        lock = threading.Lock()

        def _worker():
            with acquire_zai_slot(
                provider="zai",
                model="glm-5.2",
                base_url="https://api.z.ai/api/coding/paas/v4",
            ):
                with lock:
                    in_flight["value"] += 1
                    observed_peak["value"] = max(
                        observed_peak["value"], in_flight["value"]
                    )
                time.sleep(0.05)
                with lock:
                    in_flight["value"] -= 1

        threads = [threading.Thread(target=_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert observed_peak["value"] <= 2

    def test_slots_released_after_use(self):
        _reset(1)
        sem = zai_concurrency._gate._semaphore()
        assert sem is not None

        with acquire_zai_slot(
            provider="zai",
            model="glm-5.2",
            base_url="https://api.z.ai/api/coding/paas/v4",
        ):
            # While held, a second acquire must not be available immediately.
            assert sem.acquire(blocking=False) is False
        # After exit, the slot is released and available again.
        assert sem.acquire(blocking=False) is True
        sem.release()


class TestNonFatal:
    """A starved gate must degrade to a no-op, not hang or raise."""

    def test_starved_proceeds(self):
        # cap 1, timeout 0 -> non-blocking probe that fails when held.
        _reset(1, 0.0)
        sem = zai_concurrency._gate._semaphore()
        assert sem is not None
        sem.acquire()  # hold the only slot

        handle = acquire_zai_slot(
            provider="zai",
            model="glm-5.2",
            base_url="https://api.z.ai/api/coding/paas/v4",
        )
        # Starved -> pass-through, not an error.
        assert type(handle).__name__ == "_NoopHandle"
        with handle:
            pass


class TestConfigKnob:
    def test_default_cap_is_four(self, monkeypatch):
        # Assert the MODULE's default, not a re-computed literal: with the env
        # var unset, a fresh import must leave the module-level cap at 4 and
        # configured_max_concurrent() must report it. This fails if the
        # documented default regresses (the old test only re-checked the
        # stdlib helper and could never catch that).
        import importlib

        monkeypatch.delenv("HERMES_ZAI_MAX_CONCURRENT", raising=False)
        reloaded = importlib.reload(zai_concurrency)
        try:
            assert reloaded._ZAI_MAX_CONCURRENT == 4
            assert reloaded.configured_max_concurrent() == 4
        finally:
            # restore a clean gate for the rest of the suite
            reloaded._reset_for_tests(4, 30.0)

    def test_env_override_sets_cap(self, monkeypatch):
        import importlib

        monkeypatch.setenv("HERMES_ZAI_MAX_CONCURRENT", "7")
        reloaded = importlib.reload(zai_concurrency)
        try:
            assert reloaded._ZAI_MAX_CONCURRENT == 7
            assert reloaded.configured_max_concurrent() == 7
        finally:
            monkeypatch.delenv("HERMES_ZAI_MAX_CONCURRENT", raising=False)
            importlib.reload(zai_concurrency)._reset_for_tests(4, 30.0)

    def test_fractional_timeout_is_honored(self, monkeypatch):
        # regression: the seconds timeout is parsed as a float, so a
        # sub-second tuning is not silently snapped to the default
        import importlib

        monkeypatch.setenv("HERMES_ZAI_ACQUIRE_TIMEOUT_S", "0.5")
        reloaded = importlib.reload(zai_concurrency)
        try:
            assert abs(reloaded._ZAI_ACQUIRE_TIMEOUT - 0.5) < 1e-9
        finally:
            monkeypatch.delenv("HERMES_ZAI_ACQUIRE_TIMEOUT_S", raising=False)
            importlib.reload(zai_concurrency)._reset_for_tests(4, 30.0)
