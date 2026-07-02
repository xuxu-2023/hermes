"""Tests for Neuralwatt provider support — standard OpenAI-compatible provider."""

import types

import pytest

from hermes_cli.auth import (
    PROVIDER_REGISTRY,
    resolve_provider,
    get_api_key_provider_status,
    resolve_api_key_provider_credentials,
)


_OTHER_PROVIDER_KEYS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY",
    "XAI_API_KEY", "KIMI_API_KEY", "KIMI_CN_API_KEY",
    "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY",
    "KILOCODE_API_KEY", "HF_TOKEN", "GLM_API_KEY", "ZAI_API_KEY",
    "XIAOMI_API_KEY", "TOKENHUB_API_KEY", "GMI_API_KEY",
    "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
)


# =============================================================================
# Provider Registry
# =============================================================================


class TestNeuralwattProviderRegistry:
    def test_registered(self):
        assert "neuralwatt" in PROVIDER_REGISTRY

    def test_name(self):
        assert PROVIDER_REGISTRY["neuralwatt"].name == "Neuralwatt"

    def test_auth_type(self):
        assert PROVIDER_REGISTRY["neuralwatt"].auth_type == "api_key"

    def test_inference_base_url(self):
        assert PROVIDER_REGISTRY["neuralwatt"].inference_base_url == "https://api.neuralwatt.com/v1"

    def test_api_key_env_vars(self):
        assert PROVIDER_REGISTRY["neuralwatt"].api_key_env_vars == ("NEURALWATT_API_KEY",)

    def test_base_url_env_var(self):
        assert PROVIDER_REGISTRY["neuralwatt"].base_url_env_var == "NEURALWATT_BASE_URL"


# =============================================================================
# Aliases
# =============================================================================


class TestNeuralwattAliases:
    @pytest.mark.parametrize("alias", ["neuralwatt", "neural-watt", "neuralwatt-ai"])
    def test_alias_resolves(self, alias, monkeypatch):
        for key in _OTHER_PROVIDER_KEYS + ("OPENROUTER_API_KEY",):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("NEURALWATT_API_KEY", "nw-test-12345")
        assert resolve_provider(alias) == "neuralwatt"

    def test_normalize_provider_models_py(self):
        from hermes_cli.models import normalize_provider
        assert normalize_provider("neural-watt") == "neuralwatt"
        assert normalize_provider("neuralwatt-ai") == "neuralwatt"

    def test_normalize_provider_providers_py(self):
        from hermes_cli.providers import normalize_provider
        assert normalize_provider("neural-watt") == "neuralwatt"
        assert normalize_provider("neuralwatt-ai") == "neuralwatt"


# =============================================================================
# Credentials
# =============================================================================


