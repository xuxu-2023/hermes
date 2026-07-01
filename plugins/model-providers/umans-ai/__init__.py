"""Umans AI provider profile.

Umans AI (api.code.umans.ai/v1) is an OpenAI-compatible inference provider
offering models from multiple labs (GLM, Kimi, Qwen) under unified model IDs.
All models support reasoning and tool calling.

Key behaviours:
  - Sends top-level ``reasoning_effort`` for reasoning-capable models,
    mapping Hermes effort levels to Umans wire values (xhigh → "max").
  - Strips internal ``timestamp`` fields from messages — the Umans API uses
    strict Pydantic validation and 400s on unknown message fields.
  - Vision-capable (GLM models accept image content).
  - Per-model temperature support: GLM models accept it; Kimi/Qwen models
    do not and are handled via models.dev capability metadata.
"""

from __future__ import annotations

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


# Models that do not accept a temperature parameter (per models.dev).
# Kimi and Qwen families manage sampling server-side.
_NO_TEMPERATURE_PREFIXES = ("umans-kimi", "umans-coder", "umans-flash")


def _model_supports_reasoning(model: str | None) -> bool:
    """All Umans models support reasoning per models.dev."""
    return bool(model)


class UmansProfile(ProviderProfile):
    """Umans AI — reasoning_effort + strict message sanitization."""

    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip internal ``timestamp`` fields that the Umans API rejects.

        Hermes adds ``timestamp`` to message dicts for session persistence
        (loaded back from SQLite on session resume).  The general
        ``convert_messages`` sanitizer strips it in the deep-copy path,
        but the fast path (no other internal fields present) returns the
        original list.  This hook ensures ``timestamp`` is always stripped
        for Umans regardless of which ``convert_messages`` path was taken.

        New dicts are created only for messages that carry ``timestamp``;
        all others are reused as-is (no deep copy of the whole list).
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, dict) and "timestamp" in msg:
                msg = {k: v for k, v in msg.items() if k != "timestamp"}
            result.append(msg)
        return result

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        model: str | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Send ``reasoning_effort`` as a top-level API parameter.

        Returns (extra_body_additions, top_level_kwargs).

        Effort mapping mirrors the DeepSeek pattern:
          - low / medium / high → pass through as-is
          - xhigh / max → "max"
          - When no effort is configured, omit the parameter so the
            server applies its default.
        """
        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}

        if not _model_supports_reasoning(model):
            return extra_body, top_level

        # Respect explicit "disabled" from reasoning_config.
        if isinstance(reasoning_config, dict) and reasoning_config.get("enabled") is False:
            return extra_body, top_level

        if isinstance(reasoning_config, dict):
            effort = (reasoning_config.get("effort") or "").strip().lower()
            if effort in {"xhigh", "max"}:
                top_level["reasoning_effort"] = "max"
            elif effort in {"low", "medium", "high"}:
                top_level["reasoning_effort"] = effort

        return extra_body, top_level


umans_ai = UmansProfile(
    name="umans-ai",
    aliases=("umans", "custom:umans"),
    display_name="Umans AI",
    description="Umans AI — multi-model coding plan (GLM, Kimi, Qwen)",
    signup_url="https://umans.ai/",
    env_vars=("UMANS_API_KEY", "UMANS_BASE_URL"),
    base_url="https://api.code.umans.ai/v1",
    auth_type="api_key",
    supports_vision=True,
    fallback_models=(
        "umans-glm-5.2",
        "umans-glm-5.1",
        "umans-kimi-k2.7",
        "umans-coder",
        "umans-flash",
    ),
    default_aux_model="umans-flash",
)

register_provider(umans_ai)
