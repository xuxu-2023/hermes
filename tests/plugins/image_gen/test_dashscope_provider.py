#!/usr/bin/env python3
"""Tests for DashScope (Alibaba Cloud) image generation provider."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    """Ensure DASHSCOPE_API_KEY is set for all tests."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key-12345")


# ---------------------------------------------------------------------------
# Provider class tests
# ---------------------------------------------------------------------------


class TestDashScopeImageGenProvider:
    def test_name(self):
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        provider = DashScopeImageGenProvider()
        assert provider.name == "dashscope"

    def test_display_name(self):
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        provider = DashScopeImageGenProvider()
        assert provider.display_name == "DashScope (Alibaba)"

    def test_is_available_with_key(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-xxx")
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        provider = DashScopeImageGenProvider()
        assert provider.is_available() is True

    def test_is_available_without_key(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        provider = DashScopeImageGenProvider()
        assert provider.is_available() is False

    def test_list_models(self):
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        provider = DashScopeImageGenProvider()
        models = provider.list_models()
        assert len(models) == 3
        assert models[0]["id"] == "wanx2.1-t2i-turbo"
        assert models[1]["id"] == "wanx2.1-t2i-plus"
        assert models[2]["id"] == "wanx-v1"

    def test_default_model(self):
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        provider = DashScopeImageGenProvider()
        assert provider.default_model() == "wanx2.1-t2i-turbo"

    def test_get_setup_schema(self):
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        provider = DashScopeImageGenProvider()
        schema = provider.get_setup_schema()
        assert schema["name"] == "DashScope (Alibaba Cloud)"
        assert schema["badge"] == "paid"
        assert len(schema["env_vars"]) == 1
        assert schema["env_vars"][0]["key"] == "DASHSCOPE_API_KEY"


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_default_model(self):
        from plugins.image_gen.dashscope import _resolve_model

        model_id, meta = _resolve_model()
        assert model_id == "wanx2.1-t2i-turbo"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_IMAGE_MODEL", "wanx-v1")
        from plugins.image_gen.dashscope import _resolve_model

        model_id, _ = _resolve_model()
        assert model_id == "wanx-v1"

    def test_config_override(self):
        """Test that config.yaml model override works."""
        from plugins.image_gen.dashscope import _resolve_model

        mock_cfg = {"image_gen": {"dashscope": {"model": "wanx2.1-t2i-plus"}}}
        with patch("hermes_cli.config.load_config", return_value=mock_cfg):
            model_id, _ = _resolve_model()
            assert model_id == "wanx2.1-t2i-plus"


# ---------------------------------------------------------------------------
# Generate tests
# ---------------------------------------------------------------------------


class TestGenerate:
    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        provider = DashScopeImageGenProvider()
        result = provider.generate(prompt="test")
        assert result["success"] is False
        assert "DASHSCOPE_API_KEY" in result["error"]
        assert result["error_type"] == "auth_required"

    def test_empty_prompt(self):
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        provider = DashScopeImageGenProvider()
        result = provider.generate(prompt="")
        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"

    def test_successful_generation(self):
        """Test full async task flow: submit → poll → download."""
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        # Mock submit response
        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.raise_for_status = MagicMock()
        submit_resp.json.return_value = {
            "output": {"task_id": "task-12345", "task_status": "PENDING"},
            "request_id": "req-abc",
        }

        # Mock poll response (succeeded)
        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.raise_for_status = MagicMock()
        poll_resp.json.return_value = {
            "output": {
                "task_id": "task-12345",
                "task_status": "SUCCEEDED",
                "results": [{"url": "https://dashscope-result.example.com/image.png"}],
            },
        }

        def mock_post(*args, **kwargs):
            return submit_resp

        def mock_get(*args, **kwargs):
            return poll_resp

        with patch("plugins.image_gen.dashscope.requests.post", side_effect=mock_post), \
             patch("plugins.image_gen.dashscope.requests.get", side_effect=mock_get), \
             patch("plugins.image_gen.dashscope.time.sleep"), \
             patch(
                 "plugins.image_gen.dashscope.save_url_image",
                 return_value=Path("/tmp/dashscope_wanx2.1-t2i-turbo_20260611_000000_deadbeef.png"),
             ) as mock_save:
            provider = DashScopeImageGenProvider()
            result = provider.generate(prompt="A cat playing piano")

        assert result["success"] is True
        assert result["image"].startswith("/")
        assert result["provider"] == "dashscope"
        assert result["model"] == "wanx2.1-t2i-turbo"
        mock_save.assert_called_once()

    def test_task_failure(self):
        """Test that a FAILED task returns an error response."""
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.raise_for_status = MagicMock()
        submit_resp.json.return_value = {
            "output": {"task_id": "task-fail", "task_status": "PENDING"},
        }

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.raise_for_status = MagicMock()
        poll_resp.json.return_value = {
            "output": {
                "task_id": "task-fail",
                "task_status": "FAILED",
                "code": "InvalidParameter",
                "message": "Invalid prompt content",
            },
        }

        with patch("plugins.image_gen.dashscope.requests.post", return_value=submit_resp), \
             patch("plugins.image_gen.dashscope.requests.get", return_value=poll_resp), \
             patch("plugins.image_gen.dashscope.time.sleep"):
            provider = DashScopeImageGenProvider()
            result = provider.generate(prompt="test")

        assert result["success"] is False
        assert result["error_type"] == "provider_error"
        assert "InvalidParameter" in result["error"]

    def test_submit_api_error(self):
        """Test HTTP error on task submission."""
        import requests as req_lib
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_resp.json.return_value = {"message": "Invalid API key"}
        mock_resp.raise_for_status.side_effect = req_lib.HTTPError(response=mock_resp)

        with patch("plugins.image_gen.dashscope.requests.post", return_value=mock_resp):
            provider = DashScopeImageGenProvider()
            result = provider.generate(prompt="test")

        assert result["success"] is False
        assert result["error_type"] == "api_error"
        assert "401" in result["error"]

    def test_submit_timeout(self):
        """Test timeout on task submission."""
        import requests as req_lib
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        with patch("plugins.image_gen.dashscope.requests.post", side_effect=req_lib.Timeout()):
            provider = DashScopeImageGenProvider()
            result = provider.generate(prompt="test")

        assert result["success"] is False
        assert result["error_type"] == "timeout"

    def test_submit_connection_error(self):
        """Test connection error on task submission."""
        import requests as req_lib
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        with patch("plugins.image_gen.dashscope.requests.post", side_effect=req_lib.ConnectionError("refused")):
            provider = DashScopeImageGenProvider()
            result = provider.generate(prompt="test")

        assert result["success"] is False
        assert result["error_type"] == "connection_error"

    def test_missing_task_id(self):
        """Test that missing task_id in submit response returns error."""
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.raise_for_status = MagicMock()
        submit_resp.json.return_value = {"output": {}, "request_id": "req-abc"}

        with patch("plugins.image_gen.dashscope.requests.post", return_value=submit_resp):
            provider = DashScopeImageGenProvider()
            result = provider.generate(prompt="test")

        assert result["success"] is False
        assert "task_id" in result["error"]

    def test_empty_results(self):
        """Test that empty results list returns error."""
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.raise_for_status = MagicMock()
        submit_resp.json.return_value = {
            "output": {"task_id": "task-empty", "task_status": "PENDING"},
        }

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.raise_for_status = MagicMock()
        poll_resp.json.return_value = {
            "output": {
                "task_id": "task-empty",
                "task_status": "SUCCEEDED",
                "results": [],
            },
        }

        with patch("plugins.image_gen.dashscope.requests.post", return_value=submit_resp), \
             patch("plugins.image_gen.dashscope.requests.get", return_value=poll_resp), \
             patch("plugins.image_gen.dashscope.time.sleep"):
            provider = DashScopeImageGenProvider()
            result = provider.generate(prompt="test")

        assert result["success"] is False
        assert result["error_type"] == "empty_response"

    def test_url_download_fallback(self):
        """If caching the URL fails, fall back to bare URL."""
        import requests as req_lib
        from plugins.image_gen.dashscope import DashScopeImageGenProvider

        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.raise_for_status = MagicMock()
        submit_resp.json.return_value = {
            "output": {"task_id": "task-url", "task_status": "PENDING"},
        }

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.raise_for_status = MagicMock()
        poll_resp.json.return_value = {
            "output": {
                "task_id": "task-url",
                "task_status": "SUCCEEDED",
                "results": [{"url": "https://dashscope.example.com/img.png"}],
            },
        }

        with patch("plugins.image_gen.dashscope.requests.post", return_value=submit_resp), \
             patch("plugins.image_gen.dashscope.requests.get", return_value=poll_resp), \
             patch("plugins.image_gen.dashscope.time.sleep"), \
             patch(
                 "plugins.image_gen.dashscope.save_url_image",
                 side_effect=req_lib.HTTPError("404"),
             ):
            provider = DashScopeImageGenProvider()
            result = provider.generate(prompt="test")

        assert result["success"] is True
        assert result["image"] == "https://dashscope.example.com/img.png"

    def test_aspect_ratio_mapping(self):
        """Test that aspect ratios are correctly mapped to DashScope sizes."""
        from plugins.image_gen.dashscope import _SIZES

        # wanx2.1-t2i-turbo sizes
        assert _SIZES["wanx2.1-t2i-turbo"]["landscape"] == "1024*576"
        assert _SIZES["wanx2.1-t2i-turbo"]["square"] == "1024*1024"
        assert _SIZES["wanx2.1-t2i-turbo"]["portrait"] == "576*1024"

        # wanx-v1 has limited support (all 1024*1024)
        assert _SIZES["wanx-v1"]["landscape"] == "1024*1024"


# ---------------------------------------------------------------------------
# Registration test
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register(self):
        from plugins.image_gen.dashscope import DashScopeImageGenProvider, register

        mock_ctx = MagicMock()
        register(mock_ctx)
        mock_ctx.register_image_gen_provider.assert_called_once()
        provider = mock_ctx.register_image_gen_provider.call_args[0][0]
        assert isinstance(provider, DashScopeImageGenProvider)
