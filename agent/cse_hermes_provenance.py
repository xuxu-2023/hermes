"""Native CSE/Hermes body-signal provenance envelope helpers.

This module is deliberately sideband-only: it builds redaction-safe metadata for
CSE-facing provider calls and never mutates model-visible messages.  The first
consumer is the local ``cse-live`` OpenAI-compatible provider façade, which
expects the envelope under ``metadata.cse_hermes_provenance``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from typing import Any

PROVENANCE_METADATA_KEY = "cse_hermes_provenance"
EXPECTED_SCHEMA_VERSION = "cse.hermes.provenance.v1"
CSE_PROVIDER_MODEL_IDS = {"cse-live"}
_ALLOWED_ATOM_RE = re.compile(r"[^A-Za-z0-9_.:-]+")

_SOURCE_KINDS = {"cli", "gateway", "telegram", "cron", "heartbeat", "test_harness", "unknown"}
_SURFACE_KINDS = {"direct_chat", "group_chat", "topic_thread", "cli", "local_daemon", "unknown"}
_PLATFORM_REF_PLATFORMS = {"telegram", "gateway", "cli", "cron", "test_harness", "unknown"}
_PROCESS_LOCAL_REF_SALT = secrets.token_hex(32)


def should_emit_cse_hermes_provenance(agent: Any) -> bool:
    """Return true when this provider call should carry CSE provenance.

    The default is intentionally narrow so ordinary providers are untouched.
    Falsey ``HERMES_CSE_HERMES_PROVENANCE`` values disable emission for
    troubleshooting; truthy values do not broaden emission beyond CSE routes.
    """

    override = os.getenv("HERMES_CSE_HERMES_PROVENANCE")
    if override is not None and override.strip().lower() in {"0", "false", "no", "off"}:
        return False

    model = str(getattr(agent, "model", "") or "").strip().lower()
    if model in CSE_PROVIDER_MODEL_IDS:
        return True
    return any(model.endswith(f"/{model_id}") for model_id in CSE_PROVIDER_MODEL_IDS)


def attach_cse_hermes_provenance_metadata(
    agent: Any,
    api_kwargs: dict[str, Any],
    api_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach native CSE provenance to OpenAI-compatible chat kwargs.

    The returned dict is the same request object with a redaction-safe
    ``metadata.cse_hermes_provenance`` entry.  Existing caller-provided
    metadata keys are preserved, but the CSE provenance key itself is
    overwritten so request overrides cannot spoof a native body signal.
    """

    if not should_emit_cse_hermes_provenance(agent):
        return api_kwargs

    metadata = api_kwargs.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    metadata[PROVENANCE_METADATA_KEY] = build_cse_hermes_provenance(
        agent,
        api_messages,
        tools_present=bool(api_kwargs.get("tools")),
    )
    api_kwargs["metadata"] = metadata
    return api_kwargs


def build_cse_hermes_provenance(
    agent: Any,
    api_messages: list[dict[str, Any]],
    *,
    tools_present: bool = False,
) -> dict[str, Any]:
    """Build a redaction-safe native body-signal provenance envelope."""

    profile_id = _safe_profile_id(_active_profile_name())
    raw_session_id = _safe_str(getattr(agent, "session_id", None)) or "anonymous"
    session_ref = _prefixed_hash("session", raw_session_id)
    trace_ref = _prefixed_hash("trace", profile_id, raw_session_id)
    conversation_ref = _prefixed_hash("conversation", profile_id, raw_session_id)
    request_ref = f"req_{uuid.uuid4().hex[:24]}"
    api_call_count = getattr(agent, "_api_call_count", None)
    turn_seed = f"{raw_session_id}:{api_call_count}:{request_ref}"
    turn_ref = _prefixed_hash("turn", turn_seed)

    source_kind = _source_kind(agent)
    surface_kind = _surface_kind(agent, source_kind)
    message_kind = _message_kind(api_messages)
    capability_mode = (
        "tool_round_trip"
        if tools_present or message_kind in {"tool_request", "tool_result", "tool_denial"} or _messages_contain_tool_semantics(api_messages)
        else "text_only_pr2"
    )
    complete = source_kind != "unknown" and surface_kind != "unknown"

    provenance: dict[str, Any] = {
        "schema_version": EXPECTED_SCHEMA_VERSION,
        "request_id": request_ref,
        "trace_id": trace_ref,
        "turn_id": turn_ref,
        "hermes_profile_id": profile_id,
        "hermes_session_id": session_ref,
        "source_kind": source_kind,
        "surface_kind": surface_kind,
        "message_kind": message_kind,
        "entity_id": "cse-live",
        "conversation_id": conversation_ref,
        "capability_mode": capability_mode,
        "provenance_policy": "required_for_v1" if complete else "optional_transient",
        "provenance_completeness": "complete" if complete else "partial",
        "payload_redaction_state": "redacted",
        "raw_identifier_values_included": False,
        "producer": {
            "system": "hermes-agent",
            "kind": "native_body_signal_producer",
            "authority": "hermes",
        },
        "delivery_context": {
            "body_route_ref": _ref("body_route", profile_id, source_kind, surface_kind, raw_session_id),
            "delivery_state": "provider_request_pre_delivery",
            "delivery_authority": "hermes",
        },
        "audit_context": {
            "audit_ref": _ref("audit", trace_ref, request_ref, turn_ref),
            "raw_payloads_included": False,
        },
        "memory_context": {
            "transfer_policy": "none",
            "host_context_kinds": [],
            "host_context_ref_count": 0,
            "cse_memory_ref_count": 0,
        },
    }

    platform_refs = _platform_refs(agent, source_kind, surface_kind)
    if platform_refs:
        provenance["platform_refs"] = platform_refs

    return provenance


