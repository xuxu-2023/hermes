"""Regression tests for hermes_cli/fallback_config.py — JSON string inputs.

Bug: #51560 — `hermes config set fallback_providers '[{...}]'` writes the
value as a JSON-encoded string in config.yaml. The original parser dropped
string inputs entirely (`else: return []`), so the entire fallback chain
silently became empty and the gateway returned a canned
"Provider authentication failed" message instead of failing over.

The fix: `_iter_fallback_entries` now attempts a `json.loads` on a string
input and recurses once (to handle double-encoded values), then falls back
to the original behaviour. This restores failover for users whose
config.yaml already contains the broken shape.
"""

from __future__ import annotations

import pytest

from hermes_cli.fallback_config import (
    _iter_fallback_entries,
    get_fallback_chain,
)


# ---------------------------------------------------------------------------
# Regression: string inputs (the bug)
# ---------------------------------------------------------------------------

class TestJsonStringInputs:
    """#51560: when fallback_providers is a JSON string in config.yaml."""

    def test_json_string_list_of_dicts_parses(self):
        """A single-encoded JSON list is parsed and entries returned."""
        raw = '[{"provider": "anthropic", "model": "claude-sonnet-4-6"}]'
        assert _iter_fallback_entries(raw) == [
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        ]

    def test_json_string_multiple_entries_preserves_order(self):
        raw = (
            '[{"provider": "anthropic", "model": "claude-sonnet-4-6"},'
            ' {"provider": "gemini", "model": "gemini-3.1-pro-preview"}]'
        )
        chain = _iter_fallback_entries(raw)
        assert [e["provider"] for e in chain] == ["anthropic", "gemini"]
        assert chain[0]["model"] == "claude-sonnet-4-6"
        assert chain[1]["model"] == "gemini-3.1-pro-preview"

    def test_json_string_single_dict_wrapped(self):
        """A JSON string of a dict is wrapped in a list, like the dict form."""
        raw = '{"provider": "anthropic", "model": "claude-sonnet-4-6"}'
        assert _iter_fallback_entries(raw) == [
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        ]

    def test_double_encoded_string_recurses(self):
        """A JSON-encoded string of a list is still parseable."""
        # Outer string contains escaped quotes for a JSON list
        raw = '"[{\\"provider\\": \\"anthropic\\", \\"model\\": \\"claude-sonnet-4-6\\"}]"'
        chain = _iter_fallback_entries(raw)
        assert chain == [
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        ]

    def test_invalid_json_string_returns_empty_chain(self):
        """Non-JSON strings don't raise; the chain is empty (silent drop)."""
        assert _iter_fallback_entries("not json at all") == []
        assert _iter_fallback_entries("") == []

    def test_empty_json_list_returns_empty(self):
        """An empty JSON list returns an empty chain."""
        assert _iter_fallback_entries("[]") == []

    def test_get_fallback_chain_with_json_string(self):
        """The public entry point returns the chain when the value is a JSON string."""
        cfg = {
            "fallback_providers": (
                '[{"provider": "anthropic", "model": "claude-sonnet-4-6"},'
                ' {"provider": "gemini", "model": "gemini-3.1-pro-preview"}]'
            ),
        }
        chain = get_fallback_chain(cfg)
        assert [e["provider"] for e in chain] == ["anthropic", "gemini"]

    def test_get_fallback_chain_json_string_with_legacy_key(self):
        """A JSON-string `fallback_providers` is merged with a list-form
        `fallback_model` (legacy)."""
        cfg = {
            "fallback_providers": (
                '[{"provider": "anthropic", "model": "claude-sonnet-4-6"}]'
            ),
            "fallback_model": {"provider": "nous", "model": "Hermes-4"},
        }
        chain = get_fallback_chain(cfg)
        providers = [e["provider"] for e in chain]
        assert "anthropic" in providers
        assert "nous" in providers

    def test_fallback_model_as_json_string_also_works(self):
        """The legacy `fallback_model` key is parsed through the same helper."""
        cfg = {
            "fallback_providers": [
                {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"},
            ],
            "fallback_model": '{"provider": "nous", "model": "Hermes-4"}',
        }
        chain = get_fallback_chain(cfg)
        providers = [e["provider"] for e in chain]
        assert "openrouter" in providers
        assert "nous" in providers


# ---------------------------------------------------------------------------
# Pre-existing shapes must still work (regression guard)
# ---------------------------------------------------------------------------

class TestExistingShapesUnchanged:
    """Make sure the JSON-string support didn't break the working inputs."""

    def test_list_of_dicts_unchanged(self):
        raw = [
            {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"},
            {"provider": "nous", "model": "Hermes-4"},
        ]
        chain = _iter_fallback_entries(raw)
        assert len(chain) == 2
        assert chain[0]["provider"] == "openrouter"

    def test_single_dict_wrapped(self):
        raw = {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"}
        assert _iter_fallback_entries(raw) == [raw]

    def test_none_returns_empty(self):
        assert _iter_fallback_entries(None) == []

    def test_integer_returns_empty(self):
        assert _iter_fallback_entries(42) == []

    def test_get_fallback_chain_with_no_keys(self):
        assert get_fallback_chain({}) == []
        assert get_fallback_chain(None) == []

    def test_get_fallback_chain_dedupes_legacy(self):
        cfg = {
            "fallback_providers": [
                {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"},
            ],
            "fallback_model": {"provider": "OpenRouter", "model": "anthropic/claude-sonnet-4.6"},
        }
        chain = get_fallback_chain(cfg)
        assert len(chain) == 1
        assert chain[0]["provider"].lower() == "openrouter"

    def test_normalized_base_url_preserved(self):
        raw = [
            {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "base_url": "https://api.example.com/v1/",
            }
        ]
        chain = _iter_fallback_entries(raw)
        assert chain[0]["base_url"] == "https://api.example.com/v1"
