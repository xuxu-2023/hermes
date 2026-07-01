"""Tests for auxiliary auto provider model mismatch fix (issue #44746).

When provider == "auto", the model resolution should use the live runtime's
resolved model, not a stale model from _read_main_model().
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agent import auxiliary_client as ac


class TestAuxiliaryAutoModelMismatch:
    """provider == "auto" must not use stale _read_main_model()."""

    def test_auto_uses_resolved_model_not_stale_main(self, monkeypatch):
        """Auto provider should use resolved model from _resolve_auto, not _read_main_model."""
        # Mock _resolve_auto to return a live runtime model
        mock_client = MagicMock()
        monkeypatch.setattr(
            ac, "_resolve_auto",
            lambda main_runtime=None: (mock_client, "glm-5.1"),
        )
        # Mock _read_main_model to return a stale model
        monkeypatch.setattr(ac, "_read_main_model", lambda: "gpt-5.5")
        # Mock _get_aux_model_for_provider to return empty for "auto"
        monkeypatch.setattr(ac, "_get_aux_model_for_provider", lambda p: "")

        client, model = ac.resolve_provider_client(
            "auto",
            model=None,
            main_runtime={
                "provider": "zai",
                "model": "glm-5.1",
                "base_url": "https://api.z.ai/api/coding/paas/v4",
            },
        )

        assert client is mock_client
        assert model == "glm-5.1"  # Should be resolved, not stale "gpt-5.5"

    def test_auto_with_explicit_model_keeps_explicit(self, monkeypatch):
        """When caller provides explicit model, auto should keep it."""
        mock_client = MagicMock()
        monkeypatch.setattr(
            ac, "_resolve_auto",
            lambda main_runtime=None: (mock_client, "glm-5.1"),
        )
        monkeypatch.setattr(ac, "_read_main_model", lambda: "gpt-5.5")
        monkeypatch.setattr(ac, "_get_aux_model_for_provider", lambda p: "")

        client, model = ac.resolve_provider_client(
            "auto",
            model="custom-model",
            main_runtime={
                "provider": "zai",
                "model": "glm-5.1",
                "base_url": "https://api.z.ai/api/coding/paas/v4",
            },
        )

        assert client is mock_client
        assert model == "custom-model"  # Explicit model should be preserved

    def test_non_auto_still_uses_main_model_fallback(self, monkeypatch):
        """Non-auto providers should still fall back to _read_main_model."""
        mock_client = MagicMock()
        monkeypatch.setattr(
            ac, "_try_openrouter",
            lambda **kw: (mock_client, "openrouter-default"),
        )
        monkeypatch.setattr(ac, "_read_main_model", lambda: "gpt-5.5")
        monkeypatch.setattr(ac, "_get_aux_model_for_provider", lambda p: "")
        monkeypatch.setattr(ac, "_normalize_resolved_model", lambda m, p: m)

        client, model = ac.resolve_provider_client(
            "openrouter",
            model=None,
        )

        assert client is mock_client
        assert model == "gpt-5.5"  # Should use _read_main_model for non-auto

    def test_auto_with_aux_model_default(self, monkeypatch):
        """When _get_aux_model_for_provider returns a value, auto should use it."""
        mock_client = MagicMock()
        monkeypatch.setattr(
            ac, "_resolve_auto",
            lambda main_runtime=None: (mock_client, "glm-5.1"),
        )
        monkeypatch.setattr(ac, "_read_main_model", lambda: "gpt-5.5")
        monkeypatch.setattr(ac, "_get_aux_model_for_provider", lambda p: "aux-default" if p == "auto" else "")

        client, model = ac.resolve_provider_client(
            "auto",
            model=None,
            main_runtime={
                "provider": "zai",
                "model": "glm-5.1",
                "base_url": "https://api.z.ai/api/coding/paas/v4",
            },
        )

        assert client is mock_client
        # _get_aux_model_for_provider returns "aux-default", which should be used
        # But the auto branch's resolved model should take precedence
        # Actually, looking at the code: model = _get_aux_model_for_provider("auto") or None or model
        # So model = "aux-default", then in auto branch: final_model = "aux-default" or "glm-5.1"
        # So it should be "aux-default"
        assert model == "aux-default"

    def test_auto_resolves_to_none_when_no_runtime(self, monkeypatch):
        """Auto provider with no runtime should return None."""
        monkeypatch.setattr(
            ac, "_resolve_auto",
            lambda main_runtime=None: (None, None),
        )
        monkeypatch.setattr(ac, "_read_main_model", lambda: "gpt-5.5")
        monkeypatch.setattr(ac, "_get_aux_model_for_provider", lambda p: "")

        client, model = ac.resolve_provider_client(
            "auto",
            model=None,
            main_runtime=None,
        )

        assert client is None
        assert model is None
