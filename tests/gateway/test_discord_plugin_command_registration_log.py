"""Regression tests: plugin slash command registration failures must be logged.

Pre-fix behavior: the plugin auto-register loop in
``_register_slash_commands()`` had a silent ``except Exception: pass``
around ``tree.add_command()``. If a plugin's slash command failed to
register (name conflict, discord.py validation error, internal
state, etc.) the failure was invisible. The plugin showed as
enabled in ``hermes plugins list`` but never appeared in Discord's
slash picker, and there was no log entry to explain why.

Post-fix behavior: the same exception path emits a
``logger.warning(...)`` with the plugin name, exception type, and
message. The next debugging session sees the actual cause instead
of guessing at a silent failure.

These tests pin the fix.
"""

import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import PlatformConfig


# ---------------------------------------------------------------------------
# Discord module mock — borrowed from test_discord_slash_auth.py so this
# file runs on machines without discord.py installed.
# ---------------------------------------------------------------------------


def _ensure_discord_mock():
    if "discord" in sys.modules and hasattr(sys.modules["discord"], "__file__"):
        return  # real discord installed

    if sys.modules.get("discord") is None:
        discord_mod = MagicMock()
        discord_mod.Intents.default.return_value = MagicMock()
        discord_mod.Client = MagicMock
        discord_mod.File = MagicMock
        discord_mod.DMChannel = type("DMChannel", (), {})
        discord_mod.Thread = type("Thread", (), {})
        discord_mod.ForumChannel = type("ForumChannel", (), {})
        discord_mod.ui = SimpleNamespace(
            View=object,
            button=lambda *a, **k: (lambda fn: fn),
            Button=object,
        )
        discord_mod.ButtonStyle = SimpleNamespace(
            success=1, primary=2, danger=3,
            green=1, blurple=2, red=3, grey=4, secondary=5,
        )
        discord_mod.Color = SimpleNamespace(
            orange=lambda: 1, green=lambda: 2, blue=lambda: 3, red=lambda: 4,
        )
        discord_mod.Interaction = object
        discord_mod.Embed = MagicMock

        class _FakeGroup:
            def __init__(self, *, name, description, parent=None):
                self.name = name
                self.description = description
                self.callback = None
                self.parent = parent
                self.default_permissions = None

        class _FakeCommand:
            def __init__(self, *, name, description, callback=None, parent=None):
                self.name = name
                self.description = description
                self.callback = callback
                self.parent = parent
                self.default_permissions = None

        discord_mod.app_commands = SimpleNamespace(
            describe=lambda **kwargs: (lambda fn: fn),
            choices=lambda **kwargs: (lambda fn: fn),
            autocomplete=lambda **kwargs: (lambda fn: fn),
            Choice=lambda **kwargs: SimpleNamespace(**kwargs),
            Group=_FakeGroup,
            Command=_FakeCommand,
        )
        discord_mod.opus = SimpleNamespace(is_loaded=lambda: True)

        ext_mod = MagicMock()
        commands_mod = MagicMock()
        commands_mod.Bot = MagicMock
        ext_mod.commands = commands_mod

        sys.modules["discord"] = discord_mod
        sys.modules.setdefault("discord.ext", ext_mod)
        sys.modules.setdefault("discord.ext.commands", commands_mod)


_ensure_discord_mock()

import hermes_cli.commands as hermes_commands  # noqa: E402
from plugins.platforms.discord.adapter import DiscordAdapter  # noqa: E402


@pytest.fixture
def adapter():
    """DiscordAdapter with a stubbed _client and a stubbed tree.

    The tree is a separate fixture so individual tests can swap in a
    tree that raises or one that tracks add_command calls.
    """
    config = PlatformConfig(enabled=True, token="***")
    a = DiscordAdapter(config)
    a._client = SimpleNamespace(
        user=SimpleNamespace(id=99999, name="HermesBot"),
        guilds=[],
    )
    return a


@pytest.fixture
def fake_tree():
    """Tree that records add_command calls and can be told to raise.

    Uses MagicMock for the ``command()`` decorator path that the built-in
    slash commands use. The plugin auto-register loop calls
    ``tree.add_command()`` directly (no decorator) so we control that
    with ``side_effect`` for failure simulation.
    """
    tree = MagicMock()
    tree.get_commands.return_value = []
    return tree


def _patch_plugin_entries(monkeypatch, entries):
    """Stub _iter_plugin_command_entries to return ``entries``."""
    monkeypatch.setattr(
        hermes_commands, "_iter_plugin_command_entries",
        lambda: iter(entries),
    )


