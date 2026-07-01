"""Tests for the Umans AI provider profile and registry entries."""

import pytest

from providers import get_provider_profile, list_providers


# ── Profile ──────────────────────────────────────────────────────────

def test_profile_registered():
    p = get_provider_profile("umans-ai")
    assert p is not None
    assert p.name == "umans-ai"


def test_profile_aliases():
    for alias in ("umans", "custom:umans"):
        p = get_provider_profile(alias)
        assert p is not None
        assert p.name == "umans-ai"


def test_profile_fields():
    p = get_provider_profile("umans-ai")
    assert p.base_url == "https://api.code.umans.ai/v1"
    assert p.auth_type == "api_key"
    assert "UMANS_API_KEY" in p.env_vars
    assert p.supports_vision is True
    assert len(p.fallback_models) == 5
    assert "umans-glm-5.2" in p.fallback_models


def test_profile_in_list():
    names = {p.name for p in list_providers()}
    assert "umans-ai" in names


# ── Reasoning ────────────────────────────────────────────────────────

def test_reasoning_effort_xhigh_maps_to_max():
    p = get_provider_profile("umans-ai")
    extra_body, top_level = p.build_api_kwargs_extras(
        reasoning_config={"enabled": True, "effort": "xhigh"},
        model="umans-glm-5.2",
    )
    assert top_level == {"reasoning_effort": "max"}
    assert extra_body == {}


def test_reasoning_effort_max_maps_to_max():
    p = get_provider_profile("umans-ai")
    extra_body, top_level = p.build_api_kwargs_extras(
        reasoning_config={"enabled": True, "effort": "max"},
        model="umans-glm-5.2",
    )
    assert top_level == {"reasoning_effort": "max"}


def test_reasoning_effort_medium_passes_through():
    p = get_provider_profile("umans-ai")
    extra_body, top_level = p.build_api_kwargs_extras(
        reasoning_config={"enabled": True, "effort": "medium"},
        model="umans-glm-5.2",
    )
    assert top_level == {"reasoning_effort": "medium"}


def test_reasoning_disabled():
    p = get_provider_profile("umans-ai")
    extra_body, top_level = p.build_api_kwargs_extras(
        reasoning_config={"enabled": False},
        model="umans-glm-5.2",
    )
    assert top_level == {}
    assert extra_body == {}


def test_reasoning_no_config_omits_effort():
    p = get_provider_profile("umans-ai")
    extra_body, top_level = p.build_api_kwargs_extras(
        reasoning_config=None,
        model="umans-glm-5.2",
    )
    assert top_level == {}


# ── Message sanitization ─────────────────────────────────────────────

def test_prepare_messages_strips_timestamp():
    p = get_provider_profile("umans-ai")
    msgs = [
        {"role": "user", "content": "hi", "timestamp": 12345.0},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye", "timestamp": 67890.0},
    ]
    result = p.prepare_messages(msgs)
    assert "timestamp" not in result[0]
    assert "timestamp" not in result[2]
    # Messages without timestamp are untouched
    assert result[1] == msgs[1]
    # Original dicts are not mutated
    assert "timestamp" in msgs[0]


def test_prepare_messages_passthrough_when_no_timestamp():
    p = get_provider_profile("umans-ai")
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    result = p.prepare_messages(msgs)
    assert result == msgs


# ── Registry wiring ──────────────────────────────────────────────────

def test_provider_models_entry():
    from hermes_cli.models import _PROVIDER_MODELS
    assert "umans-ai" in _PROVIDER_MODELS
    models = _PROVIDER_MODELS["umans-ai"]
    assert "umans-glm-5.2" in models
    assert len(models) == 5


def test_provider_aliases_entry():
    from hermes_cli.models import _PROVIDER_ALIASES
    assert _PROVIDER_ALIASES.get("umans") == "umans-ai"


def test_hermes_overlay_entry():
    from hermes_cli.providers import HERMES_OVERLAYS
    assert "umans-ai" in HERMES_OVERLAYS
    overlay = HERMES_OVERLAYS["umans-ai"]
    assert overlay.transport == "openai_chat"
    assert overlay.base_url_env_var == "UMANS_BASE_URL"


def test_provider_prefixes():
    from agent.model_metadata import _PROVIDER_PREFIXES
    assert "umans-ai" in _PROVIDER_PREFIXES
    assert "umans" in _PROVIDER_PREFIXES


def test_matching_prefix_strip():
    from hermes_cli.model_normalize import _MATCHING_PREFIX_STRIP_PROVIDERS
    assert "umans-ai" in _MATCHING_PREFIX_STRIP_PROVIDERS
    assert "umans" in _MATCHING_PREFIX_STRIP_PROVIDERS


def test_doctor_env_hints():
    from hermes_cli.doctor import _PROVIDER_ENV_HINTS
    assert "UMANS_API_KEY" in _PROVIDER_ENV_HINTS


def test_models_dev_mapping():
    from agent.models_dev import PROVIDER_TO_MODELS_DEV
    assert PROVIDER_TO_MODELS_DEV.get("umans-ai") == "umans-ai"
