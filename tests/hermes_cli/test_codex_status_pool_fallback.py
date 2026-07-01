"""Tests for get_codex_auth_status() pool fallback path.

Regression: #47066 — hermes status showed false negative for OpenAI Codex
authentication when credentials existed only in the credential pool JSON
(not exposed as entry attributes).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.auth import get_codex_auth_status


class TestCodexStatusPoolFallback:
    """When the singleton store is incomplete but pool has credentials,
    get_codex_auth_status must report logged_in=True."""

    def test_pool_entry_has_access_token_reports_logged_in(self):
        """Pool entry with access_token attribute → logged_in via pool check."""
        entry = SimpleNamespace(
            runtime_api_key=None,
            access_token="eyJhbG...OTl9.sig",
            label="test",
            last_refresh=None,
        )
        pool = MagicMock()
        pool.has_credentials.return_value = True
        pool.select.return_value = entry

        with patch("agent.credential_pool.load_pool", return_value=pool):
            result = get_codex_auth_status()

        assert result["logged_in"] is True
        assert "pool:" in result.get("source", "")

    def test_auth_error_with_pool_token_reports_logged_in(self):
        """When resolve_codex_runtime_credentials raises AuthError but
        _pool_codex_access_token returns a token, report logged_in=True."""
        from hermes_cli.auth import AuthError

        with (
            patch("agent.credential_pool.load_pool", return_value=None),
            patch(
                "hermes_cli.auth.resolve_codex_runtime_credentials",
                side_effect=AuthError(
                    "Codex auth is missing access_token.",
                    provider="openai-codex",
                    code="codex_auth_missing_access_token",
                    relogin_required=True,
                ),
            ),
            patch(
                "hermes_cli.auth._pool_codex_access_token",
                return_value="eyJhbG...OTl9.sig",
            ),
        ):
            result = get_codex_auth_status()

        assert result["logged_in"] is True
        assert result.get("source") == "credential_pool"

    def test_auth_error_without_pool_token_reports_not_logged_in(self):
        """When both resolve and pool fallback fail, report logged_in=False."""
        from hermes_cli.auth import AuthError

        with (
            patch("agent.credential_pool.load_pool", return_value=None),
            patch(
                "hermes_cli.auth.resolve_codex_runtime_credentials",
                side_effect=AuthError(
                    "No Codex credentials stored.",
                    provider="openai-codex",
                    code="codex_auth_missing",
                    relogin_required=True,
                ),
            ),
            patch(
                "hermes_cli.auth._pool_codex_access_token",
                return_value="",
            ),
        ):
            result = get_codex_auth_status()

        assert result["logged_in"] is False
        assert "error" in result

    def test_resolve_success_returns_logged_in(self):
        """When resolve_codex_runtime_credentials succeeds, report logged_in."""
        with (
            patch("agent.credential_pool.load_pool", return_value=None),
            patch(
                "hermes_cli.auth.resolve_codex_runtime_credentials",
                return_value={
                    "api_key": "test-key",
                    "source": "singleton",
                    "auth_mode": "chatgpt",
                    "last_refresh": None,
                },
            ),
        ):
            result = get_codex_auth_status()

        assert result["logged_in"] is True
        assert result.get("source") == "singleton"
