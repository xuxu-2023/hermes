import asyncio
import logging
import os
from typing import Any, Dict, Optional
import aiohttp

from gateway.config import PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
import hermes_cli.gateway as gateway_mod

logger = logging.getLogger(__name__)

def check_ghasedak_requirements() -> bool:
    try:
        import aiohttp
    except ImportError:
        return False
    return bool(os.getenv("GHASEDAK_API_KEY"))

def _is_connected(config) -> bool:
    return bool((gateway_mod.get_env_value("GHASEDAK_API_KEY") or "").strip())

class GhasedakAdapter(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = 1000

    def __init__(self, config: PlatformConfig):
        super().__init__(config, "ghasedak")
        self.env_vars = {k: os.getenv(k, "") for k in ['GHASEDAK_API_KEY', 'GHASEDAK_SENDER']}
        self._http_session: Optional[aiohttp.ClientSession] = None

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self.env_vars["GHASEDAK_API_KEY"]:
            return False
        self._http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self._running = True
        return True

    async def disconnect(self) -> None:
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        self._running = False

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        if not self._http_session:
            return SendResult(success=False, error="Not connected")
        try:

        payload = {"receptor": chat_id, "message": content}
        if self.env_vars.get("GHASEDAK_SENDER"): payload["linenumber"] = self.env_vars["GHASEDAK_SENDER"]
        url = "https://api.ghasedak.me/v2/sms/send/simple"
        headers = {"apikey": self.env_vars['GHASEDAK_API_KEY']}
        async with self._http_session.post(url, data=payload, headers=headers) as resp:
            data = await resp.json()
            if data.get("result", {}).get("code") != 200: return SendResult(success=False, error=str(data))
            items = data.get("items", [])
            return SendResult(success=True, message_id=str(items[0]) if items else "")

        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "dm"}

def _build_adapter(config):
    return GhasedakAdapter(config)

def register(ctx) -> None:
    ctx.register_platform(
        name="ghasedak",
        label="Ghasedak (قاصدک)",
        adapter_factory=_build_adapter,
        check_fn=check_ghasedak_requirements,
        is_connected=_is_connected,
        required_env=["GHASEDAK_API_KEY"],
        install_hint="pip install aiohttp",
        allowed_users_env="GHASEDAK_ALLOWED_USERS",
        allow_all_env="GHASEDAK_ALLOW_ALL_USERS",
        max_message_length=1000,
        emoji="💬",
        allow_update_command=True,
    )
