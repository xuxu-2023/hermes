"""Generic webhook platform adapter.

Runs an aiohttp HTTP server that receives webhook POSTs from external
services (GitHub, GitLab, JIRA, Stripe, etc.), validates HMAC signatures,
transforms payloads into agent prompts, and routes responses back to the
source or to another configured platform.

Configuration lives in config.yaml under platforms.webhook.extra.routes.
Each route defines:
  - events: which event types to accept (header-based filtering)
  - secret: HMAC secret for signature validation (REQUIRED)
  - prompt: template string formatted with the webhook payload
  - skills: optional list of skills to load for the agent
  - deliver: where to send the response (github_comment, telegram, etc.)
  - deliver_extra: additional delivery config (repo, pr_number, chat_id)
  - deliver_only: if true, skip the agent — the rendered prompt IS the
    message that gets delivered.  Use for external push notifications
    (Supabase, monitoring alerts, inter-agent pings) where zero LLM cost
    and sub-second delivery matter more than agent reasoning.

Security:
  - HMAC secret is required per route (validated at startup)
  - Rate limiting per route (fixed-window, configurable)
  - Idempotency cache prevents duplicate agent runs on webhook retries
  - Body size limits checked before reading payload
  - Set secret to "INSECURE_NO_AUTH" to skip validation (testing only)
"""

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Set

try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Sentinel returned by _resolve_request_profile when a /p/<profile>/ prefix
# names a profile this gateway does not serve (→ 404). Distinct from None
# (no prefix / multiplexing off → handle as the default profile).
_PROFILE_REJECTED = object()

_BUILTIN_DELIVER_PLATFORMS = {
    "telegram", "discord", "slack", "signal", "sms", "whatsapp",
    "matrix", "mattermost", "homeassistant", "email", "dingtalk",
    "feishu", "wecom", "wecom_callback", "weixin", "bluebubbles",
    "qqbot", "yuanbao",
}

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8644
_INSECURE_NO_AUTH = "INSECURE_NO_AUTH"
_DYNAMIC_ROUTES_FILENAME = "webhook_subscriptions.json"
_RATE_WINDOW_SECONDS = 60.0

# Hostnames/IP literals that only serve connections originating on the same
# machine. Anything else is treated as a public bind for safety-rail purposes.
_LOOPBACK_HOSTS = frozenset({
    "127.0.0.1",
    "localhost",
    "::1",
    "ip6-localhost",
    "ip6-loopback",
})


def _is_loopback_host(host: str) -> bool:
    """True when `host` binds only to the local machine.

    Covers IPv4 loopback, the standard `localhost` alias, IPv6 loopback in
    both bracketed and bare form, and the common Debian-style aliases. Any
    falsy value (empty string, None) is conservatively treated as non-loopback
    because an unset host usually means the platform-default public bind.
    """
    if not host:
        return False
    return host.strip().lower() in _LOOPBACK_HOSTS


def check_webhook_requirements() -> bool:
    """Check if webhook adapter dependencies are available."""
    return AIOHTTP_AVAILABLE


