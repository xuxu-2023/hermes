from __future__ import annotations

import sys
import types

import pytest


def _install_gateway_run_import_stubs(monkeypatch):
    account_usage = types.ModuleType("agent.account_usage")
    account_usage.fetch_account_usage = lambda *args, **kwargs: None
    account_usage.render_account_usage_lines = lambda *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "agent.account_usage", account_usage)


def test_gateway_startup_lock_is_exclusive(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import gateway.status as status

    assert status.acquire_gateway_startup_lock() is True
    assert status.acquire_gateway_startup_lock() is True

    lock_path = tmp_path / "gateway-startup.lock"
    competing_handle = open(lock_path, "a+", encoding="utf-8")
    try:
        assert status._try_acquire_file_lock(competing_handle) is False
    finally:
        competing_handle.close()

    status.release_gateway_startup_lock()
    assert status.acquire_gateway_startup_lock() is True
    status.release_gateway_startup_lock()


@pytest.mark.asyncio
async def test_start_gateway_exits_when_startup_lock_is_busy(monkeypatch):
    _install_gateway_run_import_stubs(monkeypatch)
    import gateway.run as run

    calls: list[str] = []

    monkeypatch.setattr(
        "gateway.status.acquire_gateway_startup_lock",
        lambda: calls.append("startup_lock") or False,
    )
    monkeypatch.setattr(
        "gateway.status.acquire_gateway_runtime_lock",
        lambda: calls.append("runtime_lock") or True,
    )
    monkeypatch.setattr(run, "sync_skills", lambda quiet=True: None, raising=False)

    assert await run.start_gateway(replace=True) is False
    assert calls == ["startup_lock"]


@pytest.mark.asyncio
async def test_start_gateway_releases_startup_lock_after_pid_claim(monkeypatch):
    _install_gateway_run_import_stubs(monkeypatch)
    import gateway.run as run

    calls: list[str] = []

    class FakeRunner:
        should_exit_cleanly = True
        should_exit_with_failure = False
        exit_reason = None
        adapters = {}
        _signal_initiated_shutdown = False

        def __init__(self, config):
            calls.append("runner_init")

        async def start(self):
            calls.append("runner_start")
            return True

    monkeypatch.setattr("gateway.status.acquire_gateway_startup_lock", lambda: calls.append("startup_lock") or True)
    monkeypatch.setattr("gateway.status.release_gateway_startup_lock", lambda: calls.append("release_startup"))
    monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)
    monkeypatch.setattr("gateway.status.acquire_gateway_runtime_lock", lambda: calls.append("runtime_lock") or True)
    monkeypatch.setattr("gateway.status.write_pid_file", lambda: calls.append("write_pid"))
    monkeypatch.setattr(run, "GatewayRunner", FakeRunner)
    monkeypatch.setattr(run, "sync_skills", lambda quiet=True: None, raising=False)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda **kwargs: None)
    monkeypatch.setattr("tools.mcp_tool.discover_mcp_tools", lambda: None)
    monkeypatch.setattr(run, "_ensure_windows_gateway_venv_imports", lambda: None)

    assert await run.start_gateway(replace=True) is True
    assert calls.index("write_pid") < calls.index("release_startup")
    assert calls.index("release_startup") < calls.index("runner_start")
    assert "runtime_lock" in calls
