"""Unit tests for the Z.AI / GLM provider profile's thinking-mode wiring.

GLM-5.2+ models accept ``extra_body.thinking`` with an optional ``effort``
field (``high`` / ``max``).  Older GLM models (5.1, 5, 4.x) only support
``thinking.type`` without effort.

These tests pin the profile's wire-shape contract so Z.AI requests stay
correctly shaped without going live.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def zai_profile():
    """Resolve the registered Z.AI profile.

    Going through ``providers.get_provider_profile`` keeps the test honest —
    if someone later replaces the registered class with a plain
    ``ProviderProfile``, every assertion below collapses.
    """
    import model_tools  # noqa: F401
    import providers

    profile = providers.get_provider_profile("zai")
    assert profile is not None, "zai provider profile must be registered"
    return profile


class TestZAIThinkingWireShape:
    """``build_api_kwargs_extras`` produces Z.AI's exact wire format."""

    def test_no_reasoning_config_returns_empty(self, zai_profile):
        """No reasoning_config → preserve default wire format (no thinking).

        This is critical for backward compatibility: existing GLM users who
        haven't configured reasoning must see no wire format change.
        """
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config=None, model="glm-5.2"
        )
        assert extra_body == {}
        assert top_level == {}

    def test_glm52_enabled_with_xhigh_effort(self, zai_profile):
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "xhigh"},
            model="glm-5.2",
        )
        assert extra_body == {"thinking": {"type": "enabled", "effort": "max"}}
        assert top_level == {}

    def test_glm52_enabled_with_high_effort(self, zai_profile):
        extra_body, _ = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "high"},
            model="glm-5.2",
        )
        assert extra_body == {"thinking": {"type": "enabled", "effort": "high"}}

    @pytest.mark.parametrize("effort", ["none", "minimal", "low", "medium", "", "garbage"])
    def test_glm52_lower_efforts_omit_effort_field(self, zai_profile, effort):
        """Lower or unknown efforts → omit effort, GLM uses server default."""
        extra_body, _ = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": effort},
            model="glm-5.2",
        )
        assert extra_body == {"thinking": {"type": "enabled"}}

    def test_glm52_disabled_sends_disabled_marker(self, zai_profile):
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": False}, model="glm-5.2"
        )
        assert extra_body == {"thinking": {"type": "disabled"}}
        assert top_level == {}

    def test_disabled_ignores_effort_field(self, zai_profile):
        """Effort silently dropped when thinking is off."""
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": False, "effort": "xhigh"},
            model="glm-5.2",
        )
        assert extra_body == {"thinking": {"type": "disabled"}}
        assert top_level == {}

    @pytest.mark.parametrize("effort", ["xhigh", "max", "XHIGH", "  Max  "])
    def test_xhigh_and_max_normalize_to_max(self, zai_profile, effort):
        """All max-level efforts produce thinking.effort=max in extra_body."""
        extra_body, top_level = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": effort},
            model="glm-5.2",
        )
        assert extra_body["thinking"]["effort"] == "max"
        assert top_level == {}

    def test_case_insensitive_model_and_effort(self, zai_profile):
        """Model names and efforts are case-insensitive."""
        extra_body, _ = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "XHigh"},
            model="GLM-5.2",
        )
        assert extra_body == {"thinking": {"type": "enabled", "effort": "max"}}

    def test_vendor_prefixed_model(self, zai_profile):
        """Vendor prefix stripped before matching."""
        extra_body, _ = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "xhigh"},
            model="zai/glm-5.2",
        )
        assert extra_body == {"thinking": {"type": "enabled", "effort": "max"}}


class TestZAIModelGating:
    """GLM-5.2+ gets effort; older models get thinking without effort."""

    @pytest.mark.parametrize("model", ["glm-5.2", "GLM-5.2", "zai/glm-5.2", "zhipu/glm-5.2"])
    def test_effort_capable_models_get_effort(self, zai_profile, model):
        extra_body, _ = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "xhigh"}, model=model
        )
        assert extra_body["thinking"]["effort"] == "max"

    @pytest.mark.parametrize(
        "model",
        ["glm-5.1", "glm-5", "glm-4.6", "glm-4.5", "glm-4-9b", "unknown-model", None, ""],
    )
    def test_older_models_omit_effort(self, zai_profile, model):
        """Older GLM models get thinking.type but no effort field."""
        extra_body, _ = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "xhigh"}, model=model
        )
        assert extra_body == {"thinking": {"type": "enabled"}}
        assert "effort" not in extra_body.get("thinking", {})

    def test_glm_52_variant_matches(self, zai_profile):
        """GLM-5.2-preview, glm-5.2-turbo etc. should also match."""
        extra_body, _ = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "xhigh"},
            model="glm-5.2-preview",
        )
        assert extra_body["thinking"]["effort"] == "max"

    def test_glm_520_does_not_match(self, zai_profile):
        """Hypothetical glm-5.20 must NOT match glm-5.2."""
        extra_body, _ = zai_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "xhigh"},
            model="glm-5.20",
        )
        assert "effort" not in extra_body.get("thinking", {})


class TestZAIBackwardCompat:
    """Ensure the profile doesn't break existing GLM users."""

    def test_none_reasoning_config_preserves_wire_format(self, zai_profile):
        """The critical backward-compat test: no reasoning_config → no-op."""
        for model in ["glm-5.1", "glm-5", "glm-4.6", "glm-5.2"]:
            extra_body, top_level = zai_profile.build_api_kwargs_extras(
                reasoning_config=None, model=model
            )
            assert extra_body == {}, f"model={model} should get empty extra_body"
            assert top_level == {}, f"model={model} should get empty top_level"


class TestZAITransportIntegration:
    """End-to-end: the transport's full kwargs match Z.AI's expected wire format.

    Mirrors the DeepSeek integration test pattern — verify the full
    ``ChatCompletionsTransport().build_kwargs()`` output, not just the
    profile method in isolation.
    """

    def test_glm52_xhigh_produces_correct_wire_shape(self, zai_profile):
        from agent.transports.chat_completions import ChatCompletionsTransport

        kwargs = ChatCompletionsTransport().build_kwargs(
            model="glm-5.2",
            messages=[{"role": "user", "content": "ping"}],
            tools=None,
            provider_profile=zai_profile,
            reasoning_config={"enabled": True, "effort": "xhigh"},
            base_url="https://api.z.ai/api/paas/v4",
            provider_name="zai",
        )
        assert kwargs["model"] == "glm-5.2"
        assert kwargs["extra_body"]["thinking"] == {"type": "enabled", "effort": "max"}

    def test_glm51_no_reasoning_preserves_wire_format(self, zai_profile):
        """Older model with no reasoning config → no thinking in wire."""
        from agent.transports.chat_completions import ChatCompletionsTransport

        kwargs = ChatCompletionsTransport().build_kwargs(
            model="glm-5.1",
            messages=[{"role": "user", "content": "ping"}],
            tools=None,
            provider_profile=zai_profile,
            reasoning_config=None,
            base_url="https://api.z.ai/api/paas/v4",
            provider_name="zai",
        )
        assert "thinking" not in kwargs.get("extra_body", {})

    def test_glm52_disabled_thinking(self, zai_profile):
        from agent.transports.chat_completions import ChatCompletionsTransport

        kwargs = ChatCompletionsTransport().build_kwargs(
            model="glm-5.2",
            messages=[{"role": "user", "content": "ping"}],
            tools=None,
            provider_profile=zai_profile,
            reasoning_config={"enabled": False},
            base_url="https://api.z.ai/api/paas/v4",
            provider_name="zai",
        )
        assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
