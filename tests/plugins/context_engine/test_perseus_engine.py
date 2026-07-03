"""Tests for the Perseus context engine plugin."""

import json
import os
import tempfile

import pytest

from agent.context_engine import ContextEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_engine():
    """Load the Perseus engine, skipping if unavailable."""
    from plugins.context_engine import load_context_engine

    engine = load_context_engine("perseus")
    if engine is None:
        pytest.skip("Perseus engine not loadable")
    if not engine.is_available():
        pytest.skip("perseus CLI not available")
    return engine


# ---------------------------------------------------------------------------
# ABC contract
# ---------------------------------------------------------------------------


class TestPerseusEngineABC:
    """Verify the engine satisfies the ContextEngine ABC."""

    def test_is_context_engine(self):
        engine = _load_engine()
        assert isinstance(engine, ContextEngine)

    def test_name(self):
        engine = _load_engine()
        assert engine.name == "perseus"

    def test_has_required_attributes(self):
        engine = _load_engine()
        assert hasattr(engine, "last_prompt_tokens")
        assert hasattr(engine, "last_completion_tokens")
        assert hasattr(engine, "last_total_tokens")
        assert hasattr(engine, "threshold_tokens")
        assert hasattr(engine, "context_length")
        assert engine.context_length > 0

    def test_should_compress_returns_bool(self):
        engine = _load_engine()
        result = engine.should_compress(1000)
        assert isinstance(result, bool)

    def test_compress_returns_list(self):
        engine = _load_engine()
        msgs = [{"role": "user", "content": "hello"}]
        result = engine.compress(msgs)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_update_from_response(self):
        engine = _load_engine()
        engine.update_from_response(
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        )
        assert engine.last_prompt_tokens == 100
        assert engine.last_total_tokens == 150

    def test_get_status(self):
        engine = _load_engine()
        status = engine.get_status()
        assert status["engine"] == "perseus"
        assert "perseus_available" in status
        assert "resolved_directives" in status


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class TestPerseusEngineTools:
    """Verify the Perseus-specific tools work correctly."""

    def test_get_tool_schemas(self):
        engine = _load_engine()
        schemas = engine.get_tool_schemas()
        names = {s["name"] for s in schemas}
        assert "perseus_render" in names
        assert "perseus_list" in names

    def test_perseus_list(self):
        engine = _load_engine()
        result = engine.handle_tool_call("perseus_list", {})
        data = json.loads(result)
        assert "commands" in data
        assert "perseus" in data["commands"].lower()

    def test_perseus_render_file(self):
        engine = _load_engine()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            f.write("Hello from test file\nWith multiple lines")
            path = f.name
        try:
            result = engine.handle_tool_call(
                "perseus_render", {"source": path}
            )
            data = json.loads(result)
            assert "result" in data
            assert "Hello from test file" in data["result"]
        finally:
            os.unlink(path)

    def test_perseus_render_missing_source(self):
        engine = _load_engine()
        result = engine.handle_tool_call("perseus_render", {})
        data = json.loads(result)
        assert "error" in data

    def test_unknown_tool(self):
        engine = _load_engine()
        result = engine.handle_tool_call("nonexistent_tool", {})
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestPerseusEngineSessionLifecycle:
    """Verify session lifecycle works correctly."""

    def test_on_session_start(self):
        engine = _load_engine()
        engine.on_session_start("test-session", hermes_home="/tmp")
        # Should not crash — directive resolution is best-effort
        assert engine._resolved_count >= 0

    def test_on_session_reset(self):
        engine = _load_engine()
        engine._resolved_count = 5
        engine.last_prompt_tokens = 1000
        engine.on_session_reset()
        assert engine._resolved_count == 0
        assert engine.last_prompt_tokens == 0

    def test_on_session_end(self):
        engine = _load_engine()
        # Should not crash with no compressor
        engine.on_session_end("test-session", [])


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestPerseusEngineDiscovery:
    """Verify the plugin system discovers this engine."""

    def test_discovered_by_plugin_system(self):
        from plugins.context_engine import discover_context_engines

        engines = discover_context_engines()
        names = {name for name, _, _ in engines}
        assert "perseus" in names

    def test_available_reported_correctly(self):
        from plugins.context_engine import discover_context_engines

        engines = discover_context_engines()
        for name, desc, avail in engines:
            if name == "perseus":
                assert avail is True
                assert "Perseus" in desc