def _patch_command_registry(monkeypatch, commands):
    """Stub COMMAND_REGISTRY to return ``commands`` for the built-in loop.

    Returns an empty list by default — tests that need built-in commands
    to register should pass them explicitly.
    """
    monkeypatch.setattr(hermes_commands, "COMMAND_REGISTRY", commands)


# ---------------------------------------------------------------------------
# The fix: failure to register a plugin command emits a warning
# ---------------------------------------------------------------------------


def _add_command_names(fake_tree):
    """Extract command names from tree.add_command call args.

    The plugin auto-register loop calls ``tree.add_command(auto_cmd)``
    with a positional ``Command`` object. Built-in commands use the
    ``@tree.command(name=...)`` decorator which doesn't call
    ``add_command`` at all. So in practice all calls to
    ``add_command`` are plugin commands.
    """
    names = []
    for c in fake_tree.add_command.call_args_list:
        # Positional arg: the Command object. Fall back to kwargs.
        if c.args:
            cmd = c.args[0]
            name = getattr(cmd, "name", None)
            if name is not None:
                names.append(name)
        elif "name" in c.kwargs:
            names.append(c.kwargs["name"])
    return names


def test_plugin_command_registration_failure_logs_warning(
    adapter, fake_tree, monkeypatch, caplog
):
    """If ``tree.add_command`` raises for a plugin command, log a WARNING
    with the plugin name and exception details — do not silently swallow.
    """
    adapter._client.tree = fake_tree
    fake_tree.add_command.side_effect = ValueError("name already in use")

    _patch_command_registry(monkeypatch, [])
    _patch_plugin_entries(monkeypatch, [
        ("myplugin", "My plugin command", "args <N>"),
    ])

    with caplog.at_level(logging.WARNING, logger="plugins.discord_platform.adapter"):
        adapter._register_slash_commands()

    # The warning must be present, must name the plugin, and must
    # include the exception type + message.
    plugin_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and "myplugin" in r.getMessage()
        and "failed to register" in r.getMessage()
    ]
    assert plugin_warnings, (
        f"Expected a WARNING about myplugin registration failure, "
        f"got: {[r.getMessage() for r in caplog.records]}"
    )
    msg = plugin_warnings[0].getMessage()
    assert "ValueError" in msg, f"Exception type missing from warning: {msg!r}"
    assert "name already in use" in msg, f"Exception message missing: {msg!r}"


def test_plugin_command_registration_success_does_not_warn(
    adapter, fake_tree, monkeypatch, caplog
):
    """On the happy path, plugin command registration must NOT emit a
    WARNING — the new code path is silent when nothing goes wrong.
    """
    adapter._client.tree = fake_tree
    fake_tree.add_command.return_value = None  # succeeds

    _patch_command_registry(monkeypatch, [])
    _patch_plugin_entries(monkeypatch, [
        ("myplugin", "My plugin command", "args <N>"),
    ])

    with caplog.at_level(logging.WARNING, logger="plugins.discord_platform.adapter"):
        adapter._register_slash_commands()

    failure_warnings = [
        r for r in caplog.records
        if "failed to register" in r.getMessage()
    ]
    assert not failure_warnings, (
        f"Happy path should not emit registration-failure warnings, "
        f"got: {[r.getMessage() for r in failure_warnings]}"
    )
    # Confirm the plugin's add_command was actually called
    # (positive control — the test would pass trivially if the
    # function returned early without calling add_command at all).
    assert "myplugin" in _add_command_names(fake_tree), (
        "Plugin command was never passed to tree.add_command"
    )


def test_multiple_plugins_one_failing_logs_only_for_the_failing_one(
    adapter, fake_tree, monkeypatch, caplog
):
    """If one plugin's command raises, the other plugin's command
    should still register. The warning should name only the failing one.
    """
    adapter._client.tree = fake_tree
    # The first add_command call raises; subsequent calls succeed.
    fake_tree.add_command.side_effect = [
        ValueError("conflict with built-in"),
        None,  # second plugin registers fine
    ]

    _patch_command_registry(monkeypatch, [])
    _patch_plugin_entries(monkeypatch, [
        ("badplugin", "Conflicts with built-in", "args <N>"),
        ("goodplugin", "Registers fine", "args <N>"),
    ])

    with caplog.at_level(logging.WARNING, logger="plugins.discord_platform.adapter"):
        adapter._register_slash_commands()

    failure_warnings = [
        r for r in caplog.records
        if "failed to register" in r.getMessage()
    ]
    assert len(failure_warnings) == 1
    assert "badplugin" in failure_warnings[0].getMessage()
    assert "goodplugin" not in failure_warnings[0].getMessage()
    # Both plugins were attempted — positive control.
    add_names = _add_command_names(fake_tree)
    assert "badplugin" in add_names
    assert "goodplugin" in add_names
