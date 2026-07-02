"""Hermetic tests for the Vaultwarden / Bitwarden PM integration.

We never hit a real vault — subprocess is mocked so the suite stays
fast and offline-safe.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from agent.secret_sources import vaultwarden as vw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_ITEM = {
    "object": "item",
    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "name": "Hermes",
    "type": 1,
    "fields": [
        {"name": "OPENROUTER_API_KEY", "value": "sk-or-test", "type": 1},
        {"name": "ANTHROPIC_API_KEY", "value": "sk-ant-test", "type": 1},
        {"name": "123INVALID", "value": "should-be-skipped", "type": 0},
        {"name": "", "value": "also-skipped", "type": 0},
    ],
}

_FAKE_SESSION = "fake-session-token-abc123"


def _make_ok_proc(payload=None) -> mock.MagicMock:
    proc = mock.MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(payload if payload is not None else _FAKE_ITEM)
    proc.stderr = ""
    return proc


def _make_fail_proc(returncode=1, stderr="error msg") -> mock.MagicMock:
    proc = mock.MagicMock()
    proc.returncode = returncode
    proc.stdout = ""
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# find_bw
# ---------------------------------------------------------------------------


class TestFindBw:
    def test_finds_system_bw(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vw.shutil, "which", lambda _: "/usr/bin/bw")
        monkeypatch.setattr(vw, "_hermes_bin_dir", lambda: tmp_path / "bin")
        result = vw.find_bw()
        assert result == Path("/usr/bin/bw")

    def test_managed_bin_wins_over_system(self, tmp_path, monkeypatch):
        managed = tmp_path / "bin" / "bw"
        managed.parent.mkdir(parents=True)
        managed.write_text("#!/bin/sh\necho fake")
        managed.chmod(0o755)
        monkeypatch.setattr(vw, "_hermes_bin_dir", lambda: tmp_path / "bin")
        monkeypatch.setattr(vw.shutil, "which", lambda _: "/usr/bin/bw")
        result = vw.find_bw()
        assert result == managed

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vw, "_hermes_bin_dir", lambda: tmp_path / "bin")
        monkeypatch.setattr(vw.shutil, "which", lambda _: None)
        assert vw.find_bw() is None


# ---------------------------------------------------------------------------
# _is_valid_env_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("OPENROUTER_API_KEY", True),
        ("_PRIVATE", True),
        ("MY_VAR_123", True),
        ("123INVALID", False),
        ("", False),
        ("has space", False),
        ("has-dash", False),
    ],
)
def test_is_valid_env_name(name, expected):
    assert vw._is_valid_env_name(name) is expected


# ---------------------------------------------------------------------------
# fetch_vaultwarden_secrets
# ---------------------------------------------------------------------------


class TestFetchVaultwardenSecrets:
    def setup_method(self):
        vw._CACHE.clear()

    def test_returns_parsed_fields(self, tmp_path):
        with mock.patch("subprocess.run", return_value=_make_ok_proc()):
            secrets, warnings = vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=False,
                home_path=tmp_path,
            )
        assert secrets["OPENROUTER_API_KEY"] == "sk-or-test"
        assert secrets["ANTHROPIC_API_KEY"] == "sk-ant-test"
        assert "123INVALID" not in secrets
        assert any("123INVALID" in w for w in warnings)

    def test_raises_on_empty_session(self):
        with pytest.raises(RuntimeError, match="session token is empty"):
            vw.fetch_vaultwarden_secrets(
                session="",
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=False,
            )

    def test_raises_on_empty_item_name(self):
        with pytest.raises(RuntimeError, match="item_name is empty"):
            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="",
                binary=Path("/usr/bin/bw"),
                use_cache=False,
            )

    def test_raises_when_bw_fails(self, tmp_path):
        with mock.patch("subprocess.run", return_value=_make_fail_proc(1, "Not logged in")):
            with pytest.raises(RuntimeError, match="bw exited 1"):
                vw.fetch_vaultwarden_secrets(
                    session=_FAKE_SESSION,
                    item_name="Hermes",
                    binary=Path("/usr/bin/bw"),
                    use_cache=False,
                    home_path=tmp_path,
                )

    def test_raises_when_bw_not_found(self, monkeypatch):
        monkeypatch.setattr(vw, "find_bw", lambda: None)
        with pytest.raises(RuntimeError, match="bw binary not found"):
            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=None,
                use_cache=False,
            )

    def test_in_process_cache_prevents_second_subprocess_call(self, tmp_path):
        with mock.patch("subprocess.run", return_value=_make_ok_proc()) as mock_run:
            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=True,
                home_path=tmp_path,
            )
            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=True,
                home_path=tmp_path,
            )
        assert mock_run.call_count == 1

    def test_disk_cache_survives_process_cache_clear(self, tmp_path):
        with mock.patch("subprocess.run", return_value=_make_ok_proc()) as mock_run:
            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=True,
                home_path=tmp_path,
            )
            vw._CACHE.clear()
            secrets, _ = vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=True,
                home_path=tmp_path,
            )
        assert mock_run.call_count == 1
        assert "OPENROUTER_API_KEY" in secrets

    def test_expired_disk_cache_triggers_refetch(self, tmp_path):
        with mock.patch("subprocess.run", return_value=_make_ok_proc()) as mock_run:
            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                cache_ttl_seconds=1,
                use_cache=True,
                home_path=tmp_path,
            )
            vw._CACHE.clear()
            cache_file = vw._disk_cache_path(tmp_path)
            payload = json.loads(cache_file.read_text())
            payload["fetched_at"] = time.time() - 10
            cache_file.write_text(json.dumps(payload))

            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                cache_ttl_seconds=1,
                use_cache=True,
                home_path=tmp_path,
            )
        assert mock_run.call_count == 2

    def test_item_with_no_fields_returns_empty_with_warning(self, tmp_path):
        item = {**_FAKE_ITEM, "fields": []}
        with mock.patch("subprocess.run", return_value=_make_ok_proc(item)):
            secrets, warnings = vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=False,
                home_path=tmp_path,
            )
        assert secrets == {}
        assert any("fields" in w for w in warnings)

    def test_raises_on_non_json_output(self, tmp_path):
        proc = mock.MagicMock()
        proc.returncode = 0
        proc.stdout = "not json"
        proc.stderr = ""
        with mock.patch("subprocess.run", return_value=proc):
            with pytest.raises(RuntimeError, match="non-JSON"):
                vw.fetch_vaultwarden_secrets(
                    session=_FAKE_SESSION,
                    item_name="Hermes",
                    binary=Path("/usr/bin/bw"),
                    use_cache=False,
                    home_path=tmp_path,
                )

    def test_timeout_raises_runtime_error(self, tmp_path):
        with mock.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("bw", 30)
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                vw.fetch_vaultwarden_secrets(
                    session=_FAKE_SESSION,
                    item_name="Hermes",
                    binary=Path("/usr/bin/bw"),
                    use_cache=False,
                    home_path=tmp_path,
                )


# ---------------------------------------------------------------------------
# apply_vaultwarden_secrets
# ---------------------------------------------------------------------------


class TestApplyVaultwardenSecrets:
    def setup_method(self):
        vw._CACHE.clear()

    def test_disabled_returns_ok_without_calling_bw(self):
        with mock.patch("subprocess.run") as mock_run:
            result = vw.apply_vaultwarden_secrets(enabled=False)
        assert result.ok
        mock_run.assert_not_called()

    def test_missing_session_env_returns_error(self, monkeypatch):
        monkeypatch.delenv("BW_SESSION", raising=False)
        result = vw.apply_vaultwarden_secrets(
            enabled=True,
            session_env="BW_SESSION",
            item_name="Hermes",
        )
        assert not result.ok
        assert "BW_SESSION" in result.error

    def test_missing_item_name_returns_error(self, monkeypatch):
        monkeypatch.setenv("BW_SESSION", _FAKE_SESSION)
        result = vw.apply_vaultwarden_secrets(
            enabled=True,
            item_name="",
        )
        assert not result.ok
        assert "item_name" in result.error

    def test_missing_binary_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BW_SESSION", _FAKE_SESSION)
        monkeypatch.setattr(vw, "find_bw", lambda: None)
        result = vw.apply_vaultwarden_secrets(
            enabled=True,
            item_name="Hermes",
            home_path=tmp_path,
        )
        assert not result.ok
        assert "not found" in result.error

    def test_applies_new_secrets_to_environ(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BW_SESSION", _FAKE_SESSION)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(vw, "find_bw", lambda: Path("/usr/bin/bw"))
        with mock.patch("subprocess.run", return_value=_make_ok_proc()):
            result = vw.apply_vaultwarden_secrets(
                enabled=True,
                item_name="Hermes",
                home_path=tmp_path,
            )
        assert result.ok
        assert "OPENROUTER_API_KEY" in result.applied
        assert "ANTHROPIC_API_KEY" in result.applied
        assert os.environ["OPENROUTER_API_KEY"] == "sk-or-test"

    def test_skips_existing_env_vars_when_override_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BW_SESSION", _FAKE_SESSION)
        monkeypatch.setenv("OPENROUTER_API_KEY", "existing-value")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(vw, "find_bw", lambda: Path("/usr/bin/bw"))
        with mock.patch("subprocess.run", return_value=_make_ok_proc()):
            result = vw.apply_vaultwarden_secrets(
                enabled=True,
                item_name="Hermes",
                override_existing=False,
                home_path=tmp_path,
            )
        assert "OPENROUTER_API_KEY" in result.skipped
        assert "ANTHROPIC_API_KEY" in result.applied
        assert os.environ["OPENROUTER_API_KEY"] == "existing-value"

    def test_overrides_existing_when_flag_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BW_SESSION", _FAKE_SESSION)
        monkeypatch.setenv("OPENROUTER_API_KEY", "old-value")
        monkeypatch.setattr(vw, "find_bw", lambda: Path("/usr/bin/bw"))
        with mock.patch("subprocess.run", return_value=_make_ok_proc()):
            result = vw.apply_vaultwarden_secrets(
                enabled=True,
                item_name="Hermes",
                override_existing=True,
                home_path=tmp_path,
            )
        assert "OPENROUTER_API_KEY" in result.applied
        assert os.environ["OPENROUTER_API_KEY"] == "sk-or-test"

    def test_session_env_itself_never_overwritten(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BW_SESSION", _FAKE_SESSION)
        item = {
            **_FAKE_ITEM,
            "fields": [
                {"name": "BW_SESSION", "value": "should-not-apply", "type": 1},
                {"name": "SOME_KEY", "value": "val", "type": 1},
            ],
        }
        monkeypatch.setattr(vw, "find_bw", lambda: Path("/usr/bin/bw"))
        with mock.patch("subprocess.run", return_value=_make_ok_proc(item)):
            result = vw.apply_vaultwarden_secrets(
                enabled=True,
                item_name="Hermes",
                session_env="BW_SESSION",
                home_path=tmp_path,
            )
        assert "BW_SESSION" in result.skipped
        assert os.environ["BW_SESSION"] == _FAKE_SESSION

    def test_fetch_error_returns_error_result(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BW_SESSION", _FAKE_SESSION)
        monkeypatch.setattr(vw, "find_bw", lambda: Path("/usr/bin/bw"))
        with mock.patch(
            "subprocess.run", return_value=_make_fail_proc(1, "Session expired")
        ):
            result = vw.apply_vaultwarden_secrets(
                enabled=True,
                item_name="Hermes",
                home_path=tmp_path,
            )
        assert not result.ok
        assert "Session expired" in result.error


# ---------------------------------------------------------------------------
# Disk cache format
# ---------------------------------------------------------------------------


class TestDiskCache:
    def setup_method(self):
        vw._CACHE.clear()

    def test_disk_cache_written_with_mode_0600(self, tmp_path):
        with mock.patch("subprocess.run", return_value=_make_ok_proc()):
            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=True,
                home_path=tmp_path,
            )
        cache_path = vw._disk_cache_path(tmp_path)
        assert cache_path.exists()
        mode = cache_path.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_cache_key_prefixed_with_vw(self, tmp_path):
        with mock.patch("subprocess.run", return_value=_make_ok_proc()):
            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=True,
                home_path=tmp_path,
            )
        cache_path = vw._disk_cache_path(tmp_path)
        payload = json.loads(cache_path.read_text())
        assert payload["key"].startswith("vw|")

    def test_reset_cache_for_tests_clears_both_layers(self, tmp_path):
        with mock.patch("subprocess.run", return_value=_make_ok_proc()):
            vw.fetch_vaultwarden_secrets(
                session=_FAKE_SESSION,
                item_name="Hermes",
                binary=Path("/usr/bin/bw"),
                use_cache=True,
                home_path=tmp_path,
            )
        assert vw._CACHE
        vw._reset_cache_for_tests(tmp_path)
        assert not vw._CACHE
        assert not vw._disk_cache_path(tmp_path).exists()
