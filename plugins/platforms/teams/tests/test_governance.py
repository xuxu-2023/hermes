"""Integration tests for the Teams adapter governance wiring (DLP + audit)."""

from __future__ import annotations

import asyncio

import pytest

from plugins.platforms.teams.adapter import TeamsAdapter
from plugins.platforms.teams.dlp import DlpConfig


class _FakeApp:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))
        return type("R", (), {"id": "m1"})()

    async def reply(self, *_a, **_k):
        raise RuntimeError("no threading in test")


def _adapter(*, dlp: bool, audit: str | None) -> TeamsAdapter:
    """Construct an adapter without the heavy gateway __init__."""
    a = TeamsAdapter.__new__(TeamsAdapter)
    a._dlp_cfg = DlpConfig(enabled=dlp)
    a._audit_channel = audit or ""
    a._app = _FakeApp()
    a.format_message = lambda c: c  # identity for the test
    a.truncate_message = lambda c: [c]
    return a


def test_send_redacts_outbound():
    a = _adapter(dlp=True, audit=None)
    asyncio.run(a.send("CHAT1", "mail me at a.b@example.com, key sk-proj-ABCDEFGH12345678"))
    chat, text = a._app.sent[0]
    assert chat == "CHAT1"
    assert "a.b@example.com" not in text and "sk-proj-" not in text
    assert "[REDACTED:email]" in text and "[REDACTED:secret]" in text


def test_audit_mirror_sends_redacted_copy():
    a = _adapter(dlp=True, audit="AUDIT")
    asyncio.run(a.send("CHAT1", "secret sk-ant-api03-zzzzzzzzzz here"))
    # two sends: the reply to CHAT1 + the audit mirror to AUDIT
    targets = [c for c, _ in a._app.sent]
    assert "CHAT1" in targets and "AUDIT" in targets
    audit_text = next(t for c, t in a._app.sent if c == "AUDIT")
    assert "sk-ant-" not in audit_text and "[REDACTED:secret]" in audit_text
    assert audit_text.startswith("🧾 Reply in CHAT1")


def test_audit_loop_guard_skips_own_channel():
    a = _adapter(dlp=False, audit="AUDIT")
    asyncio.run(a.send("AUDIT", "a message posted into the audit channel itself"))
    # only the direct send, no self-mirror
    assert [c for c, _ in a._app.sent] == ["AUDIT"]


def test_no_audit_when_unconfigured():
    a = _adapter(dlp=True, audit=None)
    asyncio.run(a.send("CHAT1", "hello"))
    assert len(a._app.sent) == 1  # reply only, no mirror


def test_dlp_text_helper_for_cards():
    a = TeamsAdapter.__new__(TeamsAdapter)
    a._dlp_cfg = DlpConfig(enabled=True)
    out = a._dlp_text("approve: curl -H 'Authorization: Bearer abcdef0123456789xyz' x")
    assert "Bearer abcdef" not in out and "[REDACTED:secret]" in out
    a._dlp_cfg = DlpConfig(enabled=False)
    assert a._dlp_text("sk-proj-ABCDEFGH12345678") == "sk-proj-ABCDEFGH12345678"