class TestNeuralwattCredentials:
    def test_status_configured(self, monkeypatch):
        monkeypatch.setenv("NEURALWATT_API_KEY", "nw-test")
        status = get_api_key_provider_status("neuralwatt")
        assert status["configured"]

    def test_status_not_configured(self, monkeypatch):
        monkeypatch.delenv("NEURALWATT_API_KEY", raising=False)
        status = get_api_key_provider_status("neuralwatt")
        assert not status["configured"]

    def test_openrouter_key_does_not_make_neuralwatt_configured(self, monkeypatch):
        """OpenRouter users should NOT see neuralwatt as configured."""
        monkeypatch.delenv("NEURALWATT_API_KEY", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        status = get_api_key_provider_status("neuralwatt")
        assert not status["configured"]

    def test_resolve_credentials(self, monkeypatch):
        monkeypatch.setenv("NEURALWATT_API_KEY", "nw-direct-key")
        monkeypatch.delenv("NEURALWATT_BASE_URL", raising=False)
        creds = resolve_api_key_provider_credentials("neuralwatt")
        assert creds["api_key"] == "nw-direct-key"
        assert creds["base_url"] == "https://api.neuralwatt.com/v1"

    def test_custom_base_url_override(self, monkeypatch):
        monkeypatch.setenv("NEURALWATT_API_KEY", "nw-x")
        monkeypatch.setenv("NEURALWATT_BASE_URL", "https://custom.neuralwatt.example/v1")
        creds = resolve_api_key_provider_credentials("neuralwatt")
        assert creds["base_url"] == "https://custom.neuralwatt.example/v1"


# =============================================================================
# Model catalog
# =============================================================================


class TestNeuralwattModelCatalog:
    def test_static_model_list(self):
        """Neuralwatt has a static _PROVIDER_MODELS catalog entry. Specific
        model names change with releases and don't belong in tests.
        """
        from hermes_cli.models import _PROVIDER_MODELS
        assert "neuralwatt" in _PROVIDER_MODELS
        assert len(_PROVIDER_MODELS["neuralwatt"]) >= 1

    def test_canonical_provider_entry(self):
        from hermes_cli.models import CANONICAL_PROVIDERS
        slugs = [p.slug for p in CANONICAL_PROVIDERS]
        assert "neuralwatt" in slugs


# =============================================================================
# Model normalization — slash-form IDs are preserved (no prefix strip)
# =============================================================================


class TestNeuralwattNormalization:
    def test_not_in_matching_prefix_strip_set(self):
        """Neuralwatt model IDs are slash-form (e.g. ``zai-org/GLM-5.1-FP8``)
        where the slash is part of the ID, so the provider must NOT participate
        in matching-prefix stripping (which would corrupt the org segment).
        """
        from hermes_cli.model_normalize import _MATCHING_PREFIX_STRIP_PROVIDERS
        assert "neuralwatt" not in _MATCHING_PREFIX_STRIP_PROVIDERS

    def test_slash_form_id_unchanged(self):
        from hermes_cli.model_normalize import normalize_model_for_provider
        assert (
            normalize_model_for_provider("zai-org/GLM-5.1-FP8", "neuralwatt")
            == "zai-org/GLM-5.1-FP8"
        )


# =============================================================================
# URL mapping
# =============================================================================


class TestNeuralwattURLMapping:
    def test_url_to_provider(self):
        from agent.model_metadata import _URL_TO_PROVIDER
        assert _URL_TO_PROVIDER.get("api.neuralwatt.com") == "neuralwatt"

    def test_provider_prefixes(self):
        from agent.model_metadata import _PROVIDER_PREFIXES
        assert "neuralwatt" in _PROVIDER_PREFIXES
        assert "neural-watt" in _PROVIDER_PREFIXES
        assert "neuralwatt-ai" in _PROVIDER_PREFIXES

    def test_trajectory_compressor_unknown_for_neuralwatt(self):
        """Neuralwatt needs no provider-specific trajectory compression, so it
        falls through to the generic ("") path like other OpenAI-compatible
        aggregators (e.g. GMI).
        """
        import trajectory_compressor as tc
        comp = tc.TrajectoryCompressor.__new__(tc.TrajectoryCompressor)
        comp.config = types.SimpleNamespace(base_url="https://api.neuralwatt.com/v1")
        assert comp._detect_provider() == ""


# =============================================================================
# providers.py overlay + aliases
# =============================================================================


class TestNeuralwattProvidersModule:
    def test_overlay_exists(self):
        from hermes_cli.providers import HERMES_OVERLAYS
        assert "neuralwatt" in HERMES_OVERLAYS
        overlay = HERMES_OVERLAYS["neuralwatt"]
        assert overlay.transport == "openai_chat"
        assert overlay.base_url_env_var == "NEURALWATT_BASE_URL"
        assert not overlay.is_aggregator

    def test_label(self):
        from hermes_cli.models import _PROVIDER_LABELS
        assert _PROVIDER_LABELS["neuralwatt"] == "Neuralwatt"


# =============================================================================
# Provider profile (plugin registration)
# =============================================================================


class TestNeuralwattProfile:
    def test_profile_registered(self):
        from providers import get_provider_profile
        profile = get_provider_profile("neuralwatt")
        assert profile is not None
        assert profile.base_url == "https://api.neuralwatt.com/v1"
        assert profile.auth_type == "api_key"

    def test_default_aux_model_on_profile(self):
        """Neuralwatt sets default_aux_model on its profile (the modern way) —
        no entry in the legacy _API_KEY_PROVIDER_AUX_MODELS fallback dict.
        """
        from providers import get_provider_profile
        profile = get_provider_profile("neuralwatt")
        assert profile.default_aux_model == "glm-5-fast"

    def test_not_in_aux_fallback_dict(self):
        from agent.auxiliary_client import _API_KEY_PROVIDER_AUX_MODELS
        assert "neuralwatt" not in _API_KEY_PROVIDER_AUX_MODELS
