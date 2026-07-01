"""Tests for /steer handling in tui_gateway's ``command.dispatch``.

This is the server-side dispatch path for ``/steer``. The Ink TUI handles
``/steer`` client-side (ui-tui/src/app/slash/commands/core.ts), so in practice
this branch is reached by clients that dispatch slash commands server-side —
notably the Electron desktop app, where ``/steer`` is an ``exec`` command.

The contract under test:

* A steer is injected into the live turn ONLY when the session is actually
  running. The handler returns an ``exec`` confirmation in that case.
* When the session is idle (no running turn) the text is returned as a plain
  next-turn ``send`` — even if the session still holds an agent object. This
  matches the handler's "no active run, treat as next-turn message" intent and
  avoids claiming a mid-turn injection that cannot happen.
* Empty / whitespace-only args are a usage error.
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home


@pytest.fixture()
def server(hermes_home):
    with patch.dict(
        "sys.modules",
        {
            "hermes_cli.env_loader": MagicMock(),
            "hermes_cli.banner": MagicMock(),
        },
    ):
        mod = importlib.import_module("tui_gateway.server")
        yield mod
        mod._sessions.clear()
        mod._pending.clear()
        mod._answers.clear()


def _make_session(server, agent=None, running=True):
    sid = "sid-test"
    s = {
        "session_key": "tui-steer-session-1",
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": running,
        "attached_images": [],
        "cols": 120,
    }
    if agent is not None:
        s["agent"] = agent
    server._sessions[sid] = s
    return sid


def _call(server, method, **params):
    handler = server._methods[method]
    return handler(1, params)


class _AcceptingAgent:
    def __init__(self):
        self.seen = []

    def steer(self, text):
        self.seen.append(text)
        return True


class _RaisingAgent:
    def __init__(self):
        self.seen = []

    def steer(self, text):
        self.seen.append(text)
        raise RuntimeError("boom")


# ── usage / validation ────────────────────────────────────────────────


def test_steer_empty_arg_is_usage_error(server):
    sid = _make_session(server, agent=_AcceptingAgent())
    r = _call(server, "command.dispatch", name="steer", arg="", session_id=sid)
    assert "error" in r
    assert r["error"]["code"] == 4004


def test_steer_whitespace_only_is_usage_error(server):
    sid = _make_session(server, agent=_AcceptingAgent())
    r = _call(server, "command.dispatch", name="steer", arg="   ", session_id=sid)
    assert "error" in r
    assert r["error"]["code"] == 4004


# ── running turn: inject + confirm ────────────────────────────────────


def test_steer_running_agent_returns_exec_confirmation(server):
    agent = _AcceptingAgent()
    sid = _make_session(server, agent=agent, running=True)
    r = _call(server, "command.dispatch", name="steer", arg="check the logs", session_id=sid)
    result = r["result"]
    assert result["type"] == "exec"
    assert "queued" in result["output"].lower()
    assert agent.seen == ["check the logs"]


def test_steer_strips_surrounding_whitespace_before_injecting(server):
    """The injected text (and the echo) is trimmed, matching the session.steer RPC."""
    agent = _AcceptingAgent()
    sid = _make_session(server, agent=agent, running=True)
    r = _call(server, "command.dispatch", name="steer", arg="  check the logs  ", session_id=sid)
    assert agent.seen == ["check the logs"]
    assert "  check" not in r["result"]["output"]


def test_steer_running_agent_exception_returns_exec_not_dropped(server):
    """Defensive: if steer() raises, report it as exec — never a false 'queued'."""
    sid = _make_session(server, agent=_RaisingAgent(), running=True)
    r = _call(server, "command.dispatch", name="steer", arg="please stop", session_id=sid)
    result = r["result"]
    assert result["type"] == "exec"
    assert "fail" in result["output"].lower()


# ── idle session: genuine next-turn message, NOT a mid-turn injection ──


def test_steer_idle_agent_falls_back_to_send_without_injecting(server):
    """A session that still holds an agent but is NOT running must not inject —
    it returns a plain next-turn send and never calls steer()."""
    agent = _AcceptingAgent()
    sid = _make_session(server, agent=agent, running=False)
    r = _call(server, "command.dispatch", name="steer", arg="hello", session_id=sid)
    result = r["result"]
    assert result["type"] == "send"
    assert result["message"] == "hello"
    assert agent.seen == []  # steer() never called when idle


def test_steer_no_agent_falls_back_to_plain_send(server):
    sid = _make_session(server, agent=None, running=True)
    r = _call(server, "command.dispatch", name="steer", arg="hello", session_id=sid)
    result = r["result"]
    assert result["type"] == "send"
    assert result["message"] == "hello"
