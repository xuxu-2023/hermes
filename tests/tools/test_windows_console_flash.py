"""Regression tests for Windows console window flash (issue #42544)."""

import subprocess
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def local_env():
    """Create a LocalEnvironment without running its full constructor."""
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment.__new__(LocalEnvironment)
    env.cwd = "/tmp"
    env.env = {}
    return env


@pytest.fixture
def popen_capture(monkeypatch):
    """Capture subprocess.Popen kwargs while returning a minimal process mock."""
    captured = {}

    def mock_popen(*args, **kwargs):
        captured.update(kwargs)
        proc = MagicMock()
        proc.pid = 12345
        proc.returncode = 0
        return proc

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    return captured


@pytest.fixture
def run_bash_deps(monkeypatch):
    import tools.environments.local as local_mod

    monkeypatch.setattr(local_mod, "_find_bash", lambda: "/usr/bin/bash")
    monkeypatch.setattr(local_mod, "_make_run_env", lambda env: {})
    monkeypatch.setattr(local_mod, "_resolve_safe_cwd", lambda cwd: cwd)
    return local_mod


class TestRunBashCreationFlags:
    """Verify _run_bash uses detached, non-breakaway flags on Windows."""

    def test_run_bash_uses_detach_flags_on_windows(
        self, local_env, run_bash_deps, popen_capture, monkeypatch
    ):
        """Windows bash invocations must include DETACHED_PROCESS to prevent console flash."""
        from hermes_cli import _subprocess_compat as sc
        from hermes_cli._subprocess_compat import (
            _CREATE_BREAKAWAY_FROM_JOB,
            _CREATE_NEW_PROCESS_GROUP,
            _CREATE_NO_WINDOW,
            _DETACHED_PROCESS,
        )

        monkeypatch.setattr(run_bash_deps, "_IS_WINDOWS", True)
        monkeypatch.setattr(sc, "IS_WINDOWS", True)

        local_env._run_bash("echo hello", timeout=10)

        flags = popen_capture.get("creationflags", 0)
        assert flags & _DETACHED_PROCESS, (
            f"creationflags must include DETACHED_PROCESS (0x{_DETACHED_PROCESS:08x}) "
            f"to prevent console flash. Got flags=0x{flags:08x}"
        )
        assert flags & _CREATE_NO_WINDOW, (
            f"creationflags must include CREATE_NO_WINDOW (0x{_CREATE_NO_WINDOW:08x}). "
            f"Got flags=0x{flags:08x}"
        )
        assert flags & _CREATE_NEW_PROCESS_GROUP, (
            f"creationflags must include CREATE_NEW_PROCESS_GROUP "
            f"(0x{_CREATE_NEW_PROCESS_GROUP:08x}). Got flags=0x{flags:08x}"
        )
        assert not flags & _CREATE_BREAKAWAY_FROM_JOB, (
            "interactive terminal commands should not request CREATE_BREAKAWAY_FROM_JOB; "
            "some Windows job objects reject breakaway and would make every command fail"
        )

    def test_run_bash_no_creationflags_on_posix(self, local_env, run_bash_deps, popen_capture, monkeypatch):
        """On non-Windows, _run_bash must NOT pass creationflags."""
        monkeypatch.setattr(run_bash_deps, "_IS_WINDOWS", False)
        monkeypatch.setattr(run_bash_deps.os, "getpgid", lambda pid: 12345)

        local_env._run_bash("echo hello", timeout=10)

        # On POSIX, creationflags should not be present or be 0
        flags = popen_capture.get("creationflags", 0)
        assert flags == 0, (
            f"creationflags must be 0 on POSIX, got 0x{flags:08x}"
        )

    def test_run_bash_does_not_use_windows_hide_flags(
        self, local_env, run_bash_deps, popen_capture, monkeypatch
    ):
        """_run_bash must NOT use windows_hide_flags — that was the old bug.
        windows_hide_flags only sets CREATE_NO_WINDOW without DETACHED_PROCESS."""
        from hermes_cli import _subprocess_compat as sc

        monkeypatch.setattr(run_bash_deps, "_IS_WINDOWS", True)
        monkeypatch.setattr(sc, "IS_WINDOWS", True)

        local_env._run_bash("echo hello", timeout=10)

        flags = popen_capture.get("creationflags", 0)
        hide_flags_simulated = sc.windows_hide_flags()

        # The actual flags must be MORE than just hide_flags
        # (hide_flags only has CREATE_NO_WINDOW; we need DETACHED_PROCESS too)
        assert flags != hide_flags_simulated, (
            f"_run_bash must use windows_detach_flags(), not windows_hide_flags(). "
            f"Got flags=0x{flags:08x}, hide_flags=0x{hide_flags_simulated:08x}"
        )

    def test_run_bash_stdout_pipe_preserved_on_windows(
        self, local_env, run_bash_deps, popen_capture, monkeypatch
    ):
        """Verify that stdout=PIPE and stderr=STDOUT are still passed alongside
        DETACHED_PROCESS, ensuring output capture works correctly."""
        from hermes_cli import _subprocess_compat as sc

        monkeypatch.setattr(run_bash_deps, "_IS_WINDOWS", True)
        monkeypatch.setattr(sc, "IS_WINDOWS", True)

        local_env._run_bash("echo hello", timeout=10)

        assert popen_capture.get("stdout") == subprocess.PIPE, (
            "stdout must be PIPE for output capture"
        )
        assert popen_capture.get("stderr") == subprocess.STDOUT, (
            "stderr must be STDOUT for merged output capture"
        )
