"""Unit tests for the Xiaomi MiMo provider profile's reasoning wiring.

MiMo (``api.xiaomimimo.com/v1``, OpenAI-compatible) reasons by default. Turning
reasoning off (``/reasoning none`` -> reasoning_config ``{"enabled": False}``)
must send ``extra_body={"thinking": {"type": "disabled"}}`` so ``reasoning_tokens``
drop to 0. Every other state leaves the server default untouched — MiMo rejects a
top-level ``reasoning_effort`` (HTTP 400), so there is no effort granularity to map.

Mirrors ``tests/plugins/model_providers/test_kimi_profile.py``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def xiaomi_profile():
    """Resolve the registered Xiaomi profile via the provider registry.

    Importing ``model_tools`` triggers plugin discovery, which registers the
    Xiaomi profile. Going through ``get_provider_profile`` keeps the test honest:
    if the registered class is ever swapped for a plain ``ProviderProfile`` the
    disable assertion below collapses.
    """
    import model_tools  # noqa: F401
    import providers

    profile = providers.get_provider_profile("xiaomi")
    assert profile is not None, "xiaomi provider profile must be registered"
    return profile


class TestXiaomiReasoningWireShape:
    def test_no_config_leaves_server_default(self, xiaomi_profile):
        extra_body, top_level = xiaomi_profile.build_api_kwargs_extras(
            reasoning_config=None
        )
        assert extra_body == {}
        assert top_level == {}

    def test_disabled_sends_thinking_disabled(self, xiaomi_profile):
        extra_body, top_level = xiaomi_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": False}
        )
        assert extra_body == {"thinking": {"type": "disabled"}}
        assert top_level == {}

    @pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "xhigh"])
    def test_enabled_leaves_default(self, xiaomi_profile, effort):
        extra_body, top_level = xiaomi_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": effort}
        )
        assert extra_body == {}
        assert top_level == {}
