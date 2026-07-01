"""Tests for doctor vision native fast-path override (issue #47153).

When the main model is vision-capable and the native fast path is active,
`hermes doctor` should NOT warn about a missing vision system dependency.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# _vision_native_fast_path_active
# ---------------------------------------------------------------------------


class TestVisionNativeFastPathActive:
    """Unit tests for _vision_native_fast_path_active()."""

    def test_returns_true_when_native_mode(self):
        from hermes_cli.doctor import _vision_native_fast_path_active

        cfg = {"model": {"provider": "minimax-oauth", "default": "MiniMax-M3"}}
        with (
            patch("hermes_cli.config.load_config", return_value=cfg),
            patch(
                "agent.image_routing.decide_image_input_mode",
                return_value="native",
            ),
        ):
            assert _vision_native_fast_path_active() is True

    def test_returns_false_when_text_mode(self):
        from hermes_cli.doctor import _vision_native_fast_path_active

        cfg = {"model": {"provider": "openrouter", "default": "qwen/qwen3-235b"}}
        with (
            patch("hermes_cli.config.load_config", return_value=cfg),
            patch(
                "agent.image_routing.decide_image_input_mode",
                return_value="text",
            ),
        ):
            assert _vision_native_fast_path_active() is False

    def test_returns_false_when_no_model_config(self):
        from hermes_cli.doctor import _vision_native_fast_path_active

        with patch("hermes_cli.config.load_config", return_value={}):
            assert _vision_native_fast_path_active() is False

    def test_returns_false_when_config_load_raises(self):
        from hermes_cli.doctor import _vision_native_fast_path_active

        with patch("hermes_cli.config.load_config", side_effect=Exception("boom")):
            assert _vision_native_fast_path_active() is False

    def test_returns_false_when_provider_empty(self):
        from hermes_cli.doctor import _vision_native_fast_path_active

        cfg = {"model": {"provider": "", "default": "some-model"}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            assert _vision_native_fast_path_active() is False

    def test_returns_false_when_model_empty(self):
        from hermes_cli.doctor import _vision_native_fast_path_active

        cfg = {"model": {"provider": "openrouter", "default": ""}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            assert _vision_native_fast_path_active() is False


# ---------------------------------------------------------------------------
# _apply_doctor_tool_availability_overrides — vision path
# ---------------------------------------------------------------------------


class TestDoctorVisionOverride:
    """Integration tests for the vision override in doctor tool availability."""

    def test_vision_moved_to_available_when_native_fast_path(self):
        from hermes_cli.doctor import _apply_doctor_tool_availability_overrides

        unavailable = [
            {"name": "vision", "missing_vars": []},
            {"name": "tts", "missing_vars": ["OPENAI_API_KEY"]},
        ]
        with patch(
            "hermes_cli.doctor._vision_native_fast_path_active",
            return_value=True,
        ):
            avail, unavail = _apply_doctor_tool_availability_overrides(
                ["terminal"], unavailable,
            )
        assert "vision" in avail
        assert "terminal" in avail
        vision_items = [u for u in unavail if u.get("name") == "vision"]
        assert vision_items == []
        tts_items = [u for u in unavail if u.get("name") == "tts"]
        assert len(tts_items) == 1

    def test_vision_stays_unavailable_when_no_native_path(self):
        from hermes_cli.doctor import _apply_doctor_tool_availability_overrides

        unavailable = [
            {"name": "vision", "missing_vars": []},
        ]
        with patch(
            "hermes_cli.doctor._vision_native_fast_path_active",
            return_value=False,
        ):
            avail, unavail = _apply_doctor_tool_availability_overrides(
                ["terminal"], unavailable,
            )
        assert "vision" not in avail
        assert any(u.get("name") == "vision" for u in unavail)


# ---------------------------------------------------------------------------
# _doctor_tool_availability_detail — vision suffix
# ---------------------------------------------------------------------------


class TestDoctorVisionDetail:
    """Tests for the vision detail suffix in doctor output."""

    def test_vision_detail_when_native_fast_path(self):
        from hermes_cli.doctor import _doctor_tool_availability_detail

        with patch(
            "hermes_cli.doctor._vision_native_fast_path_active",
            return_value=True,
        ):
            detail = _doctor_tool_availability_detail("vision")
        assert "native fast path" in detail.lower()

    def test_vision_detail_empty_when_no_native_path(self):
        from hermes_cli.doctor import _doctor_tool_availability_detail

        with patch(
            "hermes_cli.doctor._vision_native_fast_path_active",
            return_value=False,
        ):
            detail = _doctor_tool_availability_detail("vision")
        assert detail == ""

    def test_kanban_detail_unchanged(self):
        from hermes_cli.doctor import _doctor_tool_availability_detail

        # kanban detail should still work regardless of vision state
        with patch.dict("os.environ", {}, clear=False):
            if "HERMES_KANBAN_TASK" in __import__("os").environ:
                __import__("os").environ.pop("HERMES_KANBAN_TASK")
            detail = _doctor_tool_availability_detail("kanban")
        assert "runtime-gated" in detail.lower()
