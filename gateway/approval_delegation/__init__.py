"""Approval Delegation — state management for cross-platform approval routing.

When ``approvals.delegation.enabled`` is ``true`` in config.yaml, non-admin
users' dangerous-command approval requests are routed to an admin's chat
instead of the user's own chat.  The admin approves/denies from their own
DM, and the result is relayed back to the original user's session.

This module provides:
- Delegation config loading (``get_delegation_config``)
- Admin lookup and user classification (``is_admin_user``, ``get_admins``)
- Delegation state management (``register_delegation``, ``resolve_delegation``,
  ``clear_delegation``) for mapping admin chat → original session

No monkey-patching — integration is via the standard approval flow in
``tools/approval.py`` and ``gateway/run.py``.
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────

_delegation_config: Optional[Dict] = None
_delegation_config_lock = threading.Lock()


def get_delegation_config() -> Dict:
    """Load ``approvals.delegation`` from config.yaml (lazy, thread-safe).

    Returns a dict with ``enabled`` (bool) and ``admins`` (list of dicts
    with ``platform``, ``user_id``, and optional ``chat_id``).
    """
    global _delegation_config
    if _delegation_config is not None:
        return _delegation_config

    with _delegation_config_lock:
        if _delegation_config is not None:
            return _delegation_config

        cfg: Dict = {"enabled": False, "admins": []}
        try:
            from hermes_cli.config import load_config
            config = load_config()
            approvals = config.get("approvals", {}) or {}
            raw = approvals.get("delegation", {})
            if isinstance(raw, dict):
                cfg["enabled"] = bool(raw.get("enabled", False))
                raw_admins = raw.get("admins", [])
                if isinstance(raw_admins, list):
                    cfg["admins"] = [
                        {
                            "platform": str(a.get("platform", "")).strip().lower(),
                            "user_id": str(a.get("user_id", "")).strip(),
                            "chat_id": str(
                                a.get("chat_id", a.get("user_id", ""))
                            ).strip(),
                        }
                        for a in raw_admins
                        if isinstance(a, dict)
                        and a.get("platform")
                        and a.get("user_id")
                    ]
        except Exception as e:
            logger.warning("[approval-delegation] Failed to load config: %s", e)

        _delegation_config = cfg
        if cfg["enabled"]:
            logger.info(
                "[approval-delegation] Enabled with %d admin(s): %s",
                len(cfg["admins"]),
                [f"{a['platform']}:{a['user_id']}" for a in cfg["admins"]],
            )
        return cfg


def reload_delegation_config() -> None:
    """Force reload of delegation config (e.g. after config edit)."""
    global _delegation_config
    with _delegation_config_lock:
        _delegation_config = None
    get_delegation_config()


def is_delegation_enabled() -> bool:
    """Quick check if delegation is enabled."""
    return get_delegation_config().get("enabled", False)


def get_admins() -> List[Dict]:
    """Return the list of admin dicts (platform, user_id, chat_id)."""
    return get_delegation_config().get("admins", [])


def is_admin_user(platform: str, user_id: str) -> bool:
    """Check if the given user is an admin (never delegated)."""
    plat = str(platform).strip().lower()
    uid = str(user_id).strip()
    return any(
        a["platform"] == plat and a["user_id"] == uid
        for a in get_admins()
    )


def get_first_admin() -> Optional[Dict]:
    """Return the first admin entry, or None if no admins configured."""
    admins = get_admins()
    return admins[0] if admins else None


# ── Delegation state ────────────────────────────────────────────────────
#
# When an approval is delegated, we store a mapping:
#   admin_chat_key → delegation entry
# so that when the admin sends /approve or /deny, we can resolve the
# original session's pending approval.

_delegation_map: Dict[str, Dict[str, Any]] = {}
_delegation_lock = threading.Lock()
_DELEGATION_TTL = 600  # 10 minutes


def _admin_chat_key(platform: str, chat_id: str) -> str:
    """Build the lookup key for the delegation map."""
    return f"{str(platform).strip().lower()}:{str(chat_id).strip()}"


def register_delegation(
    *,
    admin_platform: str,
    admin_chat_id: str,
    session_key: str,
    user_platform: str,
    user_chat_id: str,
    user_chat_meta: Any = None,
    command: str = "",
    description: str = "",
) -> None:
    """Register a pending delegation: admin_chat → original session.

    Called when an approval is redirected to an admin.
    """
    key = _admin_chat_key(admin_platform, admin_chat_id)
    entry = {
        "session_key": session_key,
        "user_platform": user_platform,
        "user_chat_id": user_chat_id,
        "user_chat_meta": user_chat_meta,
        "command": command,
        "description": description,
        "created_at": time.monotonic(),
    }
    with _delegation_lock:
        _delegation_map[key] = entry
    logger.info(
        "[approval-delegation] Registered: admin=%s → session=%s cmd=%s",
        key, session_key[:16], command[:60],
    )


def resolve_delegation(platform: str, chat_id: str) -> Optional[Dict[str, Any]]:
    """Look up a pending delegation for the given admin chat.

    Returns the delegation entry dict, or None if no pending delegation.
    Automatically prunes stale entries.
    """
    key = _admin_chat_key(platform, chat_id)
    with _delegation_lock:
        entry = _delegation_map.get(key)
        if entry is None:
            return None
        # Check TTL
        if time.monotonic() - entry["created_at"] > _DELEGATION_TTL:
            del _delegation_map[key]
            return None
        return entry


def clear_delegation(platform: str, chat_id: str) -> None:
    """Remove a delegation entry after it's been resolved."""
    key = _admin_chat_key(platform, chat_id)
    with _delegation_lock:
        _delegation_map.pop(key, None)


def clear_all_delegations() -> None:
    """Clear all delegation state (e.g. on gateway shutdown)."""
    with _delegation_lock:
        _delegation_map.clear()
