"""Integration: quiet ``-q`` kanban workers stamp fatal errors before exit.

Regression coverage for #46593 — the ``cli.py`` hook that bridges
``run_conversation`` errors to ``record_worker_error_from_env``.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import cli


@pytest.fixture
def kanban_worker_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_KANBAN_GOAL_MODE", raising=False)

    from hermes_cli import kanban_db as kb

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="worker task", assignee="test-worker")
        kb.claim_task(conn, tid)
        run_id = kb._current_run_id(conn, tid)
    finally:
        conn.close()

    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    if run_id is not None:
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return tid


def _drive_quiet_worker(monkeypatch, result):
    """Run ``cli.main(quiet=True)`` with a stubbed CLI whose agent returns
    ``result`` from ``run_conversation``. Returns the SystemExit code."""
    import cli as cli_mod

    def run_conversation(*, user_message, conversation_history):
        return result

    class FakeCLI:
        def __init__(self, **_kwargs):
            self.provider = "test-provider"
            self.model = "test-model"
            self.session_id = "kanban-worker-session"
            self.conversation_history = []
            self._active_agent_route_signature = "same-route"
            self.tool_progress_mode = "off"
            self.agent = SimpleNamespace(
                session_id="kanban-worker-session",
                platform="cli",
                quiet_mode=False,
                suppress_status_output=False,
                stream_delta_callback=object(),
                tool_gen_callback=object(),
                run_conversation=run_conversation,
            )

        def _claim_active_session(self, surface, *, stderr=False):
            return True

        def _ensure_runtime_credentials(self):
            return True

        def _resolve_turn_agent_config(self, effective_query):
            return {
                "signature": "same-route",
                "model": None,
                "runtime": None,
                "request_overrides": None,
            }

        def _init_agent(self, **kwargs):
            return True

    monkeypatch.setattr(cli_mod, "HermesCLI", FakeCLI)
    monkeypatch.setattr(cli_mod.atexit, "register", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "_finalize_single_query", lambda _cli: None)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main(query="work task", quiet=True, toolsets="terminal")
    return exc_info.value.code


def test_quiet_kanban_worker_stamps_error_before_exit(kanban_worker_env, monkeypatch):
    """``cli.main(..., quiet=True)`` must persist worker errors on the open run."""
    from hermes_cli import kanban_db as kb

    worker_error = "litellm.NotFoundError: model 'gpt-bogus' does not exist"
    code = _drive_quiet_worker(
        monkeypatch,
        {"final_response": "", "error": worker_error, "failed": True},
    )
    assert code == 1

    conn = kb.connect()
    try:
        assert kb._open_run_error(conn, kanban_worker_env) == worker_error
    finally:
        conn.close()


def test_quiet_kanban_worker_skips_rate_limit_error(kanban_worker_env, monkeypatch):
    """A rate-limit/billing failure exits with the quota sentinel and must NOT
    stamp the run — that reap is a throttle requeue, not a task failure, so the
    dispatcher ignores any stamped error and stamping would be wasted."""
    from hermes_cli import kanban_db as kb

    code = _drive_quiet_worker(
        monkeypatch,
        {
            "final_response": "",
            "error": "litellm.RateLimitError: quota exhausted",
            "failed": True,
            "failure_reason": "rate_limit",
        },
    )
    assert code == kb.KANBAN_RATE_LIMIT_EXIT_CODE

    conn = kb.connect()
    try:
        assert kb._open_run_error(conn, kanban_worker_env) is None
    finally:
        conn.close()
