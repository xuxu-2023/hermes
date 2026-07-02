"""Tests for entry-point (pip-installed) plugin discovery in the CLI (#53898).

The runtime PluginManager scans the ``hermes_agent.plugins`` entry-point
group, but the CLI's ``_discover_all_plugins`` previously walked directories
only. As a result a pip-installed plugin loaded at gateway runtime yet
``hermes plugins list``/``enable`` reported it as "not installed or bundled".
"""

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def empty_dirs(tmp_path):
    """Patch both directory sources to empty dirs so only entry points show."""
    with patch("hermes_cli.plugins.get_bundled_plugins_dir", return_value=tmp_path / "nope_bundled"), \
         patch("hermes_cli.plugins_cmd._plugins_dir", return_value=tmp_path / "nope_user"):
        yield


def test_entrypoint_plugin_discovered(empty_dirs):
    from hermes_cli.plugins_cmd import _discover_all_plugins

    with patch(
        "hermes_cli.plugins_cmd._scan_entry_point_plugins",
        return_value=[("my-pip-plugin", "1.2.3", "A pip plugin", "my-pip-plugin")],
    ):
        entries = _discover_all_plugins()

    by_key = {e[5]: e for e in entries}
    assert "my-pip-plugin" in by_key
    name, version, description, source, dir_path, key = by_key["my-pip-plugin"]
    assert name == "my-pip-plugin"
    assert version == "1.2.3"
    assert source == "pip"
    assert dir_path is None


def test_entrypoint_plugin_resolves_and_enables(empty_dirs):
    from hermes_cli.plugins_cmd import _resolve_plugin_key

    with patch(
        "hermes_cli.plugins_cmd._scan_entry_point_plugins",
        return_value=[("my-pip-plugin", "1.2.3", "", "my-pip-plugin")],
    ):
        # Before the fix, _resolve_plugin_key returned None for entry-point
        # plugins, so `hermes plugins enable` aborted with "not installed".
        assert _resolve_plugin_key("my-pip-plugin") == "my-pip-plugin"


class _FakeDist:
    def __init__(self, version, summary):
        self.version = version
        self.metadata = {"Summary": summary}


class _FakeEP:
    def __init__(self, name, value, dist, group="hermes_agent.plugins"):
        self.name = name
        self.value = value
        self.group = group
        self.dist = dist


class _FakeEntryPoints(list):
    """Mimic Python 3.12+ entry_points() with a .select(group=...) method."""

    def select(self, group=None):
        return [ep for ep in self if ep.group == group]


def test_entrypoint_plugin_discovered_via_metadata(empty_dirs):
    """End-to-end proof through importlib.metadata (no patching of the new
    helper) — fails on main because the CLI never enumerated entry points."""
    import importlib.metadata

    from hermes_cli.plugins_cmd import _discover_all_plugins

    fake_eps = _FakeEntryPoints([
        _FakeEP(
            "my-pip-plugin",
            "my_pkg.plugin:register",
            _FakeDist("3.4.5", "Pip-installed Hermes plugin"),
        ),
        _FakeEP("unrelated", "x:y", _FakeDist("0", ""), group="console_scripts"),
    ])

    with patch.object(importlib.metadata, "entry_points", return_value=fake_eps):
        entries = _discover_all_plugins()

    by_key = {e[5]: e for e in entries}
    assert "my-pip-plugin" in by_key
    assert "unrelated" not in by_key
    assert by_key["my-pip-plugin"][1] == "3.4.5"
    assert by_key["my-pip-plugin"][3] == "pip"


def test_directory_plugin_wins_on_key_collision(tmp_path):
    """A directory plugin with the same key must take precedence over the
    entry-point one (mirrors the loader's dir-over-entrypoint ordering)."""
    import yaml

    from hermes_cli.plugins_cmd import _discover_all_plugins

    user_dir = tmp_path / "user"
    d = user_dir / "shared-key"
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(
        yaml.dump({"name": "shared-key", "version": "9.9.9"}), encoding="utf-8"
    )

    with patch("hermes_cli.plugins.get_bundled_plugins_dir", return_value=tmp_path / "nope"), \
         patch("hermes_cli.plugins_cmd._plugins_dir", return_value=user_dir), \
         patch(
             "hermes_cli.plugins_cmd._scan_entry_point_plugins",
             return_value=[("shared-key", "1.0.0", "", "shared-key")],
         ):
        entries = _discover_all_plugins()

    by_key = {e[5]: e for e in entries}
    assert by_key["shared-key"][1] == "9.9.9"  # directory version, not 1.0.0
    assert by_key["shared-key"][3] != "pip"
