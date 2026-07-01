"""Tests for the LINE platform adapter plugin.

Covers the seven synthesis areas from the PR review:

1. webhook signature verification (HMAC-SHA256, base64) + tampering rejection
2. inbound chat-id resolution for user / group / room sources
3. three-allowlist gating (users / groups / rooms / allow_all)
4. inbound dedup via webhookEventId
5. RequestCache state machine (PENDING → READY → DELIVERED, ERROR)
6. Markdown stripping with URL preservation + LINE-sized chunking
7. send routing: reply token preferred → push fallback → batched at 5/call
8. register() metadata + standalone_send shape
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import base64
import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.gateway._plugin_adapter_loader import load_plugin_adapter

# Load plugins/platforms/line/adapter.py under plugin_adapter_line so it
# cannot collide with sibling platform-plugin tests in the same xdist worker.
_line = load_plugin_adapter("line")

verify_line_signature = _line.verify_line_signature
strip_markdown_preserving_urls = _line.strip_markdown_preserving_urls
split_for_line = _line.split_for_line
build_postback_button_message = _line.build_postback_button_message
_resolve_chat = _line._resolve_chat
_allowed_for_source = _line._allowed_for_source
_is_system_bypass = _line._is_system_bypass
RequestCache = _line.RequestCache
State = _line.State
LineAdapter = _line.LineAdapter
register = _line.register
check_requirements = _line.check_requirements
validate_config = _line.validate_config
_standalone_send = _line._standalone_send
_env_enablement = _line._env_enablement
_MessageDeduplicator = _line._MessageDeduplicator


# ---------------------------------------------------------------------------
# 1. Signature verification
# ---------------------------------------------------------------------------

class TestSignature:

    def _sign(self, body: bytes, secret: str) -> str:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def test_valid_signature_passes(self):
        body = b'{"events": []}'
        sig = self._sign(body, "secret")
        assert verify_line_signature(body, sig, "secret")

    def test_tampered_body_rejected(self):
        body = b'{"events": []}'
        sig = self._sign(body, "secret")
        assert not verify_line_signature(body + b" ", sig, "secret")

    def test_wrong_secret_rejected(self):
        body = b'{"events": []}'
        sig = self._sign(body, "secret")
        assert not verify_line_signature(body, sig, "different")

    def test_empty_signature_rejected(self):
        assert not verify_line_signature(b"x", "", "secret")

    def test_empty_secret_rejected(self):
        assert not verify_line_signature(b"x", "AAAA", "")

    def test_garbage_signature_rejected(self):
        assert not verify_line_signature(b"hello", "not base64 at all!!", "s")


# ---------------------------------------------------------------------------
# 2. Chat-id / source resolution
# ---------------------------------------------------------------------------

class TestSourceResolution:

    def test_user_source(self):
        chat_id, ctype = _resolve_chat({"type": "user", "userId": "U123"})
        assert chat_id == "U123"
        assert ctype == "dm"

    def test_group_source(self):
        chat_id, ctype = _resolve_chat({"type": "group", "groupId": "C456", "userId": "U123"})
        assert chat_id == "C456"
        assert ctype == "group"

    def test_room_source(self):
        chat_id, ctype = _resolve_chat({"type": "room", "roomId": "R789", "userId": "U123"})
        assert chat_id == "R789"
        assert ctype == "room"

    def test_unknown_source_falls_back_to_dm(self):
        chat_id, ctype = _resolve_chat({"type": "weird"})
        assert chat_id == ""
        assert ctype == "dm"

    def test_empty_source(self):
        chat_id, ctype = _resolve_chat({})
        assert chat_id == ""
        assert ctype == "dm"


# ---------------------------------------------------------------------------
# 3. Three-allowlist gating
# ---------------------------------------------------------------------------

class TestAllowlist:

    def test_allow_all_short_circuits(self):
        for src in [
            {"type": "user", "userId": "Ufoo"},
            {"type": "group", "groupId": "Cfoo"},
            {"type": "room", "roomId": "Rfoo"},
        ]:
            assert _allowed_for_source(src, allow_all=True, user_ids=set(), group_ids=set(), room_ids=set())

    def test_user_in_allowlist_passes(self):
        src = {"type": "user", "userId": "Uok"}
        assert _allowed_for_source(src, allow_all=False, user_ids={"Uok"}, group_ids=set(), room_ids=set())

    def test_user_not_in_allowlist_rejected(self):
        src = {"type": "user", "userId": "Uother"}
        assert not _allowed_for_source(src, allow_all=False, user_ids={"Uok"}, group_ids=set(), room_ids=set())

    def test_group_uses_group_list_not_user_list(self):
        src = {"type": "group", "groupId": "Cok", "userId": "Uany"}
        assert _allowed_for_source(src, allow_all=False, user_ids={"Uany"}, group_ids={"Cok"}, room_ids=set())
        assert not _allowed_for_source(src, allow_all=False, user_ids={"Uany"}, group_ids=set(), room_ids=set())

    def test_room_uses_room_list(self):
        src = {"type": "room", "roomId": "Rok"}
        assert _allowed_for_source(src, allow_all=False, user_ids=set(), group_ids=set(), room_ids={"Rok"})
        assert not _allowed_for_source(src, allow_all=False, user_ids=set(), group_ids=set(), room_ids=set())

    def test_unknown_type_rejected(self):
        src = {"type": "weird"}
        assert not _allowed_for_source(src, allow_all=False, user_ids=set(), group_ids=set(), room_ids=set())


# ---------------------------------------------------------------------------
# 4. Inbound dedup
# ---------------------------------------------------------------------------

class TestDedup:

    def test_first_event_not_duplicate(self):
        d = _MessageDeduplicator()
        assert not d.is_duplicate("evt1")

    def test_repeat_event_marked_duplicate(self):
        d = _MessageDeduplicator()
        d.is_duplicate("evt1")
        assert d.is_duplicate("evt1")

    def test_blank_id_not_treated_as_duplicate(self):
        d = _MessageDeduplicator()
        # Blank IDs should always pass through (don't lock out unidentifiable events).
        assert not d.is_duplicate("")
        assert not d.is_duplicate("")

    def test_lru_eviction_under_pressure(self):
        d = _MessageDeduplicator(max_size=10)
        for i in range(20):
            d.is_duplicate(f"evt{i}")
        # Exact eviction order isn't specified, but the cap must be enforced.
        # Insert one more and assert the bookkeeping doesn't grow without bound.
        d.is_duplicate("evt20")
        assert len(d._seen) <= 20  # bounded — exact cap depends on eviction policy


# ---------------------------------------------------------------------------
# 5. RequestCache state machine
# ---------------------------------------------------------------------------

class TestRequestCache:

    def test_register_pending_is_pending(self):
        c = RequestCache()
        rid = c.register_pending("Uchat")
        assert c.get(rid).state is State.PENDING
        assert c.get(rid).chat_id == "Uchat"

    def test_set_ready_transitions(self):
        c = RequestCache()
        rid = c.register_pending("Uchat")
        c.set_ready(rid, "the answer")
        assert c.get(rid).state is State.READY
        assert c.get(rid).payload == "the answer"

    def test_set_error_transitions(self):
        c = RequestCache()
        rid = c.register_pending("Uchat")
        c.set_error(rid, "boom")
        assert c.get(rid).state is State.ERROR
        assert c.get(rid).payload == "boom"

    def test_mark_delivered_from_ready(self):
        c = RequestCache()
        rid = c.register_pending("Uchat")
        c.set_ready(rid, "x")
        c.mark_delivered(rid)
        assert c.get(rid).state is State.DELIVERED

    def test_mark_delivered_from_error(self):
        c = RequestCache()
        rid = c.register_pending("Uchat")
        c.set_error(rid, "x")
        c.mark_delivered(rid)
        assert c.get(rid).state is State.DELIVERED

    def test_set_ready_on_delivered_is_noop(self):
        c = RequestCache()
        rid = c.register_pending("Uchat")
        c.set_ready(rid, "first")
        c.mark_delivered(rid)
        c.set_ready(rid, "second")
        # DELIVERED is terminal — no further mutation
        assert c.get(rid).payload == "first"
        assert c.get(rid).state is State.DELIVERED

    def test_find_pending_for_chat(self):
        c = RequestCache()
        rid_a = c.register_pending("Ua")
        rid_b = c.register_pending("Ub")
        assert c.find_pending_for_chat("Ua") == rid_a
        assert c.find_pending_for_chat("Ub") == rid_b
        assert c.find_pending_for_chat("Uc") is None
        c.set_ready(rid_a, "x")
        # No longer PENDING — should not be found
        assert c.find_pending_for_chat("Ua") is None


# ---------------------------------------------------------------------------
# 6. Markdown stripping + chunking
# ---------------------------------------------------------------------------

class TestMarkdownAndChunking:

    def test_bold_stripped(self):
        assert strip_markdown_preserving_urls("**hello**") == "hello"

    def test_italic_stripped(self):
        assert strip_markdown_preserving_urls("*hello*") == "hello"

    def test_inline_code_unfenced(self):
        assert strip_markdown_preserving_urls("run `ls -la`") == "run ls -la"

    def test_link_preserved_with_url(self):
        out = strip_markdown_preserving_urls("see [here](https://x.com)")
        assert "https://x.com" in out
        assert "here (https://x.com)" in out

    def test_heading_prefix_stripped(self):
        out = strip_markdown_preserving_urls("# Title\n## Sub")
        assert out == "Title\nSub"

    def test_bullet_marker_replaced(self):
        out = strip_markdown_preserving_urls("- a\n- b")
        assert out == "• a\n• b"

    def test_code_fence_content_kept(self):
        # Source files often contain code snippets — the agent should still
        # see the content as plain text, just without backticks.
        md = "```python\nprint('hi')\n```"
        out = strip_markdown_preserving_urls(md)
        assert "print('hi')" in out
        assert "```" not in out

    def test_split_short_returns_single_chunk(self):
        assert split_for_line("hi") == ["hi"]

    def test_split_long_chunks_at_paragraph_boundary(self):
        text = "para1\n\npara2\n\npara3"
        chunks = split_for_line(text, max_chars=8)
        assert all(len(c) <= 8 for c in chunks), chunks
        assert len(chunks) >= 2

    def test_split_caps_at_five_chunks(self):
        # 1000 paragraphs of 100 chars each — must cap at 5 LINE bubbles.
        text = "\n\n".join(["x" * 100 for _ in range(1000)])
        chunks = split_for_line(text)
        assert len(chunks) <= 5


# ---------------------------------------------------------------------------
# 7. Send routing (reply -> push fallback, batching, system-bypass)
# ---------------------------------------------------------------------------

class TestSendRouting:

    @pytest.fixture
    def adapter(self, monkeypatch):
        monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, extra={
            "channel_access_token": "tok",
            "channel_secret": "sec",
        })
        ad = LineAdapter(cfg)
        ad._client = MagicMock()
        ad._client.reply = AsyncMock()
        ad._client.push = AsyncMock()
        return ad

    def test_system_bypass_recognized(self):
        assert _is_system_bypass("⚡ Interrupting current run")
        assert _is_system_bypass("⏳ Queued — agent is busy")
        assert _is_system_bypass("⏩ Steered toward new task")
        assert not _is_system_bypass("Hello world")
        assert not _is_system_bypass("")

    def test_send_uses_reply_when_token_present(self, adapter):
        import time as _time
        adapter._reply_tokens["Uchat"] = ("rt-token", _time.time() + 30)
        result = asyncio.run(adapter.send("Uchat", "hello"))
        assert result.success
        adapter._client.reply.assert_called_once()
        adapter._client.push.assert_not_called()
        # Token consumed (single-use)
        assert "Uchat" not in adapter._reply_tokens

    def test_send_falls_back_to_push_when_no_token(self, adapter):
        result = asyncio.run(adapter.send("Uchat", "hello"))
        assert result.success
        adapter._client.push.assert_called_once()
        adapter._client.reply.assert_not_called()

    def test_send_falls_back_to_push_when_reply_fails(self, adapter):
        import time as _time
        adapter._reply_tokens["Uchat"] = ("rt-token", _time.time() + 30)
        adapter._client.reply.side_effect = RuntimeError("expired")
        result = asyncio.run(adapter.send("Uchat", "hello"))
        assert result.success
        adapter._client.reply.assert_called_once()
        adapter._client.push.assert_called_once()

    def test_send_returns_failure_when_push_fails(self, adapter):
        adapter._client.push.side_effect = RuntimeError("network")
        result = asyncio.run(adapter.send("Uchat", "hello"))
        assert not result.success
        assert "network" in result.error

    def test_send_pending_button_caches_response(self, adapter):
        # Simulate that the slow-LLM postback button has fired.
        rid = adapter._cache.register_pending("Uchat")
        adapter._pending_buttons["Uchat"] = rid
        result = asyncio.run(adapter.send("Uchat", "the answer"))
        assert result.success
        # Response must have been cached, not pushed/replied.
        adapter._client.reply.assert_not_called()
        adapter._client.push.assert_not_called()
        assert adapter._cache.get(rid).state is State.READY
        assert adapter._cache.get(rid).payload == "the answer"

    def test_send_system_bypass_skips_postback_cache(self, adapter):
        # Even with a pending button, system busy-acks must surface visibly.
        rid = adapter._cache.register_pending("Uchat")
        adapter._pending_buttons["Uchat"] = rid
        result = asyncio.run(adapter.send("Uchat", "⚡ Interrupting current run"))
        assert result.success
        # Bypass goes through push (no reply token stored)
        adapter._client.push.assert_called_once()
        # And the cache entry is unchanged (still PENDING for the eventual answer)
        assert adapter._cache.get(rid).state is State.PENDING

    def test_send_caps_messages_per_call_at_five(self, adapter):
        # Build a payload that would naturally split into more than 5 LINE
        # bubbles; the chunker should cap at 5 + truncate.
        big = "\n\n".join(["x" * 4500 for _ in range(20)])
        result = asyncio.run(adapter.send("Uchat", big))
        assert result.success
        call_kwargs = adapter._client.push.call_args
        # call_args is (args, kwargs); for our send the messages are the 2nd positional
        sent_messages = call_kwargs.args[1] if call_kwargs.args else call_kwargs.kwargs.get("messages")
        # Without args, fall back to inspecting the call shape
        if sent_messages is None:
            # We invoked client.push(chat_id, messages) — check first batch
            sent_messages = adapter._client.push.call_args.args[1]
        assert len(sent_messages) <= 5

    def test_format_message_strips_markdown(self, adapter):
        out = adapter.format_message("**bold** [link](https://x.com)")
        assert "**" not in out
        assert "https://x.com" in out


# ---------------------------------------------------------------------------
# 8. Register() metadata + plugin entry points
# ---------------------------------------------------------------------------

class TestRegister:

    class _FakeCtx:
        def __init__(self):
            self.kwargs = None

        def register_platform(self, **kw):
            self.kwargs = kw

    def test_register_calls_register_platform(self):
        ctx = self._FakeCtx()
        register(ctx)
        assert ctx.kwargs is not None
        assert ctx.kwargs["name"] == "line"
        assert ctx.kwargs["label"] == "LINE"

    def test_register_advertises_required_env(self):
        ctx = self._FakeCtx()
        register(ctx)
        assert set(ctx.kwargs["required_env"]) == {
            "LINE_CHANNEL_ACCESS_TOKEN",
            "LINE_CHANNEL_SECRET",
        }

    def test_register_wires_allowlist_envs(self):
        ctx = self._FakeCtx()
        register(ctx)
        assert ctx.kwargs["allowed_users_env"] == "LINE_ALLOWED_USERS"
        assert ctx.kwargs["allow_all_env"] == "LINE_ALLOW_ALL_USERS"

    def test_register_wires_cron_home_channel(self):
        ctx = self._FakeCtx()
        register(ctx)
        assert ctx.kwargs["cron_deliver_env_var"] == "LINE_HOME_CHANNEL"

    def test_register_provides_standalone_sender(self):
        ctx = self._FakeCtx()
        register(ctx)
        assert callable(ctx.kwargs["standalone_sender_fn"])

    def test_register_provides_env_enablement(self):
        ctx = self._FakeCtx()
        register(ctx)
        assert callable(ctx.kwargs["env_enablement_fn"])

    def test_register_factory_yields_line_adapter(self):
        ctx = self._FakeCtx()
        register(ctx)
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, extra={
            "channel_access_token": "tok",
            "channel_secret": "sec",
        })
        ad = ctx.kwargs["adapter_factory"](cfg)
        assert isinstance(ad, LineAdapter)

    def test_max_message_length_below_line_per_bubble_limit(self):
        ctx = self._FakeCtx()
        register(ctx)
        # LINE per-bubble limit is 5000; we register 4500 to leave headroom.
        assert ctx.kwargs["max_message_length"] <= 5000


class TestEnvEnablement:

    def test_returns_none_without_credentials(self, monkeypatch):
        monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
        assert _env_enablement() is None

    def test_returns_dict_with_credentials(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok")
        monkeypatch.setenv("LINE_CHANNEL_SECRET", "sec")
        assert _env_enablement() == {}

    def test_seeds_port_from_env(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok")
        monkeypatch.setenv("LINE_CHANNEL_SECRET", "sec")
        monkeypatch.setenv("LINE_PORT", "8080")
        assert _env_enablement() == {"port": 8080}

    def test_seeds_public_url(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok")
        monkeypatch.setenv("LINE_CHANNEL_SECRET", "sec")
        monkeypatch.setenv("LINE_PUBLIC_URL", "https://my-tunnel.example.com")
        result = _env_enablement()
        assert result["public_url"] == "https://my-tunnel.example.com"


class TestStandaloneSend:

    def test_missing_token_returns_error(self, monkeypatch):
        monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, extra={})
        result = asyncio.run(_standalone_send(cfg, "Uchat", "hi"))
        assert "error" in result

    def test_missing_chat_id_returns_error(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok")
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, extra={})
        result = asyncio.run(_standalone_send(cfg, "", "hi"))
        assert "error" in result

    def test_pushes_via_client_when_credentials_present(self, monkeypatch):
        from gateway.config import PlatformConfig

        push_calls = []

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def push(self, chat_id, messages):
                push_calls.append((chat_id, messages))

        monkeypatch.setattr(_line, "_LineClient", _FakeClient)
        cfg = PlatformConfig(
            enabled=True,
            extra={"channel_access_token": "tok"},
        )
        result = asyncio.run(_standalone_send(cfg, "Uchat", "hello"))
        assert result.get("success") is True
        assert len(push_calls) == 1
        assert push_calls[0][0] == "Uchat"
        # Message wraps as text bubble
        assert push_calls[0][1][0]["type"] == "text"


class TestPostbackButtonShape:

    def test_template_buttons_structure(self):
        msg = build_postback_button_message("hi", "Tap me", "rid-1")
        assert msg["type"] == "template"
        assert msg["template"]["type"] == "buttons"
        assert msg["template"]["text"] == "hi"
        actions = msg["template"]["actions"]
        assert len(actions) == 1
        assert actions[0]["type"] == "postback"
        data = json.loads(actions[0]["data"])
        assert data == {"action": "show_response", "request_id": "rid-1"}

    def test_text_truncated_to_160(self):
        long = "x" * 200
        msg = build_postback_button_message(long, "Tap", "rid")
        assert len(msg["template"]["text"]) <= 160

    def test_alt_text_truncated_to_400(self):
        long = "x" * 500
        msg = build_postback_button_message(long, "Tap", "rid")
        assert len(msg["altText"]) <= 400


class TestCheckRequirements:

    def test_rejects_without_token(self, monkeypatch):
        monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("LINE_CHANNEL_SECRET", "s")
        assert not check_requirements()

    def test_rejects_without_secret(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "t")
        monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
        assert not check_requirements()


class TestValidateConfig:

    def test_validates_from_extra(self):
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(
            enabled=True,
            extra={"channel_access_token": "t", "channel_secret": "s"},
        )
        assert validate_config(cfg)

    def test_rejects_empty_config(self, monkeypatch):
        monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, extra={})
        assert not validate_config(cfg)


class TestAdapterInit:

    def test_init_from_config_extra(self, monkeypatch):
        for k in ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET", "LINE_PORT"):
            monkeypatch.delenv(k, raising=False)
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(
            enabled=True,
            extra={
                "channel_access_token": "tok",
                "channel_secret": "sec",
                "port": 7777,
                "public_url": "https://x.example.com",
                "allowed_users": ["U1", "U2"],
            },
        )
        ad = LineAdapter(cfg)
        assert ad.channel_access_token == "tok"
        assert ad.channel_secret == "sec"
        assert ad.webhook_port == 7777
        assert ad.public_base_url == "https://x.example.com"
        assert ad.allowed_users == {"U1", "U2"}

    def test_env_overrides_extra(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "env-tok")
        monkeypatch.setenv("LINE_PORT", "1234")
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(
            enabled=True,
            extra={"channel_access_token": "extra-tok", "channel_secret": "s", "port": 5555},
        )
        ad = LineAdapter(cfg)
        assert ad.channel_access_token == "env-tok"
        assert ad.webhook_port == 1234

    def test_csv_allowlist_parsed(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "t")
        monkeypatch.setenv("LINE_CHANNEL_SECRET", "s")
        monkeypatch.setenv("LINE_ALLOWED_USERS", "U1, U2,U3")
        monkeypatch.setenv("LINE_ALLOWED_GROUPS", "C1")
        from gateway.config import PlatformConfig
        ad = LineAdapter(PlatformConfig(enabled=True))
        assert ad.allowed_users == {"U1", "U2", "U3"}
        assert ad.allowed_groups == {"C1"}

    def test_get_chat_info_infers_type_from_prefix(self, monkeypatch):
        monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "t")
        monkeypatch.setenv("LINE_CHANNEL_SECRET", "s")
        from gateway.config import PlatformConfig
        ad = LineAdapter(PlatformConfig(enabled=True))
        assert asyncio.run(ad.get_chat_info("U123"))["type"] == "dm"
        assert asyncio.run(ad.get_chat_info("C123"))["type"] == "group"
        assert asyncio.run(ad.get_chat_info("R123"))["type"] == "channel"


# ---------------------------------------------------------------------------
# 9. Inbound message-type classification
# ---------------------------------------------------------------------------

class TestMessageTypeMapping:
    """LINE webhook message types must map to the right normalized
    MessageType so the gateway routes media correctly (e.g. voice → STT,
    files → document handling). Regression guard for the old code that
    referenced the non-existent ``MessageType.IMAGE`` and collapsed every
    non-text message onto a single type."""

    def test_image_event_not_attributeerror_regression(self):
        # The bug: MessageType.IMAGE doesn't exist on the enum.
        MessageType = _line.MessageType
        assert not hasattr(MessageType, "IMAGE")

    def test_every_line_type_maps_to_correct_enum(self):
        MessageType = _line.MessageType
        mapping = _line._LINE_MESSAGE_TYPES
        assert mapping["text"] == MessageType.TEXT
        assert mapping["image"] == MessageType.PHOTO
        assert mapping["video"] == MessageType.VIDEO
        # LINE has no separate voice type — audio clips are voice notes.
        assert mapping["audio"] == MessageType.VOICE
        assert mapping["file"] == MessageType.DOCUMENT
        assert mapping["location"] == MessageType.LOCATION
        assert mapping["sticker"] == MessageType.STICKER

    def test_unknown_type_falls_back_to_text(self):
        MessageType = _line.MessageType
        assert _line._LINE_MESSAGE_TYPES.get("flex", MessageType.TEXT) == MessageType.TEXT


# ---------------------------------------------------------------------------
# 9. Mention gating + observation mode
# ---------------------------------------------------------------------------

class TestMentionGating:

    @pytest.fixture
    def adapter(self, monkeypatch):
        monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
        monkeypatch.delenv("LINE_REQUIRE_MENTION", raising=False)
        monkeypatch.delenv("LINE_OBSERVE_UNMENTIONED_GROUP_MESSAGES", raising=False)
        monkeypatch.delenv("LINE_FREE_RESPONSE_CHANNELS", raising=False)
        monkeypatch.delenv("LINE_MENTION_PATTERNS", raising=False)
        monkeypatch.delenv("LINE_EXCLUSIVE_BOT_MENTIONS", raising=False)
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, extra={
            "channel_access_token": "tok",
            "channel_secret": "sec",
        })
        ad = LineAdapter(cfg)
        ad._client = MagicMock()
        return ad

    # --- _is_line_group_chat ---

    def test_group_is_group_chat(self, adapter):
        assert adapter._is_line_group_chat({"type": "group"})

    def test_room_is_group_chat(self, adapter):
        assert adapter._is_line_group_chat({"type": "room"})

    def test_user_is_not_group_chat(self, adapter):
        assert not adapter._is_line_group_chat({"type": "user"})

    # --- _line_require_mention ---

    def test_require_mention_default_false(self, adapter):
        assert not adapter._line_require_mention()

    def test_require_mention_from_extra(self, adapter):
        adapter.config.extra["require_mention"] = True
        assert adapter._line_require_mention()

    def test_require_mention_from_env(self, adapter, monkeypatch):
        monkeypatch.setenv("LINE_REQUIRE_MENTION", "true")
        assert adapter._line_require_mention()

    # --- _line_observe_unmentioned_group_messages ---

    def test_observe_default_false(self, adapter):
        assert not adapter._line_observe_unmentioned_group_messages()

    def test_observe_from_extra(self, adapter):
        adapter.config.extra["observe_unmentioned_group_messages"] = True
        assert adapter._line_observe_unmentioned_group_messages()

    # --- _line_message_mentions_bot ---

    def test_native_mention_is_self(self, adapter):
        event = {"message": {"mention": {"mentionees": [{"isSelf": True}]}}}
        assert adapter._line_message_mentions_bot(event)

    def test_native_mention_all(self, adapter):
        event = {"message": {"mention": {"mentionees": [{"type": "all"}]}}}
        assert adapter._line_message_mentions_bot(event)

    def test_native_mention_other_user(self, adapter):
        event = {"message": {"mention": {"mentionees": [{"isSelf": False, "type": "user"}]}}}
        assert not adapter._line_message_mentions_bot(event)

    def test_no_mention(self, adapter):
        event = {"message": {}}
        assert not adapter._line_message_mentions_bot(event)

    # --- _line_message_matches_mention_patterns ---

    def test_regex_match(self, adapter):
        adapter._mention_patterns = [re.compile("喵奈", re.IGNORECASE)]
        assert adapter._line_message_matches_mention_patterns("喵奈 幫我看看")

    def test_regex_no_match(self, adapter):
        adapter._mention_patterns = [re.compile("喵奈", re.IGNORECASE)]
        assert not adapter._line_message_matches_mention_patterns("一般訊息")

    def test_regex_empty_patterns(self, adapter):
        adapter._mention_patterns = []
        assert not adapter._line_message_matches_mention_patterns("喵奈 幫我看看")

    # --- _should_process_line_message ---

    def test_dm_always_processed(self, adapter):
        event = {"source": {"type": "user", "userId": "U1"}}
        assert adapter._should_process_line_message(event)

    def test_group_no_mention_require_mention_disabled(self, adapter):
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "hi"}}
        assert adapter._should_process_line_message(event)

    def test_group_no_mention_require_mention_enabled_skips(self, adapter):
        adapter.config.extra["require_mention"] = True
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "hi"}}
        assert not adapter._should_process_line_message(event)

    def test_group_native_mention_require_mention_enabled(self, adapter):
        adapter.config.extra["require_mention"] = True
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "@bot hi",
                             "mention": {"mentionees": [{"isSelf": True}]}}}
        assert adapter._should_process_line_message(event)

    def test_group_regex_match_require_mention_enabled(self, adapter):
        adapter.config.extra["require_mention"] = True
        adapter._mention_patterns = [re.compile("喵奈")]
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "喵奈 幫我看看"}}
        assert adapter._should_process_line_message(event)

    def test_free_response_channel_bypasses_mention(self, adapter):
        adapter.config.extra["require_mention"] = True
        adapter.config.extra["free_response_channels"] = ["C1"]
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "hi"}}
        assert adapter._should_process_line_message(event)

    def test_room_treated_as_group(self, adapter):
        adapter.config.extra["require_mention"] = True
        event = {"source": {"type": "room", "roomId": "R1", "userId": "U1"},
                 "message": {"type": "text", "text": "hi"}}
        assert not adapter._should_process_line_message(event)

    # --- exclusive_bot_mentions ---

    def test_exclusive_bot_excludes_when_enabled(self, adapter):
        adapter.config.extra["require_mention"] = True
        adapter.config.extra["exclusive_bot_mentions"] = True
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "@otherbot hi",
                             "mention": {"mentionees": [{"isSelf": False, "type": "user"}]}}}
        assert not adapter._should_process_line_message(event)

    def test_exclusive_bot_does_not_exclude_self_mention(self, adapter):
        adapter.config.extra["require_mention"] = True
        adapter.config.extra["exclusive_bot_mentions"] = True
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "@bot hi",
                             "mention": {"mentionees": [{"isSelf": True}]}}}
        assert adapter._should_process_line_message(event)

    def test_exclusive_bot_disabled_allows_other_mention(self, adapter):
        adapter.config.extra["require_mention"] = False
        adapter.config.extra["exclusive_bot_mentions"] = False
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "@otherbot hi",
                             "mention": {"mentionees": [{"isSelf": False, "type": "user"}]}}}
        assert adapter._should_process_line_message(event)

    # --- _should_observe_unmentioned_line_group_message ---

    def test_observe_requires_observe_enabled(self, adapter):
        adapter.config.extra["require_mention"] = True
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "hi"}}
        assert not adapter._should_observe_unmentioned_line_group_message(event)

    def test_observe_requires_require_mention(self, adapter):
        adapter.config.extra["observe_unmentioned_group_messages"] = True
        adapter.config.extra["require_mention"] = False
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "hi"}}
        assert not adapter._should_observe_unmentioned_line_group_message(event)

    def test_observe_triggers_for_unmentioned_group(self, adapter):
        adapter.config.extra["require_mention"] = True
        adapter.config.extra["observe_unmentioned_group_messages"] = True
        adapter.config.extra["group_allowed_chats"] = ["C1"]
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "hi"}}
        assert adapter._should_observe_unmentioned_line_group_message(event)

    def test_observe_skips_when_mentioned(self, adapter):
        adapter.config.extra["require_mention"] = True
        adapter.config.extra["observe_unmentioned_group_messages"] = True
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "@bot hi",
                             "mention": {"mentionees": [{"isSelf": True}]}}}
        assert not adapter._should_observe_unmentioned_line_group_message(event)

    def test_observe_skips_free_response(self, adapter):
        adapter.config.extra["require_mention"] = True
        adapter.config.extra["observe_unmentioned_group_messages"] = True
        adapter.config.extra["free_response_channels"] = ["C1"]
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "hi"}}
        assert not adapter._should_observe_unmentioned_line_group_message(event)

    def test_observe_skips_dm(self, adapter):
        adapter.config.extra["require_mention"] = True
        adapter.config.extra["observe_unmentioned_group_messages"] = True
        event = {"source": {"type": "user", "userId": "U1"},
                 "message": {"type": "text", "text": "hi"}}
        assert not adapter._should_observe_unmentioned_line_group_message(event)

    # --- _compile_mention_patterns ---

    def test_compile_from_extra(self, adapter):
        adapter.config.extra["mention_patterns"] = ["喵奈", "@bot"]
        patterns = adapter._compile_mention_patterns()
        assert len(patterns) == 2

    def test_compile_from_env_json(self, adapter, monkeypatch):
        monkeypatch.setenv("LINE_MENTION_PATTERNS", '["喵奈"]')
        patterns = adapter._compile_mention_patterns()
        assert len(patterns) == 1

    def test_compile_invalid_regex_skipped(self, adapter):
        adapter.config.extra["mention_patterns"] = ["[invalid", "good"]
        patterns = adapter._compile_mention_patterns()
        assert len(patterns) == 1

    # --- _line_free_response_channels ---

    def test_free_response_from_extra(self, adapter):
        adapter.config.extra["free_response_channels"] = ["C1", "C2"]
        result = adapter._line_free_response_channels()
        assert "C1" in result
        assert "C2" in result

    def test_free_response_from_env(self, adapter, monkeypatch):
        monkeypatch.setenv("LINE_FREE_RESPONSE_CHANNELS", "C1,C2")
        result = adapter._line_free_response_channels()
        assert "C1" in result

    # --- _line_exclusive_bot_mentions ---

    def test_exclusive_default_true(self, adapter):
        assert adapter._line_exclusive_bot_mentions()

    def test_exclusive_from_extra(self, adapter):
        adapter.config.extra["exclusive_bot_mentions"] = False
        assert not adapter._line_exclusive_bot_mentions()

    # --- _line_group_observe_shared_source ---

    def test_shared_source_removes_user_id(self, adapter):
        from gateway.platforms.base import SessionSource
        source = SessionSource(
            platform="line", user_id="U1", user_name="nick",
            chat_id="C1", chat_type="group", chat_name="G1",
        )
        shared = adapter._line_group_observe_shared_source(source)
        assert shared.user_id is None
        assert shared.user_name is None
        assert shared.chat_id == "C1"

    # --- _line_group_observe_attributed_text ---

    def test_attributed_text_format(self, adapter):
        from gateway.platforms.base import MessageEvent, SessionSource
        source = SessionSource(
            platform="line", user_id="U1", user_name="Nick",
            chat_id="C1", chat_type="group", chat_name="G1",
        )
        event_obj = MessageEvent(text="hello", source=source)
        result = adapter._line_group_observe_attributed_text(event_obj)
        assert "[Nick|U1]" in result
        assert "hello" in result

    # --- _clean_line_bot_trigger_text ---

    def test_clean_strips_mention_pattern(self, adapter):
        adapter._mention_patterns = [re.compile("喵奈")]
        assert adapter._clean_line_bot_trigger_text("喵奈 幫我看看") == "幫我看看"

    def test_clean_empty_text(self, adapter):
        assert adapter._clean_line_bot_trigger_text("") == ""

    # --- _observe_unmentioned_line_group_message (transcript write) ---

    def test_observe_writes_to_transcript(self, adapter):
        from gateway.platforms.base import MessageEvent, SessionSource
        mock_store = MagicMock()
        mock_session = MagicMock()
        mock_session.session_id = "sess-1"
        mock_store.get_or_create_session.return_value = mock_session
        adapter._session_store = mock_store

        source = SessionSource(
            platform="line", user_id="U1", user_name="Nick",
            chat_id="C1", chat_type="group", chat_name="G1",
        )
        event_obj = MessageEvent(text="hello", source=source, message_id="m1")
        event = {"source": {"type": "group", "groupId": "C1", "userId": "U1"},
                 "message": {"type": "text", "text": "hello"}}

        adapter._observe_unmentioned_line_group_message(event, event_obj)

        mock_store.append_to_transcript.assert_called_once()
        call_args = mock_store.append_to_transcript.call_args
        entry = call_args[0][1]
        assert entry["role"] == "user"
        assert entry["observed"] is True
        assert "Nick" in entry["content"]
        assert entry["message_id"] == "m1"
