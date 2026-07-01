"""SMS.ir platform adapter.

Connects to the SMS.ir REST API for outbound SMS.

Gateway-specific env vars:
  - SMS_IR_API_KEY
  - SMS_IR_LINE_NUMBER (e.g. 3000...)
"""

import asyncio
import logging
import os
from typing import Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.platforms.helpers import redact_phone, strip_markdown

logger = logging.getLogger(__name__)

MAX_SMS_LENGTH = 1600

def check_sms_ir_requirements() -> bool:
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("SMS_IR_API_KEY") and os.getenv("SMS_IR_LINE_NUMBER"))


class SmsIrAdapter(BasePlatformAdapter):
    """
    SMS.ir <-> Hermes gateway adapter.
    """

    MAX_MESSAGE_LENGTH = MAX_SMS_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, "sms_ir")
        self._api_key: str = os.environ.get("SMS_IR_API_KEY", "")
        self._line_number: str = os.environ.get("SMS_IR_LINE_NUMBER", "")
        self._runner = None
        self._http_session = None

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self._api_key or not self._line_number:
            logger.error("[sms_ir] Missing SMS_IR_API_KEY or SMS_IR_LINE_NUMBER")
            return False
        return True

    async def disconnect(self) -> None:
        pass

    async def start(self) -> None:
        logger.info(f"[sms_ir] Started SMS.ir adapter with line {self._line_number}")

    async def stop(self) -> None:
        logger.info("[sms_ir] Stopped SMS.ir adapter")

    async def send(self, message: SendResult) -> None:
        result = await _standalone_send(
            self.config,
            message.chat_id,
            message.text
        )
        if "error" in result:
            logger.error(f"[sms_ir] Send failed: {result['error']}")
        else:
            logger.info(f"[sms_ir] Sent SMS to {message.chat_id}")

def _strip_markdown_for_sms(message: str) -> str:
    import re
    message = re.sub(r"\*\*(.+?)\*\*", r"\1", message, flags=re.DOTALL)
    message = re.sub(r"\*(.+?)\*", r"\1", message, flags=re.DOTALL)
    message = re.sub(r"__(.+?)__", r"\1", message, flags=re.DOTALL)
    message = re.sub(r"_(.+?)_", r"\1", message, flags=re.DOTALL)
    message = re.sub(r"```[a-z]*\n?", "", message)
    message = re.sub(r"`(.+?)`", r"\1", message)
    message = re.sub(r"^#{1,6}\s+", "", message, flags=re.MULTILINE)
    message = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", message)
    message = re.sub(r"\n{3,}", "\n\n", message)
    return message.strip()


async def _standalone_send(
    pconfig,
    chat_id,
    message,
    *,
    thread_id=None,
    media_files=None,
    force_document=False,
):
    try:
        import aiohttp
    except ImportError:
        return {"error": "aiohttp not installed. Run: pip install aiohttp"}

    api_key = os.getenv("SMS_IR_API_KEY", "")
    line_number = os.getenv("SMS_IR_LINE_NUMBER", "")
    if not api_key or not line_number:
        return {"error": "SMS.ir not configured (SMS_IR_API_KEY, SMS_IR_LINE_NUMBER required)"}

    message = _strip_markdown_for_sms(message)

    try:
        url = "https://api.sms.ir/v1/send/bulk"
        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "lineNumber": line_number,
            "MessageText": message,
            "Mobiles": [chat_id]
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    error_msg = body.get("message", str(body))
                    return {"error": f"SMS.ir API error ({resp.status}): {error_msg}"}
                return {"success": True, "platform": "sms_ir", "chat_id": chat_id}
    except Exception as e:
        return {"error": f"SMS send failed: {e}"}


def _is_connected(config) -> bool:
    import hermes_cli.gateway as gateway_mod
    return bool((gateway_mod.get_env_value("SMS_IR_API_KEY") or "").strip())


def _build_adapter(config):
    return SmsIrAdapter(config)


def register(ctx) -> None:
    ctx.register_platform(
        name="sms_ir",
        label="SMS (SMS.ir)",
        adapter_factory=_build_adapter,
        check_fn=check_sms_ir_requirements,
        is_connected=_is_connected,
        required_env=["SMS_IR_API_KEY", "SMS_IR_LINE_NUMBER"],
        install_hint="pip install aiohttp",
        allowed_users_env="SMS_IR_ALLOWED_USERS",
        allow_all_env="SMS_IR_ALLOW_ALL_USERS",
        cron_deliver_env_var="SMS_IR_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        max_message_length=MAX_SMS_LENGTH,
        pii_safe=True,
        emoji="💬",
        allow_update_command=True,
    )
