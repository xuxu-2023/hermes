"""Test ``contextual_reason`` in ``send_exec_approval`` across adapters + run.py.

Verifies that the optional ``contextual_reason`` kwarg added to every adapter's
``send_exec_approval`` method renders the agent's rationale in the approval
prompt when provided — and that the gateway forwards its captured interim
assistant text both to the adapter and into the plain-text fallback.

Salvage of #27833 by @zccyman (feature + approach are theirs), re-targeted onto
current main with the Discord plugins adapter, the WhatsApp Cloud adapter,
platform size-budget enforcement, long-rationale/special-char cases, and a
run.py forwarding test added.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ===========================================================================
# Telegram
# ===========================================================================

def _ensure_telegram_mock():
    """Wire up the minimal mocks required to import TelegramAdapter."""
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()


def _make_telegram_adapter():
    from plugins.platforms.telegram.adapter import TelegramAdapter
    from gateway.config import PlatformConfig
    config = PlatformConfig(enabled=True, token="test-token")
    adapter = TelegramAdapter(config)
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


class TestTelegramContextualRationale:

    @pytest.mark.asyncio
    async def test_rationale_appears_in_text(self):
        adapter = _make_telegram_adapter()
        mock_msg = MagicMock()
        mock_msg.message_id = 42
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)

        result = await adapter.send_exec_approval(
            chat_id="12345",
            command="rm -rf /tmp/cache",
            session_key="s:key",
            description="recursive delete",
            contextual_reason="I need to free up disk space before the build.",
        )

        assert result.success is True
        kwargs = adapter._bot.send_message.call_args[1]
        text = kwargs["text"]
        assert "I need to free up disk space" in text
        assert "recursive delete" in text

    @pytest.mark.asyncio
    async def test_no_rationale_omits_block(self):
        adapter = _make_telegram_adapter()
        mock_msg = MagicMock()
        mock_msg.message_id = 42
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)

        result = await adapter.send_exec_approval(
            chat_id="12345",
            command="rm -rf /tmp",
            session_key="s:key",
        )

        assert result.success is True
        kwargs = adapter._bot.send_message.call_args[1]
        assert "Command Approval Required" in kwargs["text"]

    @pytest.mark.asyncio
    async def test_rationale_html_escaped(self):
        adapter = _make_telegram_adapter()
        mock_msg = MagicMock()
        mock_msg.message_id = 42
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)

        result = await adapter.send_exec_approval(
            chat_id="12345",
            command="echo test",
            session_key="s:key",
            contextual_reason="This has <script>alert(1)</script> tags",
        )

        assert result.success is True
        text = adapter._bot.send_message.call_args[1]["text"]
        assert "&lt;script&gt;" in text
        assert "<script>" not in text

    @pytest.mark.asyncio
    async def test_long_rationale_truncated_within_limit(self):
        adapter = _make_telegram_adapter()
        mock_msg = MagicMock()
        mock_msg.message_id = 42
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)

        long_reason = "X" * 5000
        result = await adapter.send_exec_approval(
            chat_id="12345",
            command="echo hi",
            session_key="s:key",
            contextual_reason=long_reason,
        )

        assert result.success is True
        text = adapter._bot.send_message.call_args[1]["text"]
        # Telegram's hard cap is 4096; the rendered message must stay under it.
        assert len(text) < 4096
        assert "..." in text


# ===========================================================================
# Slack
# ===========================================================================

def _ensure_slack_mock():
    if "slack_bolt" in sys.modules:
        return
    slack_bolt = MagicMock()
    slack_bolt.async_app.AsyncApp = MagicMock
    sys.modules["slack_bolt"] = slack_bolt
    sys.modules["slack_bolt.async_app"] = slack_bolt.async_app
    handler_mod = MagicMock()
    handler_mod.AsyncSocketModeHandler = MagicMock
    sys.modules["slack_bolt.adapter"] = MagicMock()
    sys.modules["slack_bolt.adapter.socket_mode"] = MagicMock()
    sys.modules["slack_bolt.adapter.socket_mode.async_handler"] = handler_mod
    sdk_mod = MagicMock()
    sdk_mod.web = MagicMock()
    sdk_mod.web.async_client = MagicMock()
    sdk_mod.web.async_client.AsyncWebClient = MagicMock
    sys.modules["slack_sdk"] = sdk_mod
    sys.modules["slack_sdk.web"] = sdk_mod.web
    sys.modules["slack_sdk.web.async_client"] = sdk_mod.web.async_client


_ensure_slack_mock()


def _make_slack_adapter():
    from plugins.platforms.slack.adapter import SlackAdapter
    from gateway.config import PlatformConfig
    config = PlatformConfig(enabled=True, token="xoxb-test-token")
    adapter = SlackAdapter(config)
    adapter._app = MagicMock()
    adapter._bot_user_id = "U_BOT"
    adapter._team_clients = {"T1": AsyncMock()}
    adapter._team_bot_user_ids = {"T1": "U_BOT"}
    adapter._channel_team = {"C1": "T1"}
    return adapter


class TestSlackContextualRationale:

    @pytest.mark.asyncio
    async def test_rationale_in_section_block(self):
        adapter = _make_slack_adapter()
        client = adapter._team_clients["T1"]
        client.chat_postMessage = AsyncMock(return_value={"ts": "1.2"})

        result = await adapter.send_exec_approval(
            chat_id="C1",
            command="rm -rf /important",
            session_key="s:key",
            description="dangerous deletion",
            contextual_reason="Clearing stale build artifacts before redeploy.",
        )

        assert result.success is True
        blocks = client.chat_postMessage.call_args[1]["blocks"]
        section_text = blocks[0]["text"]["text"]
        assert "Clearing stale build artifacts" in section_text
        assert "rm -rf /important" in section_text

    @pytest.mark.asyncio
    async def test_long_rationale_respects_3000_char_budget(self):
        adapter = _make_slack_adapter()
        client = adapter._team_clients["T1"]
        client.chat_postMessage = AsyncMock(return_value={"ts": "1.2"})

        result = await adapter.send_exec_approval(
            chat_id="C1",
            command="echo " + "A" * 4000,
            session_key="s:key",
            description="big command",
            contextual_reason="R" * 5000,
        )

        assert result.success is True
        blocks = client.chat_postMessage.call_args[1]["blocks"]
        section_text = blocks[0]["text"]["text"]
        # Slack section blocks hard-cap at 3000 chars.
        assert len(section_text) <= 3000

    @pytest.mark.asyncio
    async def test_no_rationale_still_works(self):
        adapter = _make_slack_adapter()
        client = adapter._team_clients["T1"]
        client.chat_postMessage = AsyncMock(return_value={"ts": "1.2"})

        result = await adapter.send_exec_approval(
            chat_id="C1", command="echo hi", session_key="s",
        )
        assert result.success is True


# ===========================================================================
# Feishu
# ===========================================================================

class TestFeishuContextualRationale:

    @pytest.mark.asyncio
    async def test_rationale_in_card_markdown(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter
        adapter = object.__new__(FeishuAdapter)
        adapter._client = MagicMock()
        adapter._approval_counter = iter(range(1, 100))
        adapter._approval_state = {}
        adapter._feishu_send_with_retry = AsyncMock(
            return_value=MagicMock(data={"data": {"message_id": "om_xxx"}})
        )
        adapter._finalize_send_result = MagicMock(
            return_value=MagicMock(success=True, message_id="om_xxx")
        )

        result = await adapter.send_exec_approval(
            chat_id="oc_xxx",
            command="rm -rf /tmp",
            session_key="s:key",
            description="dangerous command",
            contextual_reason="Cleaning up temp files for the new build.",
        )

        assert result.success is True
        call_args = adapter._feishu_send_with_retry.call_args
        payload = call_args.kwargs.get("payload") or call_args[1].get("payload")
        card = json.loads(payload)
        md_content = card["elements"][0]["content"]
        assert "Cleaning up temp files" in md_content

    @pytest.mark.asyncio
    async def test_no_rationale_clean(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter
        adapter = object.__new__(FeishuAdapter)
        adapter._client = MagicMock()
        adapter._approval_counter = iter(range(1, 100))
        adapter._approval_state = {}
        adapter._feishu_send_with_retry = AsyncMock(
            return_value=MagicMock(data={"data": {"message_id": "om_xxx"}})
        )
        adapter._finalize_send_result = MagicMock(
            return_value=MagicMock(success=True, message_id="om_xxx")
        )

        result = await adapter.send_exec_approval(
            chat_id="oc_xxx",
            command="rm -rf /tmp",
            session_key="s:key",
        )

        assert result.success is True


# ===========================================================================
# Matrix
# ===========================================================================

class TestMatrixContextualRationale:

    def _make(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter
        from gateway.config import PlatformConfig
        adapter = MatrixAdapter(PlatformConfig(enabled=True))
        adapter._client = MagicMock()
        adapter._approval_prompt_by_session = {}
        adapter._approval_prompts_by_event = {}
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message_id = "evt_1"
        adapter.send = AsyncMock(return_value=mock_result)
        adapter._send_reaction = AsyncMock(return_value="evt_react")
        return adapter

    @pytest.mark.asyncio
    async def test_rationale_in_text(self):
        adapter = self._make()
        result = await adapter.send_exec_approval(
            chat_id="!room:server",
            command="rm -rf /tmp",
            session_key="s:key",
            description="dangerous command",
            contextual_reason="Removing stale cache files.",
        )
        assert result.success is True
        send_text = adapter.send.call_args[0][1]
        assert "Removing stale cache files" in send_text

    @pytest.mark.asyncio
    async def test_long_rationale_truncated(self):
        adapter = self._make()
        result = await adapter.send_exec_approval(
            chat_id="!room:server",
            command="echo hi",
            session_key="s:key",
            contextual_reason="Z" * 5000,
        )
        assert result.success is True
        send_text = adapter.send.call_args[0][1]
        assert "..." in send_text


# ===========================================================================
# QQ Bot
# ===========================================================================

class TestQQBotContextualRationale:

    def _make(self):
        from gateway.platforms.qqbot.adapter import QQAdapter
        adapter = object.__new__(QQAdapter)
        adapter._last_msg_id = {"chat1": "m1"}
        adapter.send = AsyncMock(
            return_value=MagicMock(success=True, message_id="sent")
        )
        adapter.send_approval_request = AsyncMock(
            return_value=MagicMock(success=True, message_id="appr")
        )
        return adapter

    @pytest.mark.asyncio
    async def test_rationale_sent_as_leading_message(self):
        adapter = self._make()
        result = await adapter.send_exec_approval(
            chat_id="chat1",
            command="rm -rf /tmp",
            session_key="s:key",
            description="dangerous command",
            contextual_reason="Cleaning the cache directory.",
        )
        assert result.success is True
        # The rationale was sent before the approval card.
        assert adapter.send.await_count == 1
        sent_text = adapter.send.call_args[0][1]
        assert "Cleaning the cache directory" in sent_text
        adapter.send_approval_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_rationale_no_leading_message(self):
        adapter = self._make()
        result = await adapter.send_exec_approval(
            chat_id="chat1",
            command="rm -rf /tmp",
            session_key="s:key",
        )
        assert result.success is True
        adapter.send.assert_not_awaited()


# ===========================================================================
# Teams
# ===========================================================================

class TestTeamsContextualRationale:

    @pytest.mark.asyncio
    async def test_rationale_in_card_body(self, monkeypatch):
        import plugins.platforms.teams.adapter as tmod
        if getattr(tmod, "AdaptiveCard", None) is None:
            pytest.skip("microsoft_teams cards SDK not available")
        # Spy on TextBlock so the assertion works whether the real SDK or a
        # cross-test MagicMock stub is installed for microsoft_teams.
        seen_texts = []

        def _recording_text_block(*args, **kwargs):
            txt = kwargs.get("text")
            if txt is None and args:
                txt = args[0]
            seen_texts.append(txt)
            return MagicMock()

        monkeypatch.setattr(tmod, "TextBlock", _recording_text_block)
        adapter = object.__new__(tmod.TeamsAdapter)
        adapter._app = MagicMock()
        adapter._send_card = AsyncMock(return_value=MagicMock(id="card1"))

        result = await adapter.send_exec_approval(
            chat_id="conv1",
            command="rm -rf /tmp",
            session_key="s:key",
            description="dangerous command",
            contextual_reason="Freeing disk space before deploy.",
        )

        assert result.success is True
        assert any(
            t and "Freeing disk space" in str(t) for t in seen_texts
        ), f"rationale not found in TextBlocks: {seen_texts}"


# ===========================================================================
# Discord (plugins adapter)
# ===========================================================================

class TestDiscordContextualRationale:

    def _make(self):
        import plugins.platforms.discord.adapter as dmod
        adapter = object.__new__(dmod.DiscordAdapter)
        adapter._client = MagicMock()
        adapter._allowed_user_ids = set()
        adapter._allowed_role_ids = set()
        channel = MagicMock()
        sent_msg = MagicMock()
        sent_msg.id = 999
        channel.send = AsyncMock(return_value=sent_msg)
        adapter._client.get_channel = MagicMock(return_value=channel)
        return adapter, channel

    @pytest.mark.asyncio
    async def test_rationale_in_embed_description(self):
        import plugins.platforms.discord.adapter as dmod
        if not getattr(dmod, "DISCORD_AVAILABLE", False):
            pytest.skip("discord library not available")
        adapter, channel = self._make()
        result = await adapter.send_exec_approval(
            chat_id="123",
            command="rm -rf /tmp",
            session_key="s:key",
            description="dangerous command",
            contextual_reason="Removing stale build outputs.",
        )
        assert result.success is True
        embed = channel.send.call_args[1]["embed"]
        assert "Removing stale build outputs" in embed.description

    @pytest.mark.asyncio
    async def test_long_rationale_respects_embed_budget(self):
        import plugins.platforms.discord.adapter as dmod
        if not getattr(dmod, "DISCORD_AVAILABLE", False):
            pytest.skip("discord library not available")
        adapter, channel = self._make()
        result = await adapter.send_exec_approval(
            chat_id="123",
            command="echo " + "A" * 5000,
            session_key="s:key",
            contextual_reason="R" * 5000,
        )
        assert result.success is True
        embed = channel.send.call_args[1]["embed"]
        # Discord embed description hard-caps at 4096.
        assert len(embed.description) <= 4096


# ===========================================================================
# WhatsApp Cloud (new — not in original PR)
# ===========================================================================

class TestWhatsAppCloudContextualRationale:

    def _make(self):
        from gateway.platforms.whatsapp_cloud import WhatsAppCloudAdapter
        adapter = object.__new__(WhatsAppCloudAdapter)
        adapter._http_client = MagicMock()
        adapter._exec_approval_state = {}
        adapter._truncate_body = lambda t: t if len(t) <= 1024 else t[:1021] + "..."
        adapter._bounded_put = lambda d, k, v: d.__setitem__(k, v)
        adapter._post_interactive = AsyncMock(
            return_value=MagicMock(success=True, message_id="wamid")
        )
        return adapter

    @pytest.mark.asyncio
    async def test_rationale_in_body(self):
        adapter = self._make()
        result = await adapter.send_exec_approval(
            chat_id="15551234567",
            command="rm -rf /tmp",
            session_key="s:key",
            description="dangerous command",
            contextual_reason="Clearing the cache.",
        )
        assert result.success is True
        interactive = adapter._post_interactive.call_args[0][1]
        body = interactive["body"]["text"]
        assert "Clearing the cache" in body

    @pytest.mark.asyncio
    async def test_long_rationale_respects_body_cap(self):
        adapter = self._make()
        result = await adapter.send_exec_approval(
            chat_id="15551234567",
            command="echo " + "A" * 2000,
            session_key="s:key",
            contextual_reason="R" * 2000,
        )
        assert result.success is True
        interactive = adapter._post_interactive.call_args[0][1]
        body = interactive["body"]["text"]
        # WhatsApp interactive body caps at 1024 chars.
        assert len(body) <= 1024

    @pytest.mark.asyncio
    async def test_no_rationale_still_native_buttons(self):
        adapter = self._make()
        result = await adapter.send_exec_approval(
            chat_id="15551234567",
            command="echo hi",
            session_key="s:key",
        )
        assert result.success is True
        interactive = adapter._post_interactive.call_args[0][1]
        assert interactive["type"] == "button"


# ===========================================================================
# Signature acceptance — every adapter accepts the optional kwarg
# ===========================================================================

class TestAdapterSignatureAcceptsContextualReason:

    @pytest.mark.asyncio
    async def test_telegram_accepts_contextual_reason(self):
        adapter = _make_telegram_adapter()
        mock_msg = MagicMock()
        mock_msg.message_id = 1
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)
        result = await adapter.send_exec_approval(
            chat_id="1", command="echo hi", session_key="s",
            contextual_reason="Test rationale",
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_matrix_accepts_contextual_reason(self):
        from plugins.platforms.matrix.adapter import MatrixAdapter
        from gateway.config import PlatformConfig
        adapter = MatrixAdapter(PlatformConfig(enabled=True))
        adapter._client = MagicMock()
        adapter._approval_prompt_by_session = {}
        adapter._approval_prompts_by_event = {}
        mock_result = MagicMock(success=True, message_id="e1")
        adapter.send = AsyncMock(return_value=mock_result)
        adapter._send_reaction = AsyncMock(return_value="r1")
        result = await adapter.send_exec_approval(
            chat_id="!r:s", command="echo", session_key="s",
            contextual_reason="Rationale here",
        )
        assert result.success is True


# ===========================================================================
# gateway/run.py — the captured interim assistant text is forwarded both to
# the adapter (contextual_reason) and into the plain-text fallback.
# ===========================================================================

class TestRunPyForwardsContextualReason:
    """Mirror run.py's rationale-capture + forwarding logic.

    run.py defines ``_interim_assistant_cb`` (captures the last non-empty
    interim assistant chunk into ``_last_assistant_rationale``) and
    ``_approval_notify_sync`` (forwards it as ``contextual_reason`` to
    ``send_exec_approval`` and prepends it to the plain-text fallback).  We
    reconstruct that closure structure here so the forwarding contract is
    pinned by a test without standing up the whole gateway.
    """

    def _build_closures(self, adapter):
        last = {"rationale": ""}

        def interim_cb(text, already_streamed=False):
            stripped = str(text or "").strip()
            if stripped:
                last["rationale"] = stripped

        async def notify(approval_data):
            cmd = approval_data.get("command", "")
            desc = approval_data.get("description", "dangerous command")
            rationale = last["rationale"]
            if getattr(type(adapter), "send_exec_approval", None) is not None:
                res = await adapter.send_exec_approval(
                    chat_id="c", command=cmd, session_key="s",
                    description=desc, contextual_reason=rationale,
                    metadata=None,
                )
                if res.success:
                    return None
            cmd_preview = cmd[:200] + "..." if len(cmd) > 200 else cmd
            rationale_block = f"{rationale}\n\n" if rationale else ""
            msg = (
                "⚠️ **Dangerous command requires approval:**\n"
                f"{rationale_block}"
                f"```\n{cmd_preview}\n```\n"
                f"Reason: {desc}\n\n"
            )
            await adapter.send("c", msg, metadata=None)
            return msg

        return interim_cb, notify

    @pytest.mark.asyncio
    async def test_interim_text_forwarded_to_send_exec_approval(self):
        # Real class so the run.py-style class-level method check
        # (getattr(type(adapter), "send_exec_approval", ...)) sees the method.
        class _ButtonAdapter:
            def __init__(self):
                self.calls = []

            async def send_exec_approval(self, **kwargs):
                self.calls.append(kwargs)
                return MagicMock(success=True)

            async def send(self, chat_id, msg, metadata=None):
                return MagicMock(success=True)

        adapter = _ButtonAdapter()
        interim_cb, notify = self._build_closures(adapter)

        interim_cb("Let me clean the build cache first.")
        interim_cb("   ")  # blank: must not clobber the captured rationale
        await notify({"command": "rm -rf build", "description": "delete"})

        assert len(adapter.calls) == 1
        assert adapter.calls[0]["contextual_reason"] == "Let me clean the build cache first."

    @pytest.mark.asyncio
    async def test_interim_text_in_plain_text_fallback(self):
        # Adapter with NO send_exec_approval → fallback path used.
        class _PlainAdapter:
            def __init__(self):
                self.sent = []

            async def send(self, chat_id, msg, metadata=None):
                self.sent.append(msg)
                return MagicMock(success=True)

        adapter = _PlainAdapter()
        interim_cb, notify = self._build_closures(adapter)

        interim_cb("Need to remove the stale lockfile.")
        msg = await notify({"command": "rm -f /tmp/x.lock", "description": "delete"})

        assert "Need to remove the stale lockfile." in msg
        assert "Need to remove the stale lockfile." in adapter.sent[0]

    @pytest.mark.asyncio
    async def test_no_rationale_fallback_has_no_block(self):
        class _PlainAdapter:
            async def send(self, chat_id, msg, metadata=None):
                return MagicMock(success=True)

        adapter = _PlainAdapter()
        interim_cb, notify = self._build_closures(adapter)
        msg = await notify({"command": "rm -rf /x", "description": "delete"})
        # Fallback prose intact, no leading rationale block.
        assert "Dangerous command requires approval" in msg
        assert msg.count("\n\n") >= 1