class WebhookAdapter(BasePlatformAdapter):
    """Generic webhook receiver that triggers agent runs from HTTP POSTs."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.WEBHOOK)
        self._host: str = config.extra.get("host", DEFAULT_HOST)
        self._port: int = int(config.extra.get("port", DEFAULT_PORT))
        self._global_secret: str = config.extra.get("secret", "")
        self._static_routes: Dict[str, dict] = config.extra.get("routes", {})
        self._dynamic_routes: Dict[str, dict] = {}
        self._dynamic_routes_mtime: float = 0.0
        self._routes: Dict[str, dict] = dict(self._static_routes)
        self._runner = None

        # Delivery info keyed by session chat_id.
        #
        # Read by every send() invocation for the chat_id (status messages
        # AND the final response).  Cleaned up via TTL on each POST so the
        # dict stays bounded — see _prune_delivery_info().  Do NOT pop on
        # send(), or interim status messages (e.g. fallback notifications,
        # context-pressure warnings) will consume the entry before the
        # final response arrives, causing the response to silently fall
        # back to the "log" deliver type.
        self._delivery_info: Dict[str, dict] = {}
        self._delivery_info_created: Dict[str, float] = {}
        self._delivery_info_order: Deque[tuple[float, str]] = deque()

        # Reference to gateway runner for cross-platform delivery (set externally)
        self.gateway_runner = None

        # Idempotency: TTL cache of recently processed delivery IDs.
        # Prevents duplicate agent runs when webhook providers retry.
        self._seen_deliveries: Dict[str, float] = {}
        self._idempotency_ttl: int = 3600  # 1 hour
        self._seen_deliveries_next_prune_at: float = 0.0

        # Rate limiting: per-route timestamps in a fixed window.
        self._rate_counts: Dict[str, Deque[float]] = {}
        self._rate_limit: int = int(config.extra.get("rate_limit", 30))  # per minute

        # Body size limit (auth-before-body pattern)
        self._max_body_bytes: int = int(
            config.extra.get("max_body_bytes", 1_048_576)
        )  # 1MB

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        # Load agent-created subscriptions before validating
        self._reload_dynamic_routes()

        # Validate routes at startup — each route needs either a regular
        # HMAC secret or a provider-specific auth mode that can safely
        # establish its secret through that provider's handshake (Notion).
        for name, route in self._routes.items():
            if self._is_notion_auth_route(route):
                continue

            secret = route.get("secret", self._global_secret)
            if not secret:
                raise ValueError(
                    f"[webhook] Route '{name}' has no HMAC secret. "
                    f"Set 'secret' on the route or globally. "
                    f"For testing without auth, set secret to '{_INSECURE_NO_AUTH}'."
                )

            # Safety rail: refuse to start if INSECURE_NO_AUTH is combined with a
            # non-loopback bind. The escape hatch is for local testing only;
            # serving an unauthenticated route on a public interface is a
            # deployment-grade footgun we'd rather crash early than ship.
            if secret == _INSECURE_NO_AUTH and not _is_loopback_host(self._host):
                raise ValueError(
                    f"[webhook] Route '{name}' uses INSECURE_NO_AUTH secret "
                    f"but is bound to non-loopback host '{self._host}'. "
                    f"INSECURE_NO_AUTH is for local testing only. "
                    f"Refusing to start to prevent accidental exposure."
                )
            # deliver_only routes bypass the agent — the POST body becomes a
            # direct push notification via the configured delivery target.
            # Validate up-front so misconfiguration surfaces at startup rather
            # than on the first webhook POST.
            if route.get("deliver_only"):
                deliver = route.get("deliver", "log")
                if not deliver or deliver == "log":
                    raise ValueError(
                        f"[webhook] Route '{name}' has deliver_only=true but "
                        f"deliver is '{deliver}'. Direct delivery requires a "
                        f"real target (telegram, discord, slack, github_comment, etc.)."
                    )

        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_post("/webhooks/{route_name}", self._handle_webhook)
        # Multi-profile multiplexing: a /p/<profile>/webhooks/<route> prefix
        # routes the inbound event to that profile. Same handler; the profile is
        # captured from the path and stamped onto the SessionSource so the agent
        # turn resolves that profile's config/skills/credentials. Only honored
        # when gateway.multiplex_profiles is on (the handler validates).
        app.router.add_post(
            "/p/{profile}/webhooks/{route_name}", self._handle_webhook
        )

        # Port conflict detection — fail fast if port is already in use
        import socket as _socket
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
                _s.settimeout(1)
                _s.connect(('127.0.0.1', self._port))
            logger.error('[webhook] Port %d already in use. Set a different port in config.yaml: platforms.webhook.port', self._port)
            return False
        except (ConnectionRefusedError, OSError):
            pass  # port is free

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._mark_connected()

        route_names = ", ".join(self._routes.keys()) or "(none configured)"
        logger.info(
            "[webhook] Listening on %s:%d — routes: %s",
            self._host,
            self._port,
            route_names,
        )
        return True

    async def disconnect(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._mark_disconnected()
        logger.info("[webhook] Disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Deliver the agent's response to the configured destination.

        chat_id is ``webhook:{route}:{delivery_id}``.  The delivery info
        stored during webhook receipt is read with ``.get()`` (not popped)
        so that interim status messages emitted before the final response
        — fallback-model notifications, context-pressure warnings, etc. —
        do not consume the entry and silently downgrade the final response
        to the ``log`` deliver type.  TTL cleanup happens on POST.
        """
        delivery = self._delivery_info.get(chat_id, {})
        deliver_type = delivery.get("deliver", "log")

        if deliver_type == "log":
            logger.info("[webhook] Response for %s: %s", chat_id, content[:200])
            return SendResult(success=True)

        if deliver_type == "github_comment":
            return await self._deliver_github_comment(content, delivery)

        if deliver_type == "notion_comment":
            if not (metadata or {}).get("notify"):
                logger.info(
                    "[webhook] Suppressing non-final Notion delivery for %s",
                    chat_id,
                )
                return SendResult(success=True)
            return await self._deliver_notion_comment(content, delivery)

        # Cross-platform delivery — any platform with a gateway adapter.
        # Check both built-in names and plugin-registered platforms.
        _is_known_platform = deliver_type in _BUILTIN_DELIVER_PLATFORMS
        if not _is_known_platform:
            try:
                from gateway.platform_registry import platform_registry
                _is_known_platform = platform_registry.is_registered(deliver_type)
            except Exception:
                pass
        if self.gateway_runner and _is_known_platform:
            return await self._deliver_cross_platform(
                deliver_type, content, delivery
            )

        logger.warning("[webhook] Unknown deliver type: %s", deliver_type)
        return SendResult(
            success=False, error=f"Unknown deliver type: {deliver_type}"
        )

    def _prune_delivery_info(self, now: float) -> None:
        """Drop delivery_info entries older than the idempotency TTL.

        Mirrors the cleanup pattern used for ``_seen_deliveries``.  Called
        on each POST so the dict size is bounded by ``rate_limit * TTL``
        even if many webhooks fire and never receive a final response.
        """
        if len(self._delivery_info_order) < len(self._delivery_info_created):
            self._delivery_info_order = deque(
                (created_at, key)
                for key, created_at in sorted(
                    self._delivery_info_created.items(), key=lambda item: item[1]
                )
            )
        cutoff = now - self._idempotency_ttl
        while self._delivery_info_order and self._delivery_info_order[0][0] < cutoff:
            created_at, key = self._delivery_info_order.popleft()
            if self._delivery_info_created.get(key) != created_at:
                continue
            self._delivery_info.pop(key, None)
            self._delivery_info_created.pop(key, None)

    def _prune_seen_deliveries(self, now: float) -> None:
        """Occasionally prune expired delivery IDs without scanning every POST."""
        if now < self._seen_deliveries_next_prune_at:
            return
        cutoff = now - self._idempotency_ttl
        stale = [k for k, t in self._seen_deliveries.items() if t < cutoff]
        for k in stale:
            self._seen_deliveries.pop(k, None)
        self._seen_deliveries_next_prune_at = now + min(60.0, max(1.0, self._idempotency_ttl / 10))

    def _record_rate_limit_hit(self, route_name: str, now: float) -> bool:
        """Return True if route is still within limit after recording this hit."""
        window = self._rate_counts.get(route_name)
        if not isinstance(window, deque):
            new_window: Deque[float] = deque(window or ())
            self._rate_counts[route_name] = new_window
            window = new_window
        cutoff = now - _RATE_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self._rate_limit:
            return False
        window.append(now)
        return True

    def _record_delivery_id(self, delivery_id: str, now: float) -> bool:
        """Return True when this delivery should be processed."""
        seen_at = self._seen_deliveries.get(delivery_id)
        if seen_at is not None and now - seen_at < self._idempotency_ttl:
            return False
        if seen_at is not None:
            self._seen_deliveries.pop(delivery_id, None)
        self._seen_deliveries[delivery_id] = now
        if len(self._seen_deliveries) > max(self._rate_limit * 2, 128):
            self._prune_seen_deliveries(now)
        return True

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "webhook"}

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def _handle_health(self, request: "web.Request") -> "web.Response":
        """GET /health — simple health check."""
        return web.json_response({"status": "ok", "platform": "webhook"})

    def _reload_dynamic_routes(self) -> None:
        """Reload agent-created subscriptions from disk if the file changed."""
        hermes_home = get_hermes_home()
        subs_path = hermes_home / _DYNAMIC_ROUTES_FILENAME
        if not subs_path.exists():
            if self._dynamic_routes:
                self._dynamic_routes = {}
                self._routes = dict(self._static_routes)
                logger.debug("[webhook] Dynamic subscriptions file removed, cleared dynamic routes")
            return
        try:
            mtime = subs_path.stat().st_mtime
            if mtime <= self._dynamic_routes_mtime:
                return  # No change
            data = json.loads(subs_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            # Merge: static routes take precedence over dynamic ones.
            # Reject any dynamic route whose effective secret is empty —
            # an empty secret would cause _handle_webhook to skip HMAC
            # validation entirely, letting unauthenticated callers in.
            new_dynamic: Dict[str, dict] = {}
            for k, v in data.items():
                if k in self._static_routes:
                    continue
                effective_secret = v.get("secret", self._global_secret)
                if self._is_notion_auth_route(v):
                    new_dynamic[k] = v
                    continue
                if not effective_secret:
                    logger.warning(
                        "[webhook] Dynamic route '%s' skipped: 'secret' is "
                        "missing or empty. Set a valid HMAC secret, or use "
                        "'%s' to explicitly disable auth (testing only).",
                        k,
                        _INSECURE_NO_AUTH,
                    )
                    continue
                if (
                    effective_secret == _INSECURE_NO_AUTH
                    and not _is_loopback_host(self._host)
                ):
                    logger.warning(
                        "[webhook] Dynamic route '%s' skipped: INSECURE_NO_AUTH "
                        "is only allowed on loopback hosts. Current host: '%s'.",
                        k,
                        self._host,
                    )
                    continue
                new_dynamic[k] = v
            self._dynamic_routes = new_dynamic
            self._routes = {**self._dynamic_routes, **self._static_routes}
            self._dynamic_routes_mtime = mtime
            logger.info(
                "[webhook] Reloaded %d dynamic route(s): %s",
                len(self._dynamic_routes),
                ", ".join(self._dynamic_routes.keys()) or "(none)",
            )
        except Exception as e:
            logger.error("[webhook] Failed to reload dynamic routes: %s", e)

    def _resolve_request_profile(self, request: "web.Request"):
        """Resolve + validate the /p/<profile>/ URL prefix on a webhook request.

        Returns:
          - ``None`` when no profile prefix is present, or multiplexing is off
            (the prefix is ignored, request handled as the default profile).
          - the profile name (str) when present, multiplexing is on, and the
            profile is one this gateway serves.
          - ``_PROFILE_REJECTED`` when a prefix is present but the profile is
            unknown/unconfigured (handler returns 404).
        """
        profile = (request.match_info.get("profile") or "").strip()
        if not profile:
            return None
        runner = self.gateway_runner
        cfg = getattr(runner, "config", None)
        if not getattr(cfg, "multiplex_profiles", False):
            # Prefix supplied but multiplexing is off — ignore it, behave as
            # the single-profile gateway (don't 404 a would-be valid route).
            return None
        try:
            from hermes_cli.profiles import profiles_to_serve
            served = {name for name, _ in profiles_to_serve(multiplex=True)}
        except Exception:
            return _PROFILE_REJECTED
        if profile not in served:
            return _PROFILE_REJECTED
        return profile

    async def _handle_webhook(self, request: "web.Request") -> "web.Response":
        """POST /webhooks/{route_name} — receive and process a webhook event."""
        # Hot-reload dynamic subscriptions on each request (mtime-gated, cheap)
        self._reload_dynamic_routes()

        route_name = request.match_info.get("route_name", "")
        route_config = self._routes.get(route_name)

        # Multi-profile: resolve + validate the /p/<profile>/ prefix if present.
        profile = self._resolve_request_profile(request)
        if profile is _PROFILE_REJECTED:
            return web.json_response(
                {"error": "Unknown or unconfigured profile"}, status=404
            )

        if not route_config:
            return web.json_response(
                {"error": f"Unknown route: {route_name}"}, status=404
            )

        # Disabled routes are kept in the subscriptions file (so the dashboard
        # can re-enable them) but reject incoming events.  Default-enabled:
        # only an explicit ``enabled: false`` turns a route off, matching the
        # mcp_servers ``enabled`` semantics.
        if route_config.get("enabled", True) is False:
            return web.json_response(
                {"error": f"Route disabled: {route_name}"}, status=403
            )

        # ── Auth-before-body ─────────────────────────────────────
        # Check Content-Length before reading the full payload.
        content_length = request.content_length or 0
        if content_length > self._max_body_bytes:
            return web.json_response(
                {"error": "Payload too large"}, status=413
            )

        # Read body (must be done before any validation)
        try:
            raw_body = await request.read()
        except Exception as e:
            logger.error("[webhook] Failed to read body: %s", e)
            return web.json_response({"error": "Bad request"}, status=400)

        # Notion has a two-step auth flow: the first request carries an
        # unsigned one-time verification_token; later event deliveries are
        # signed with X-Notion-Signature: sha256=<hmac> using that token as
        # the HMAC key. Keep that provider-specific boundary inside Hermes
        # instead of moving it into the public ingress proxy.
        if self._is_notion_auth_route(route_config):
            notion_status = self._handle_notion_auth(
                route_name, route_config, raw_body, request
            )
            if notion_status is not None:
                return notion_status
        else:
            # Validate HMAC signature FIRST (skip only for the explicit local-test
            # INSECURE_NO_AUTH mode). Missing/empty secrets must fail closed here,
            # not only during connect(), so direct handler reuse cannot turn a
            # network webhook route into an unauthenticated agent-dispatch surface.
            secret = route_config.get("secret", self._global_secret)
            if not secret:
                logger.error(
                    "[webhook] Route %s has no HMAC secret; refusing request",
                    route_name,
                )
                return web.json_response(
                    {"error": "Webhook route is missing an HMAC secret"},
                    status=403,
                )
            if secret != _INSECURE_NO_AUTH:
                if not self._validate_signature(request, raw_body, secret):
                    logger.warning(
                        "[webhook] Invalid signature for route %s", route_name
                    )
                    return web.json_response(
                        {"error": "Invalid signature"}, status=401
                    )

        # ── Rate limiting (after auth) ───────────────────────────
        now = time.time()
        if not self._record_rate_limit_hit(route_name, now):
            return web.json_response(
                {"error": "Rate limit exceeded"}, status=429
            )

        # Parse payload
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            # Try form-encoded as fallback
            try:
                import urllib.parse

                payload = dict(
                    urllib.parse.parse_qsl(raw_body.decode("utf-8"))
                )
            except Exception:
                return web.json_response(
                    {"error": "Cannot parse body"}, status=400
                )

        # Check event type filter
        event_type = (
            request.headers.get("X-GitHub-Event", "")
            or request.headers.get("X-GitLab-Event", "")
            or payload.get("event_type", "")
            or payload.get("type", "")
            or "unknown"
        )
        allowed_events = route_config.get("events", [])
        if allowed_events and event_type not in allowed_events:
            logger.debug(
                "[webhook] Ignoring event %s for route %s (allowed: %s)",
                event_type,
                route_name,
                allowed_events,
            )
            return web.json_response(
                {"status": "ignored", "event": event_type}
            )

        ignored_actor_id = self._ignored_actor_id_for_payload(
            route_config, payload
        )
        if ignored_actor_id:
            logger.info(
                "[webhook] Ignoring event %s for route %s from actor %s",
                event_type,
                route_name,
                ignored_actor_id,
            )
            return web.json_response(
                {
                    "status": "ignored",
                    "event": event_type,
                    "reason": "ignored_actor",
                    "actor_id": ignored_actor_id,
                },
                status=200,
            )

        notion_filter_response = await self._notion_trigger_filter_response(
            route_name, route_config, payload, event_type
        )
        if notion_filter_response is not None:
            return notion_filter_response

        # Format prompt from template
        prompt_template = route_config.get("prompt", "")
        prompt = self._render_prompt(
            prompt_template, payload, event_type, route_name
        )

        # Inject skill content if configured.
        # We call build_skill_invocation_message() directly rather than
        # using /skill-name slash commands — the gateway's command parser
        # would intercept those and break the flow.
        skills = route_config.get("skills", [])
        if skills:
            try:
                from agent.skill_commands import (
                    build_skill_invocation_message,
                    get_skill_commands,
                )

                skill_cmds = get_skill_commands()
                for skill_name in skills:
                    cmd_key = f"/{skill_name}"
                    if cmd_key in skill_cmds:
                        skill_content = build_skill_invocation_message(
                            cmd_key, user_instruction=prompt
                        )
                        if skill_content:
                            prompt = skill_content
                            break  # Load the first matching skill
                    else:
                        logger.warning(
                            "[webhook] Skill '%s' not found", skill_name
                        )
            except Exception as e:
                logger.warning("[webhook] Skill loading failed: %s", e)

        # Build a unique delivery ID. Notion does not send GitHub/Svix-style
        # delivery headers, but its event payloads include stable event IDs;
        # use those before falling back to a timestamp so webhook retries are
        # idempotent.
        delivery_id = request.headers.get(
            "X-GitHub-Delivery",
            request.headers.get(
                "svix-id",
                request.headers.get(
                    "X-Request-ID",
                    str(payload.get("id") or payload.get("event_id") or int(time.time() * 1000)),
                ),
            ),
        )

        # ── Idempotency ─────────────────────────────────────────
        # Skip duplicate deliveries (webhook retries).
        now = time.time()
        if not self._record_delivery_id(delivery_id, now):
            logger.info(
                "[webhook] Skipping duplicate delivery %s", delivery_id
            )
            return web.json_response(
                {"status": "duplicate", "delivery_id": delivery_id},
                status=200,
            )

        # ── Direct delivery mode (deliver_only) ─────────────────
        # Skip the agent entirely — the rendered prompt IS the message we
        # deliver.  Use case: external services (Supabase, monitoring,
        # cron jobs, other agents) that need to push a plain notification
        # to a user's chat with zero LLM cost.  Reuses the same HMAC auth,
        # rate limiting, idempotency, and template rendering as agent mode.
        if route_config.get("deliver_only"):
            delivery = {
                "deliver": route_config.get("deliver", "log"),
                "deliver_extra": self._render_delivery_extra(
                    route_config.get("deliver_extra", {}), payload
                ),
                "payload": payload,
            }
            logger.info(
                "[webhook] direct-deliver event=%s route=%s target=%s msg_len=%d delivery=%s",
                event_type,
                route_name,
                delivery["deliver"],
                len(prompt),
                delivery_id,
            )
            try:
                result = await self._direct_deliver(prompt, delivery)
            except Exception:
                logger.exception(
                    "[webhook] direct-deliver failed route=%s delivery=%s",
                    route_name,
                    delivery_id,
                )
                return web.json_response(
                    {"status": "error", "error": "Delivery failed", "delivery_id": delivery_id},
                    status=502,
                )

            if result.success:
                return web.json_response(
                    {
                        "status": "delivered",
                        "route": route_name,
                        "target": delivery["deliver"],
                        "delivery_id": delivery_id,
                    },
                    status=200,
                )
            # Delivery attempted but target rejected it — surface as 502
            # with a generic error (don't leak adapter-level detail).
            logger.warning(
                "[webhook] direct-deliver target rejected route=%s target=%s error=%s",
                route_name,
                delivery["deliver"],
                result.error,
            )
            return web.json_response(
                {"status": "error", "error": "Delivery failed", "delivery_id": delivery_id},
                status=502,
            )

        # Use delivery_id in session key so concurrent webhooks on the
        # same route get independent agent runs (not queued/interrupted).
        session_chat_id = f"webhook:{route_name}:{delivery_id}"

        # Store delivery info for send().  Read by every send() invocation
        # for this chat_id (interim status messages and the final response),
        # so we do NOT pop on send.  TTL-based cleanup keeps the dict bounded.
        deliver_config = {
            "deliver": route_config.get("deliver", "log"),
            "deliver_extra": self._render_delivery_extra(
                route_config.get("deliver_extra", {}), payload
            ),
            "payload": payload,
        }
        self._delivery_info[session_chat_id] = deliver_config
        self._delivery_info_created[session_chat_id] = now
        self._delivery_info_order.append((now, session_chat_id))
        self._prune_delivery_info(now)

        # Build source and event
        source = self.build_source(
            chat_id=session_chat_id,
            chat_name=f"webhook/{route_name}",
            chat_type="webhook",
            user_id=f"webhook:{route_name}",
            user_name=route_name,
        )
        if profile and isinstance(profile, str):
            source.profile = profile
        event = MessageEvent(
            text=prompt,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=payload,
            message_id=delivery_id,
        )

        logger.info(
            "[webhook] %s event=%s route=%s prompt_len=%d delivery=%s",
            request.method,
            event_type,
            route_name,
            len(prompt),
            delivery_id,
        )

        # Non-blocking — return 202 Accepted immediately
        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return web.json_response(
            {
                "status": "accepted",
                "route": route_name,
                "event": event_type,
                "delivery_id": delivery_id,
            },
            status=202,
        )

    # ------------------------------------------------------------------
    # Route-level actor filtering
    # ------------------------------------------------------------------

    def _configured_ignored_actor_ids(self, route_config: dict) -> Set[str]:
        """Return configured actor IDs that should never dispatch an agent run."""
        ignored: Set[str] = set()

        def collect(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, str):
                values: Iterable[Any] = value.replace("\n", ",").split(",")
            elif isinstance(value, (list, tuple, set)):
                values = value
            else:
                values = (value,)

            for raw in values:
                item = os.path.expandvars(os.path.expanduser(str(raw))).strip()
                if not item or "$" in item:
                    continue
                ignored.add(item.casefold())

        collect(route_config.get("ignore_actor_ids"))
        collect(route_config.get("ignored_actor_ids"))
        collect(route_config.get("ignore_author_ids"))
        filters = route_config.get("filters")
        if isinstance(filters, Mapping):
            collect(filters.get("ignore_actor_ids"))
            collect(filters.get("ignore_author_ids"))
        return ignored

    def _ignored_actor_id_for_payload(
        self, route_config: dict, payload: Any
    ) -> Optional[str]:
        ignored_ids = self._configured_ignored_actor_ids(route_config)
        if not ignored_ids or not isinstance(payload, Mapping):
            return None
        for actor_id in self._actor_ids_from_payload(payload):
            if actor_id.casefold() in ignored_ids:
                return actor_id
        return None

    def _actor_ids_from_payload(self, payload: Mapping[str, Any]) -> Iterable[str]:
        """Yield actor IDs from common webhook author fields only."""

        def ids_from_actor(actor: Any) -> Iterable[str]:
            if isinstance(actor, Mapping):
                for key in ("id", "user_id"):
                    candidate = actor.get(key)
                    if isinstance(candidate, str) and candidate:
                        yield candidate
                nested_user = actor.get("user")
                if isinstance(nested_user, Mapping):
                    yield from ids_from_actor(nested_user)
            elif isinstance(actor, list):
                for item in actor:
                    yield from ids_from_actor(item)

        for key in (
            "authors",
            "actor",
            "author",
            "created_by",
            "last_edited_by",
            "user",
            "sender",
        ):
            yield from ids_from_actor(payload.get(key))

        event = payload.get("event")
        if isinstance(event, Mapping):
            for key in (
                "authors",
                "actor",
                "author",
                "created_by",
                "last_edited_by",
                "user",
                "sender",
            ):
                yield from ids_from_actor(event.get(key))

    # ------------------------------------------------------------------
    # Notion trigger filtering
    # ------------------------------------------------------------------

    async def _notion_trigger_filter_response(
        self,
        route_name: str,
        route_config: dict,
        payload: Any,
        event_type: str,
    ) -> Optional["web.Response"]:
        """Return an early response when a Notion event should not run the agent."""
        trigger_config = route_config.get("notion_trigger_filter")
        if not isinstance(trigger_config, Mapping):
            return None
        if trigger_config.get("enabled", True) is False:
            return None
        if not self._is_notion_auth_route(route_config):
            return None
        if event_type not in {"comment.created", "page.properties_updated"}:
            return None
        if not isinstance(payload, dict):
            return web.json_response(
                {
                    "status": "ignored",
                    "event": event_type,
                    "reason": "notion_trigger_required",
                },
                status=200,
            )

        token = self._notion_api_token(route_config, trigger_config)
        if not token:
            logger.error(
                "[webhook] Notion trigger filter for route %s missing API token",
                route_name,
            )
            return web.json_response(
                {"status": "error", "error": "Missing Notion API token"},
                status=503,
            )

        notion_version = self._notion_api_version(route_config, trigger_config)
        bot_user_id = self._notion_filter_bot_user_id(trigger_config)
        bot_names = self._notion_filter_bot_names(trigger_config)
        allow_plain_text = bool(trigger_config.get("allow_plain_text_mentions", False))

        try:
            if event_type == "comment.created":
                return await self._notion_comment_trigger_filter_response(
                    route_name,
                    payload,
                    token,
                    notion_version,
                    bot_user_id,
                    bot_names,
                    allow_plain_text,
                )
            return await self._notion_assignment_trigger_filter_response(
                route_name,
                payload,
                token,
                notion_version,
                bot_user_id,
                bot_names,
                trigger_config,
            )
        except Exception:
            logger.exception(
                "[webhook] Notion trigger filter failed for route %s event=%s",
                route_name,
                event_type,
            )
            return web.json_response(
                {"status": "error", "error": "Notion trigger filter failed"},
                status=502,
            )

    async def _notion_comment_trigger_filter_response(
        self,
        route_name: str,
        payload: dict,
        token: str,
        notion_version: str,
        bot_user_id: str,
        bot_names: List[str],
        allow_plain_text: bool,
    ) -> Optional["web.Response"]:
        event_type = "comment.created"
        comment_id = self._notion_entity_id(payload, expected_type="comment")
        if not comment_id:
            return self._notion_trigger_ignored_response(
                event_type, "missing_comment_id"
            )

        comment = await asyncio.to_thread(
            self._get_notion_api_json,
            f"https://api.notion.com/v1/comments/{comment_id}",
            token,
            notion_version,
        )
        if not isinstance(comment, dict):
            return self._notion_trigger_ignored_response(
                event_type, "notion_trigger_required"
            )

        if self._ignored_actor_id_for_payload(
            {"ignore_actor_ids": [bot_user_id]},
            {"created_by": comment.get("created_by")},
        ):
            logger.info(
                "[webhook] Ignoring event %s for route %s from fetched comment author",
                event_type,
                route_name,
            )
            return self._notion_trigger_ignored_response(
                event_type, "ignored_actor"
            )

        rich_text = comment.get("rich_text", [])
        if self._notion_rich_text_mentions_bot(
            rich_text, bot_user_id, bot_names, allow_plain_text
        ):
            payload["_notion_context"] = {
                "trigger": "comment_mention",
                "comment": comment,
            }
            return None

        logger.info(
            "[webhook] Ignoring Notion comment.created for route %s: no bot mention",
            route_name,
        )
        return self._notion_trigger_ignored_response(
            event_type, "notion_trigger_required"
        )

    async def _notion_assignment_trigger_filter_response(
        self,
        route_name: str,
        payload: dict,
        token: str,
        notion_version: str,
        bot_user_id: str,
        bot_names: List[str],
        trigger_config: Mapping[str, Any],
    ) -> Optional["web.Response"]:
        event_type = "page.properties_updated"
        property_names = self._notion_assignment_property_names(trigger_config)
        updated_names = self._notion_updated_property_names(payload)
        if updated_names and property_names and updated_names.isdisjoint(
            {name.casefold() for name in property_names}
        ):
            logger.info(
                "[webhook] Ignoring Notion page.properties_updated for route %s: unrelated properties",
                route_name,
            )
            return self._notion_trigger_ignored_response(
                event_type, "notion_trigger_required"
            )

        page_id = self._notion_entity_id(payload, expected_type="page")
        if not page_id:
            return self._notion_trigger_ignored_response(
                event_type, "missing_page_id"
            )

        page = await asyncio.to_thread(
            self._get_notion_api_json,
            f"https://api.notion.com/v1/pages/{page_id}",
            token,
            notion_version,
        )
        if not isinstance(page, dict):
            return self._notion_trigger_ignored_response(
                event_type, "notion_trigger_required"
            )

        if self._notion_page_assigns_bot(
            page, bot_user_id, bot_names, property_names
        ):
            payload["_notion_context"] = {
                "trigger": "people_assignment",
                "page": page,
            }
            return None

        logger.info(
            "[webhook] Ignoring Notion page.properties_updated for route %s: no bot assignment",
            route_name,
        )
        return self._notion_trigger_ignored_response(
            event_type, "notion_trigger_required"
        )

    def _notion_trigger_ignored_response(
        self, event_type: str, reason: str
    ) -> "web.Response":
        return web.json_response(
            {"status": "ignored", "event": event_type, "reason": reason},
            status=200,
        )

    def _notion_api_token(
        self, route_config: dict, trigger_config: Mapping[str, Any]
    ) -> str:
        extra = route_config.get("deliver_extra", {})
        token_env = trigger_config.get("token_env")
        if not token_env and isinstance(extra, Mapping):
            token_env = extra.get("token_env")
        token_env = str(token_env or "NOTION_API_TOKEN")
        return os.environ.get(token_env) or os.environ.get("NOTION_API_KEY", "")

    def _notion_api_version(
        self, route_config: dict, trigger_config: Mapping[str, Any]
    ) -> str:
        extra = route_config.get("deliver_extra", {})
        version = trigger_config.get("notion_version")
        if not version and isinstance(extra, Mapping):
            version = extra.get("notion_version")
        return str(version or "2026-03-11")

    def _notion_filter_bot_user_id(
        self, trigger_config: Mapping[str, Any]
    ) -> str:
        value = trigger_config.get("bot_user_id", "")
        expanded = os.path.expandvars(os.path.expanduser(str(value))).strip()
        return "" if "$" in expanded else expanded

    def _notion_filter_bot_names(
        self, trigger_config: Mapping[str, Any]
    ) -> List[str]:
        value = trigger_config.get("bot_names", ["Hermes", "Hermes Agent"])
        if isinstance(value, str):
            names = value.replace("\n", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            names = [str(item) for item in value]
        else:
            names = []
        return [name.strip() for name in names if name and name.strip()]

    def _notion_assignment_property_names(
        self, trigger_config: Mapping[str, Any]
    ) -> List[str]:
        value = trigger_config.get("assignment_people_properties", ["Assignee"])
        if isinstance(value, str):
            names = value.replace("\n", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            names = [str(item) for item in value]
        else:
            names = []
        return [name.strip() for name in names if name and name.strip()]

    def _notion_entity_id(
        self, payload: Mapping[str, Any], *, expected_type: str
    ) -> str:
        entity = payload.get("entity")
        if isinstance(entity, Mapping):
            entity_id = entity.get("id")
            entity_type = entity.get("type")
            if (
                isinstance(entity_id, str)
                and entity_id
                and (not entity_type or entity_type == expected_type)
            ):
                return entity_id
        data = payload.get("data")
        if expected_type == "page" and isinstance(data, Mapping):
            page_id = data.get("page_id")
            if isinstance(page_id, str) and page_id:
                return page_id
        return ""

    def _notion_updated_property_names(
        self, payload: Mapping[str, Any]
    ) -> Set[str]:
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return set()
        updated = data.get("updated_properties")
        names: Set[str] = set()
        if isinstance(updated, list):
            for item in updated:
                if isinstance(item, Mapping):
                    name = item.get("name")
                    if isinstance(name, str) and name:
                        names.add(name.casefold())
                elif isinstance(item, str) and item:
                    names.add(item.casefold())
        return names

    def _notion_rich_text_mentions_bot(
        self,
        rich_text: Any,
        bot_user_id: str,
        bot_names: List[str],
        allow_plain_text: bool,
    ) -> bool:
        bot_id = bot_user_id.casefold()
        markers = {f"@{name}".casefold() for name in bot_names if name}
        if not isinstance(rich_text, list):
            return False
        for item in rich_text:
            if not isinstance(item, Mapping):
                continue
            mention = item.get("mention")
            if isinstance(mention, Mapping):
                user = mention.get("user")
                if isinstance(user, Mapping):
                    user_id = user.get("id")
                    if isinstance(user_id, str) and user_id.casefold() == bot_id:
                        return True
            if allow_plain_text:
                plain_text = item.get("plain_text")
                if isinstance(plain_text, str) and any(
                    marker in plain_text.casefold() for marker in markers
                ):
                    return True
        return False

    def _notion_page_assigns_bot(
        self,
        page: Mapping[str, Any],
        bot_user_id: str,
        bot_names: List[str],
        property_names: List[str],
    ) -> bool:
        properties = page.get("properties")
        if not isinstance(properties, Mapping):
            return False
        allowed = {name.casefold() for name in property_names}
        names = {name.casefold() for name in bot_names}
        bot_id = bot_user_id.casefold()
        for property_name, value in properties.items():
            if allowed and str(property_name).casefold() not in allowed:
                continue
            if not isinstance(value, Mapping):
                continue
            people = value.get("people")
            if not isinstance(people, list):
                continue
            for person in people:
                if not isinstance(person, Mapping):
                    continue
                person_id = person.get("id")
                if isinstance(person_id, str) and person_id.casefold() == bot_id:
                    return True
                person_name = person.get("name")
                if isinstance(person_name, str) and person_name.casefold() in names:
                    return True
        return False

    def _get_notion_api_json(
        self, url: str, token: str, notion_version: str
    ) -> dict:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": notion_version,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Notion API returned {exc.code}: {detail}") from exc

    # ------------------------------------------------------------------
    # Notion verification-token and signature handling
    # ------------------------------------------------------------------

    def _is_notion_auth_route(self, route_config: dict) -> bool:
        """Return True when a route uses Notion's webhook auth contract."""
        auth = route_config.get("auth") if isinstance(route_config, dict) else None
        mode = ""
        if isinstance(auth, dict):
            mode = str(auth.get("mode", ""))
        provider = (
            str(route_config.get("provider", ""))
            if isinstance(route_config, dict)
            else ""
        )
        return mode == "notion-signature" or provider == "notion"

    def _handle_notion_auth(
        self,
        route_name: str,
        route_config: dict,
        raw_body: bytes,
        request: "web.Request",
    ) -> Optional["web.Response"]:
        """Capture Notion verification tokens or validate signed events.

        Returns a response when the request should stop before normal webhook
        dispatch (verification captured or auth rejected). Returns None when
        the signed event passed validation and can continue through the normal
        event filter / prompt / dispatch path.
        """
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return web.json_response({"error": "Cannot parse body"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "Cannot parse body"}, status=400)

        verification_token = payload.get("verification_token")
        if isinstance(verification_token, str) and verification_token:
            try:
                self._store_notion_verification_token(
                    route_name, route_config, verification_token
                )
            except Exception:
                logger.exception(
                    "[webhook] Failed to store Notion verification token for route %s",
                    route_name,
                )
                return web.json_response(
                    {"error": "Failed to store verification token"}, status=500
                )
            logger.info(
                "[webhook] Captured Notion verification token for route %s",
                route_name,
            )
            return web.json_response(
                {"status": "verification_token_captured", "route": route_name},
                status=200,
            )

        token = self._load_notion_verification_token(route_name, route_config)
        if not token:
            logger.warning(
                "[webhook] Notion event for route %s arrived before verification token capture",
                route_name,
            )
            return web.json_response(
                {"error": "Notion verification token is missing"}, status=401
            )

        signature = request.headers.get("X-Notion-Signature", "")
        if not self._validate_notion_signature(raw_body, token, signature):
            logger.warning("[webhook] Invalid Notion signature for route %s", route_name)
            return web.json_response({"error": "Invalid Notion signature"}, status=401)
        return None

    def _notion_token_store_path(self, route_config: dict) -> Path:
        auth = route_config.get("auth") if isinstance(route_config, dict) else None
        configured = ""
        if isinstance(auth, dict):
            configured = str(auth.get("token_store", ""))
        configured = configured or str(route_config.get("token_store", ""))
        if not configured:
            configured = str(get_hermes_home() / "notion_webhook_tokens.json")
        configured = configured.replace("${HERMES_HOME}", str(get_hermes_home()))
        configured = os.path.expandvars(os.path.expanduser(configured))
        return Path(configured)

    def _read_notion_token_store(self, route_config: dict) -> dict:
        path = self._notion_token_store_path(route_config)
        if not path.exists():
            return {"routes": {}}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("Notion token store must be a JSON object")
        routes = data.setdefault("routes", {})
        if not isinstance(routes, dict):
            raise ValueError("Notion token store routes must be an object")
        return data

    def _store_notion_verification_token(
        self, route_name: str, route_config: dict, verification_token: str
    ) -> None:
        path = self._notion_token_store_path(route_config)
        data = self._read_notion_token_store(route_config)
        data["routes"][route_name] = {
            "route": route_name,
            "verification_token": verification_token,
            "captured_at": int(time.time()),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            logger.debug("[webhook] Could not chmod Notion token store temp file")
        os.replace(tmp, path)

    def _load_notion_verification_token(
        self, route_name: str, route_config: dict
    ) -> str:
        auth = route_config.get("auth") if isinstance(route_config, dict) else None
        if isinstance(auth, dict):
            inline = auth.get("verification_token")
            if isinstance(inline, str) and inline:
                return inline
        inline = route_config.get("verification_token")
        if isinstance(inline, str) and inline:
            return inline
        try:
            data = self._read_notion_token_store(route_config)
            token = data.get("routes", {}).get(route_name, {}).get(
                "verification_token", ""
            )
            return token if isinstance(token, str) else ""
        except Exception as exc:
            logger.warning("[webhook] Could not read Notion token store: %s", exc)
            return ""

    def _validate_notion_signature(
        self, body: bytes, verification_token: str, signature_header: str
    ) -> bool:
        """Validate X-Notion-Signature: sha256=<hmac> over raw body bytes."""
        if not (verification_token and signature_header):
            return False
        signature = signature_header.strip()
        if signature.startswith("sha256="):
            signature = signature[len("sha256=") :]
        if len(signature) != 64:
            return False
        try:
            int(signature, 16)
        except ValueError:
            return False
        expected = hmac.new(
            verification_token.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature.lower(), expected)

    # ------------------------------------------------------------------
    # Signature validation
    # ------------------------------------------------------------------

    def _validate_signature(
        self, request: "web.Request", body: bytes, secret: str
    ) -> bool:
        """Validate webhook signature (GitHub, GitLab, Svix, generic HMAC-SHA256)."""
        def _header(name: str) -> str:
            return (
                request.headers.get(name, "")
                or request.headers.get(name.lower(), "")
                or request.headers.get(name.upper(), "")
            )

        # Svix / AgentMail:
        #   svix-id: msg_...
        #   svix-timestamp: unix seconds
        #   svix-signature: v1,<base64-hmac> [v1,<base64-hmac> ...]
        # Signed content is: "{id}.{timestamp}.{raw_body}".  Svix secrets
        # usually start with "whsec_" and the remainder is base64-encoded.
        svix_id = _header("svix-id")
        svix_timestamp = _header("svix-timestamp")
        svix_signature = _header("svix-signature")
        if svix_id or svix_timestamp or svix_signature:
            return self._validate_svix_signature(
                body=body,
                secret=secret,
                msg_id=svix_id,
                timestamp=svix_timestamp,
                signature_header=svix_signature,
            )

        # GitHub: X-Hub-Signature-256 = sha256=<hex>
        gh_sig = request.headers.get("X-Hub-Signature-256", "")
        if gh_sig:
            expected = "sha256=" + hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(gh_sig, expected)

        # GitLab: X-Gitlab-Token = <plain secret>
        gl_token = request.headers.get("X-Gitlab-Token", "")
        if gl_token:
            return hmac.compare_digest(gl_token, secret)

        # Generic: X-Webhook-Signature = <hex HMAC-SHA256>
        generic_sig = request.headers.get("X-Webhook-Signature", "")
        if generic_sig:
            expected = hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(generic_sig, expected)

        # No recognised signature header but secret is configured → reject
        logger.debug(
            "[webhook] Secret configured but no signature header found"
        )
        return False

    def _validate_svix_signature(
        self,
        body: bytes,
        secret: str,
        msg_id: str,
        timestamp: str,
        signature_header: str,
        tolerance_seconds: int = 300,
    ) -> bool:
        """Validate Svix-compatible signatures used by AgentMail webhooks."""
        if not (msg_id and timestamp and signature_header and secret):
            return False

        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            return False
        if abs(int(time.time()) - ts) > tolerance_seconds:
            logger.warning("[webhook] Svix signature timestamp outside replay window")
            return False

        if secret.startswith("whsec_"):
            encoded_secret = secret.removeprefix("whsec_")
            try:
                key = base64.b64decode(encoded_secret, validate=True)
            except (binascii.Error, ValueError):
                logger.debug("[webhook] Invalid whsec_ Svix signing secret")
                return False
        else:
            # Be permissive for providers that document Svix-style headers but
            # hand out raw shared secrets rather than whsec_ base64 secrets.
            logger.debug("[webhook] Validating Svix-style signature with raw secret")
            key = secret.encode()

        signed_content = msg_id.encode() + b"." + timestamp.encode() + b"." + body
        expected = base64.b64encode(
            hmac.new(key, signed_content, hashlib.sha256).digest()
        ).decode()

        # Svix can send multiple signatures separated by spaces during secret
        # rotation. Each entry is formatted as "vN,<base64>".
        for part in signature_header.split():
            try:
                version, signature = part.split(",", 1)
            except ValueError:
                continue
            if version == "v1" and hmac.compare_digest(signature, expected):
                return True
        return False

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def _render_prompt(
        self,
        template: str,
        payload: dict,
        event_type: str,
        route_name: str,
    ) -> str:
        """Render a prompt template with the webhook payload.

        Supports dot-notation access into nested dicts:
        ``{pull_request.title}`` → ``payload["pull_request"]["title"]``

        Special token ``{__raw__}`` dumps the entire payload as indented
        JSON (truncated to 4000 chars).  Useful for monitoring alerts or
        any webhook where the agent needs to see the full payload.
        """
        if not template:
            truncated = json.dumps(payload, indent=2)[:4000]
            return (
                f"Webhook event '{event_type}' on route "
                f"'{route_name}':\n\n```json\n{truncated}\n```"
            )

        def _resolve(match: re.Match) -> str:
            key = match.group(1)
            # Special token: dump the entire payload as JSON
            if key == "__raw__":
                return json.dumps(payload, indent=2)[:4000]
            value: Any = payload
            for part in key.split("."):
                if isinstance(value, dict):
                    value = value.get(part, f"{{{key}}}")
                else:
                    return f"{{{key}}}"
            if isinstance(value, (dict, list)):
                return json.dumps(value, indent=2)[:2000]
            return str(value)

        return re.sub(r"\{([a-zA-Z0-9_.]+)\}", _resolve, template)

    def _render_delivery_extra(
        self, extra: dict, payload: dict
    ) -> dict:
        """Render delivery_extra template values with payload data."""
        rendered: Dict[str, Any] = {}
        for key, value in extra.items():
            if isinstance(value, str):
                rendered[key] = self._render_prompt(value, payload, "", "")
            else:
                rendered[key] = value
        return rendered

    # ------------------------------------------------------------------
    # Response delivery
    # ------------------------------------------------------------------

    async def _direct_deliver(
        self, content: str, delivery: dict
    ) -> SendResult:
        """Deliver *content* directly without invoking the agent.

        Used by ``deliver_only`` routes: the rendered template becomes the
        literal message body, and we dispatch to the same delivery helpers
        that the agent-mode ``send()`` flow uses.  All target types that
        work in agent mode work here — Telegram, Discord, Slack, GitHub
        PR comments, etc.
        """
        deliver_type = delivery.get("deliver", "log")

        if deliver_type == "log":
            # Shouldn't reach here — startup validation rejects deliver_only
            # with deliver=log — but guard defensively.
            logger.info("[webhook] direct-deliver log-only: %s", content[:200])
            return SendResult(success=True)

        if deliver_type == "github_comment":
            return await self._deliver_github_comment(content, delivery)

        if deliver_type == "notion_comment":
            return await self._deliver_notion_comment(content, delivery)

        # Fall through to the cross-platform dispatcher, which validates the
        # target name and routes via the gateway runner.
        return await self._deliver_cross_platform(
            deliver_type, content, delivery
        )

    async def _deliver_notion_comment(
        self, content: str, delivery: dict
    ) -> SendResult:
        """Post the final webhook response as a Notion page/block comment."""
        extra = delivery.get("deliver_extra", {})
        payload = delivery.get("payload", {})
        token_env = str(extra.get("token_env") or "NOTION_API_TOKEN")
        token = os.environ.get(token_env) or os.environ.get("NOTION_API_KEY")
        if not token:
            logger.error("[webhook] notion_comment delivery missing %s", token_env)
            return SendResult(success=False, error=f"Missing {token_env}")

        body: Dict[str, Any] = {"markdown": str(content or "").strip()}
        if not body["markdown"]:
            return SendResult(success=True)

        discussion_id = extra.get("discussion_id") or self._notion_comment_discussion_id(payload)
        if isinstance(discussion_id, str) and discussion_id and "{" not in discussion_id:
            body["discussion_id"] = discussion_id
        else:
            parent = self._notion_comment_parent(extra, payload)
            if not parent:
                logger.error("[webhook] notion_comment delivery missing page/block target")
                return SendResult(success=False, error="Missing Notion comment target")
            body["parent"] = parent

        notion_version = str(extra.get("notion_version") or "2026-03-11")
        try:
            response = await asyncio.to_thread(
                self._post_notion_comment,
                body,
                token,
                notion_version,
            )
        except Exception as exc:
            logger.error("[webhook] Notion comment delivery error: %s", exc)
            return SendResult(success=False, error=str(exc))

        comment_id = response.get("id") if isinstance(response, dict) else None
        logger.info("[webhook] Posted Notion comment %s", comment_id or "(unknown id)")
        return SendResult(success=True, message_id=comment_id, raw_response=response)

    def _notion_comment_discussion_id(self, payload: dict) -> str:
        for path in (
            ("discussion_id",),
            ("comment", "discussion_id"),
            ("data", "discussion_id"),
        ):
            value: Any = payload
            for part in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
            if isinstance(value, str) and value:
                return value
        return ""

    def _notion_comment_parent(self, extra: dict, payload: dict) -> dict:
        page_id = extra.get("page_id")
        if isinstance(page_id, str) and page_id and "{" not in page_id:
            return {"page_id": page_id}
        block_id = extra.get("block_id")
        if isinstance(block_id, str) and block_id and "{" not in block_id:
            return {"block_id": block_id}

        for path in (
            ("entity",),
            ("page",),
            ("block",),
            ("object",),
            ("parent",),
            ("comment", "parent"),
            ("data", "parent"),
        ):
            value: Any = payload
            for part in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
            if not isinstance(value, dict):
                continue
            target_id = value.get("id")
            target_type = value.get("type") or value.get("object")
            if isinstance(target_id, str) and target_id:
                if target_type == "block":
                    return {"block_id": target_id}
                if target_type in {"page", "page_id"} or path == ("page",):
                    return {"page_id": target_id}
        return {}

    def _post_notion_comment(
        self, body: dict, token: str, notion_version: str
    ) -> dict:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            "https://api.notion.com/v1/comments",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Notion-Version": notion_version,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Notion API returned {exc.code}: {detail}") from exc

    async def _deliver_github_comment(
        self, content: str, delivery: dict
    ) -> SendResult:
        """Post agent response as a GitHub PR/issue comment via ``gh`` CLI."""
        extra = delivery.get("deliver_extra", {})
        repo = extra.get("repo", "")
        pr_number = extra.get("pr_number", "")

        if not repo or not pr_number:
            logger.error(
                "[webhook] github_comment delivery missing repo or pr_number"
            )
            return SendResult(
                success=False, error="Missing repo or pr_number"
            )

        # --- Input validation (prevent CLI argument injection) ---
        # pr_number must be a positive integer.
        try:
            pr_int = int(pr_number)
            if pr_int <= 0:
                raise ValueError("non-positive")
        except (ValueError, TypeError):
            logger.error(
                "[webhook] invalid pr_number: %r", pr_number
            )
            return SendResult(
                success=False, error="Invalid pr_number"
            )

        # repo must match owner/name (alphanumeric, hyphens, underscores, dots).
        if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo):
            logger.error("[webhook] invalid repo format: %r", repo)
            return SendResult(
                success=False, error="Invalid repo format"
            )

        try:
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "comment",
                    str(pr_int),
                    "--repo",
                    repo,
                    "--body",
                    content,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                logger.info(
                    "[webhook] Posted comment on %s#%s", repo, pr_number
                )
                return SendResult(success=True)
            else:
                logger.error(
                    "[webhook] gh pr comment failed: %s", result.stderr
                )
                return SendResult(success=False, error=result.stderr)
        except FileNotFoundError:
            logger.error(
                "[webhook] 'gh' CLI not found — install GitHub CLI for "
                "github_comment delivery"
            )
            return SendResult(
                success=False, error="gh CLI not installed"
            )
        except Exception as e:
            logger.error("[webhook] github_comment delivery error: %s", e)
            return SendResult(success=False, error=str(e))

    async def _deliver_cross_platform(
        self, platform_name: str, content: str, delivery: dict
    ) -> SendResult:
        """Route response to another platform (telegram, discord, etc.)."""
        if not self.gateway_runner:
            return SendResult(
                success=False,
                error="No gateway runner for cross-platform delivery",
            )

        try:
            target_platform = Platform(platform_name)
        except ValueError:
            return SendResult(
                success=False, error=f"Unknown platform: {platform_name}"
            )

        adapter = self.gateway_runner.adapters.get(target_platform)
        if not adapter:
            return SendResult(
                success=False,
                error=f"Platform {platform_name} not connected",
            )

        # Use home channel if no specific chat_id in deliver_extra
        extra = delivery.get("deliver_extra", {})
        chat_id = extra.get("chat_id", "")
        if not chat_id:
            home = self.gateway_runner.config.get_home_channel(target_platform)
            if home:
                chat_id = home.chat_id
            else:
                return SendResult(
                    success=False,
                    error=f"No chat_id or home channel for {platform_name}",
                )

        # Pass thread_id from deliver_extra so Telegram forum topics work
        metadata = None
        thread_id = extra.get("message_thread_id") or extra.get("thread_id")
        if thread_id:
            metadata = {"thread_id": thread_id}

        return await adapter.send(chat_id, content, metadata=metadata)