def _active_profile_name() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name

        return get_active_profile_name() or "default"
    except Exception:
        return os.getenv("HERMES_PROFILE") or "default"


def _source_kind(agent: Any) -> str:
    platform = _safe_str(getattr(agent, "platform", None)).lower()
    if platform in _SOURCE_KINDS:
        return platform
    if platform in {"discord", "slack", "signal", "whatsapp", "matrix", "sms"}:
        return "gateway"
    return "unknown"


def _surface_kind(agent: Any, source_kind: str) -> str:
    if source_kind == "cli":
        return "cli"
    if source_kind in {"cron", "heartbeat", "test_harness"}:
        return "local_daemon"

    thread_id = _safe_str(getattr(agent, "thread_id", None))
    if thread_id:
        return "topic_thread"

    chat_type = _safe_str(getattr(agent, "chat_type", None)).lower()
    if chat_type in {"group", "supergroup", "channel"}:
        return "group_chat"
    if chat_type in {"dm", "private", "direct"}:
        return "direct_chat"

    chat_id = _safe_str(getattr(agent, "chat_id", None))
    if chat_id:
        return "direct_chat"
    return "unknown"


def _message_kind(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "tool":
            return "tool_result"
        if role == "function":
            return "tool_result"
        if role == "assistant" and message.get("tool_calls"):
            return "tool_request"
        if role == "user":
            return "user_message"
    return "user_message"


def _messages_contain_tool_semantics(messages: list[dict[str, Any]]) -> bool:
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") in {"tool", "function"}:
            return True
        if any(field in message for field in ("tool_call_id", "tool_calls", "function_call")):
            return True
    return False


def _platform_refs(agent: Any, source_kind: str, surface_kind: str) -> dict[str, str] | None:
    platform = source_kind if source_kind in _PLATFORM_REF_PLATFORMS else "unknown"
    refs: dict[str, str] = {"platform": platform}

    chat_id = _safe_str(getattr(agent, "chat_id", None))
    user_id = _safe_str(getattr(agent, "user_id", None)) or _safe_str(getattr(agent, "user_id_alt", None))
    thread_id = _safe_str(getattr(agent, "thread_id", None))

    if chat_id:
        refs["chat_ref"] = _ref("chat", platform, chat_id)
    if user_id:
        refs["user_ref"] = _ref("user", platform, user_id)
    if thread_id:
        key = "topic_ref" if platform == "telegram" or surface_kind == "topic_thread" else "thread_ref"
        refs[key] = _ref("topic" if key == "topic_ref" else "thread", platform, thread_id)

    return refs if len(refs) > 1 or platform in {"cli", "cron", "test_harness"} else None


def _safe_profile_id(value: str) -> str:
    cleaned = _safe_atom(value or "default")
    return cleaned or "default"


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _safe_atom(value: str) -> str:
    cleaned = _ALLOWED_ATOM_RE.sub("_", _safe_str(value))
    cleaned = cleaned.strip("._:-")
    return cleaned or "unknown"


def _ref(kind: str, *parts: Any) -> str:
    return f"ref:{kind}:{_digest(*parts)}"


def _prefixed_hash(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{_digest(*parts)}"


def _digest(*parts: Any) -> str:
    payload = json.dumps([str(part) for part in parts], separators=(",", ":"), sort_keys=True)
    return hmac.new(_ref_salt().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def _ref_salt() -> str:
    env_salt = os.getenv("HERMES_CSE_PROVENANCE_REF_SALT")
    if env_salt:
        return env_salt
    try:
        from hermes_constants import get_hermes_home

        salt_path = get_hermes_home() / "cse_hermes_provenance_salt"
        if salt_path.exists():
            value = salt_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        salt_path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        try:
            fd = os.open(str(salt_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            value = salt_path.read_text(encoding="utf-8").strip()
            if value:
                return value
            raise
        try:
            os.write(fd, (value + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        return value
    except Exception:
        # Last-resort process-local salt: still avoids a public static salt,
        # though refs may not be stable if the local profile cannot store the salt.
        return _PROCESS_LOCAL_REF_SALT
