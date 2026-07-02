"""Tests for the runtime-footer timing breakdown fields.

Covers:
- _format_duration human-friendly formatting
- format_runtime_footer with new api_time / tool_time / overhead_time fields
- build_footer_line wires the new fields through resolve_footer_config
- overhead_time is computed as turn_time - api_time - tool_time (clamped at 0)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add repo root so ``import gateway.runtime_footer`` works without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway.runtime_footer import (  # noqa: E402
    _format_duration,
    build_footer_line,
    format_runtime_footer,
    resolve_footer_config,
)


# ── _format_duration ───────────────────────────────────────────────────────

def test_format_duration_none_and_negative():
    assert _format_duration(None) == ""
    assert _format_duration(-1.0) == ""


def test_format_duration_sub_ten_seconds():
    assert _format_duration(0.4) == "0.4s"
    assert _format_duration(5.7) == "5.7s"
    assert _format_duration(9.99) == "10.0s"


def test_format_duration_seconds_under_minute():
    assert _format_duration(10) == "10s"
    assert _format_duration(42.7) == "43s"
    assert _format_duration(59.4) == "59s"


def test_format_duration_minutes():
    assert _format_duration(60) == "1m00s"
    assert _format_duration(125) == "2m05s"
    assert _format_duration(3599) == "59m59s"


def test_format_duration_hours():
    assert _format_duration(3600) == "1h00m"
    assert _format_duration(3725) == "1h02m"
    assert _format_duration(7325) == "2h02m"


# ── format_runtime_footer ──────────────────────────────────────────────────

def test_footer_skips_missing_fields():
    """A partially-populated footer should not show empty slots."""
    out = format_runtime_footer(
        model="glm-5.2",
        context_tokens=5000,
        context_length=100000,
    )
    assert out == "glm-5.2 · 5%"


def test_footer_renders_api_time_with_label():
    out = format_runtime_footer(
        model="glm-5.2",
        context_tokens=5000,
        context_length=100000,
        api_time=38.2,
    )
    assert "api 38s" in out


def test_footer_renders_tool_time_with_label():
    out = format_runtime_footer(
        model="glm-5.2",
        context_tokens=5000,
        context_length=100000,
        tool_time=3.1,
    )
    assert "tools 3.1s" in out


def test_footer_renders_overhead_time_derived_from_turn():
    out = format_runtime_footer(
        model="glm-5.2",
        context_tokens=5000,
        context_length=100000,
        turn_time=43.0,
        api_time=38.0,
        tool_time=3.0,
    )
    # 43 - 38 - 3 = 2.0s of overhead
    assert "other 2.0s" in out


def test_footer_overhead_clamped_at_zero():
    """If api + tool exceed turn_time, overhead should be 0 (not negative)."""
    out = format_runtime_footer(
        model="glm-5.2",
        context_tokens=5000,
        context_length=100000,
        turn_time=10.0,
        api_time=15.0,
        tool_time=5.0,
    )
    # overhead should not appear if clamped to 0 (since _format_duration("")
    # is empty for 0 — actually 0.0 is truthy in _format_duration; check
    # the value passes through with a 0.0s label).
    # _format_duration(0.0) returns "0.0s" since 0 < 10.
    # So footer will show "other 0.0s".
    assert "other 0.0s" in out


def test_footer_api_calls_singular_and_plural():
    out1 = format_runtime_footer(
        model="x", context_tokens=0, context_length=100,
        api_calls=1,
    )
    out7 = format_runtime_footer(
        model="x", context_tokens=0, context_length=100,
        api_calls=7,
    )
    assert "1 call" in out1
    assert "7 calls" in out7


def test_footer_api_calls_zero_is_omitted():
    """Zero calls shouldn't clutter the footer."""
    out = format_runtime_footer(
        model="x", context_tokens=0, context_length=100,
        api_calls=0,
    )
    assert "0 call" not in out
    assert "0 calls" not in out


def test_footer_full_breakdown():
    """End-to-end with every field populated."""
    out = format_runtime_footer(
        model="MiniMax-M3",
        context_tokens=20000,
        context_length=200000,
        turn_time=70.0,
        api_time=42.0,
        tool_time=15.0,
        api_calls=3,
    )
    assert out == "MiniMax-M3 · 10% · 1m10s · api 42s · tools 15s · other 13s · 3 calls"


# ── resolve_footer_config / build_footer_line ──────────────────────────────

def test_resolve_footer_config_default_fields_when_unset():
    cfg = {"display": {}}
    resolved = resolve_footer_config(cfg, platform_key="discord")
    assert resolved["enabled"] is False
    assert "api_time" in resolved["fields"]
    assert "tool_time" in resolved["fields"]
    assert "overhead_time" in resolved["fields"]


def test_resolve_footer_config_user_override():
    cfg = {"display": {"runtime_footer": {"enabled": True, "fields": ["model", "turn_time"]}}}
    resolved = resolve_footer_config(cfg, platform_key="discord")
    assert resolved["enabled"] is True
    assert resolved["fields"] == ["model", "turn_time"]


def test_build_footer_line_end_to_end():
    cfg = {
        "display": {
            "runtime_footer": {
                "enabled": True,
                "fields": ["model", "context_pct", "turn_time", "api_time", "tool_time", "overhead_time", "api_calls"],
            }
        }
    }
    line = build_footer_line(
        user_config=cfg,
        platform_key="discord",
        model="MiniMax-M3",
        context_tokens=20000,
        context_length=200000,
        turn_time=70.0,
        api_time=42.0,
        tool_time=15.0,
        api_calls=3,
    )
    assert line == "MiniMax-M3 · 10% · 1m10s · api 42s · tools 15s · other 13s · 3 calls"


def test_build_footer_line_disabled_returns_empty():
    cfg = {"display": {"runtime_footer": {"enabled": False}}}
    line = build_footer_line(
        user_config=cfg,
        platform_key="discord",
        model="x",
        context_tokens=0,
        context_length=100,
    )
    assert line == ""


def test_build_footer_line_omits_api_time_when_unset():
    """If api_time is None and turn_time/api_time/tool_time are all None, no
    timing fields should appear. If turn_time is set but api_time isn't, the
    other-computation will clamp to 0.0s and we accept that; this test just
    pins behavior when NO timing data is passed."""
    cfg = {"display": {"runtime_footer": {"enabled": True, "fields": ["model", "api_time"]}}}
    line = build_footer_line(
        user_config=cfg,
        platform_key="discord",
        model="x",
        context_tokens=0,
        context_length=100,
        api_time=None,
    )
    # No api time data, so that field is silently skipped.
    assert line == "x"
