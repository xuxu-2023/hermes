"""Tests for _provider_supports_explicit_api_mode guard function.

Refs: #47719 — named custom provider blocks (e.g. providers.deepseek with
provider: custom) had their api_mode silently discarded because the guard
only accepted literal "custom" or "custom:xxx" slugs.
"""

import pytest

from hermes_cli.runtime_provider import _provider_supports_explicit_api_mode


class TestProviderSupportsExplicitApiMode:
    """Verify the guard correctly allows/blocks api_mode persistence."""

    # ── When no configured_provider is recorded ──────────────────────────

    def test_no_configured_provider_returns_true(self):
        """No configured_provider → always allow (nothing to leak from)."""
        assert _provider_supports_explicit_api_mode("openai", None) is True

    def test_empty_configured_provider_returns_true(self):
        assert _provider_supports_explicit_api_mode("openai", "") is True

    # ── Normal built-in provider matching ────────────────────────────────

    def test_matching_builtin_provider_returns_true(self):
        assert _provider_supports_explicit_api_mode("openai", "openai") is True

    def test_mismatched_builtin_provider_returns_false(self):
        """Stale api_mode from a different built-in must not leak."""
        assert _provider_supports_explicit_api_mode("openai", "anthropic") is False

    def test_case_insensitive_match(self):
        assert _provider_supports_explicit_api_mode("OpenAI", "openai") is True

    # ── Custom provider: must always return True ─────────────────────────

    def test_custom_provider_with_custom_slug(self):
        assert _provider_supports_explicit_api_mode("custom", "custom") is True

    def test_custom_provider_with_custom_colon_slug(self):
        assert _provider_supports_explicit_api_mode("custom", "custom:myproxy") is True

    def test_custom_provider_with_named_block(self):
        """#47719: named provider blocks like providers.deepseek with
        provider: custom must be accepted."""
        assert _provider_supports_explicit_api_mode("custom", "deepseek") is True

    def test_custom_provider_with_any_named_block(self):
        """Any named block under providers: should be accepted when
        runtime provider is custom."""
        for name in ("deepseek", "openai", "anthropic", "my-proxy", "local-llm"):
            assert _provider_supports_explicit_api_mode("custom", name) is True, (
                f"custom provider should accept configured_provider={name!r}"
            )

    # ── Non-custom provider with custom configured ───────────────────────

    def test_non_custom_runtime_with_custom_configured_returns_false(self):
        """If runtime is 'openai' but configured is 'custom', block."""
        assert _provider_supports_explicit_api_mode("openai", "custom") is False

    def test_non_custom_runtime_with_custom_colon_configured_returns_false(self):
        assert _provider_supports_explicit_api_mode("openai", "custom:foo") is False
