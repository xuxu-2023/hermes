"""Per-platform streaming defaults + dashboard exposure.

Streaming is smooth on Telegram (native sendMessageDraft) but flickers on
edit-only platforms like Discord. The shipped defaults encode that:
display.platforms.telegram.streaming=true, .discord.streaming=false. These are
gap-fillers (user values win via deep-merge) and, because the dashboard schema
is generated from DEFAULT_CONFIG, they automatically appear as editable toggles
in the web UI.
"""

from __future__ import annotations


def test_default_per_platform_streaming_flags():
    from hermes_cli.config import DEFAULT_CONFIG
    plats = DEFAULT_CONFIG["display"]["platforms"]
    assert plats["telegram"]["streaming"] is True
    assert plats["discord"]["streaming"] is False


def test_resolver_telegram_on_discord_off_when_global_enabled():
    """With global streaming on, the per-platform defaults make Telegram stream
    and Discord not — matching the platforms' actual streaming quality."""
    from hermes_cli.config import DEFAULT_CONFIG
    from gateway.display_config import resolve_display_setting

    cfg = dict(DEFAULT_CONFIG)
    cfg["streaming"] = {"enabled": True, "transport": "auto"}

    def streams(plat):
        ov = resolve_display_setting(cfg, plat, "streaming")
        # global enabled; None override = follow global (True)
        return True if ov is None else bool(ov)

    assert streams("telegram") is True
    assert streams("discord") is False
    # A platform with no default entry follows the global switch.
    assert streams("slack") is True


def test_user_override_wins_over_default():
    """A user who explicitly enables Discord streaming keeps their value — the
    default false must not clobber it (config deep-merge: user wins)."""
    from hermes_cli.config import DEFAULT_CONFIG, _deep_merge

    user = {"display": {"platforms": {"discord": {"streaming": True}}}}
    merged = _deep_merge(dict(DEFAULT_CONFIG), user)
    assert merged["display"]["platforms"]["discord"]["streaming"] is True
    # Partial override must not wipe the sibling telegram default.
    assert merged["display"]["platforms"]["telegram"]["streaming"] is True


def test_dashboard_schema_exposes_per_platform_streaming():
    """Because the web settings schema is built from DEFAULT_CONFIG, the
    per-platform streaming toggles surface in the dashboard automatically."""
    import pytest
    pytest.importorskip("fastapi")  # web_server requires fastapi/uvicorn
    from hermes_cli.web_server import CONFIG_SCHEMA

    assert "display.platforms.telegram.streaming" in CONFIG_SCHEMA
    assert "display.platforms.discord.streaming" in CONFIG_SCHEMA
    assert CONFIG_SCHEMA["display.platforms.discord.streaming"]["type"] == "boolean"
    # Global streaming controls are exposed too.
    assert "streaming.enabled" in CONFIG_SCHEMA
    assert "streaming.transport" in CONFIG_SCHEMA


# ---------------------------------------------------------------------------
# Global-gate regression tests (issue #53697)
#
# Per-platform ``streaming: true`` (the shipped Telegram default) must NOT
# bypass the global ``streaming.enabled: false`` master switch.  A per-platform
# override can *narrow* (disable a platform when global is on) but must never
# *widen* (enable a platform when global is off).
# ---------------------------------------------------------------------------

def _cfg(streaming: dict):
    """Build a config dict with the given streaming block and DEFAULT display."""
    from hermes_cli.config import DEFAULT_CONFIG
    cfg = dict(DEFAULT_CONFIG)
    cfg["streaming"] = streaming
    return cfg


def _resolve(cfg, platform, streaming_dict):
    """Exercise the production helper that combines global + per-platform."""
    from gateway.config import StreamingConfig
    from gateway.display_config import resolve_streaming_enabled
    scfg = StreamingConfig.from_dict(streaming_dict)
    return resolve_streaming_enabled(cfg, platform, scfg)


def test_global_disabled_blocks_per_platform_streaming():
    """streaming.enabled=false must win over per-platform streaming=true."""
    streaming = {"enabled": False, "transport": "auto"}
    cfg = _cfg(streaming)
    # Telegram ships with streaming=true; global is off → must be False.
    assert _resolve(cfg, "telegram", streaming) is False


def test_global_disabled_blocks_platform_without_override():
    """streaming.enabled=false with no per-platform override → False."""
    streaming = {"enabled": False, "transport": "auto"}
    cfg = _cfg(streaming)
    # Slack has no per-platform default; global is off → False.
    assert _resolve(cfg, "slack", streaming) is False


def test_global_enabled_allows_per_platform_true():
    """streaming.enabled=true + per-platform streaming=true → True."""
    streaming = {"enabled": True, "transport": "auto"}
    cfg = _cfg(streaming)
    assert _resolve(cfg, "telegram", streaming) is True


def test_global_enabled_respects_per_platform_false():
    """streaming.enabled=true but per-platform streaming=false → False."""
    streaming = {"enabled": True, "transport": "auto"}
    cfg = _cfg(streaming)
    # Discord ships with streaming=false.
    assert _resolve(cfg, "discord", streaming) is False


def test_global_enabled_no_override_follows_global():
    """streaming.enabled=true, no per-platform override → True (follow global)."""
    streaming = {"enabled": True, "transport": "auto"}
    cfg = _cfg(streaming)
    assert _resolve(cfg, "slack", streaming) is True


def test_transport_off_kills_streaming_even_with_per_platform_true():
    """streaming.transport=off is a kill-switch that must override per-platform."""
    streaming = {"enabled": True, "transport": "off"}
    cfg = _cfg(streaming)
    assert _resolve(cfg, "telegram", streaming) is False


def test_transport_off_kills_streaming_without_override():
    """streaming.transport=off with no per-platform override → False."""
    streaming = {"enabled": True, "transport": "off"}
    cfg = _cfg(streaming)
    assert _resolve(cfg, "slack", streaming) is False
