"""ZAI / GLM provider profile.

GLM-5.2+ models support ``thinking`` (enabled/disabled) with an optional
``effort`` field (``high`` / ``max``) inside the thinking object.
Older GLM models (5, 5.1, 4.x) only support ``type: enabled/disabled``.

When the user has not configured reasoning, no thinking parameter is sent
to preserve the default wire format.  This mirrors the pattern used by the
DeepSeek profile (:mod:`plugins.model-providers.deepseek`).
"""

from __future__ import annotations

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


def _model_supports_effort(model: str | None) -> bool:
    """Return True for GLM models that accept the ``effort`` field.

    GLM-5.2 is currently the only model that supports effort. Match
    ``glm-5.2`` exactly or as a hyphen/suffix boundary (e.g.
    ``glm-5.2-preview``) but not ``glm-5.20``.
    """
    m = (model or "").lower().strip()
    # Strip vendor prefix (zai/, z-ai/, zhipu/)
    for prefix in ("zai/", "z-ai/", "zhipu/"):
        if m.startswith(prefix):
            m = m[len(prefix):]
    # Exact match or glm-5.2-* (variant suffix like -preview, -turbo).
    # A regex-free boundary check: prefix must be followed by end-of-string
    # or a hyphen, not another digit.
    return m == "glm-5.2" or m.startswith("glm-5.2-")


class ZAIProfile(ProviderProfile):
    """Z.AI / GLM — thinking + effort inside the thinking object."""

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, model: str | None = None, **context
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # When no reasoning config is set, preserve the default wire format
        # (no thinking parameter) to avoid changing behavior for existing users.
        if not isinstance(reasoning_config, dict):
            return {}, {}

        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}

        enabled = reasoning_config.get("enabled", True) is not False
        thinking: dict[str, Any] = {"type": "enabled" if enabled else "disabled"}

        # Effort is GLM-5.2+ only. Older models (5.1, 5, 4.x) ignore the
        # field, so don't send it — keeps the wire format clean.
        if enabled and _model_supports_effort(model):
            effort = (reasoning_config.get("effort") or "").strip().lower()
            # Map Hermes effort levels to GLM's high/max.
            # Hermes valid efforts: none, minimal, low, medium, high, xhigh.
            if effort in {"xhigh", "max"}:
                thinking["effort"] = "max"
            elif effort == "high":
                thinking["effort"] = "high"
            # Lower efforts (none/minimal/low/medium) → omit, GLM uses server default.

        extra_body["thinking"] = thinking
        return extra_body, top_level


zai = ZAIProfile(
    name="zai",
    aliases=("glm", "z-ai", "z.ai", "zhipu"),
    env_vars=("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
    display_name="Z.AI (GLM)",
    description="Z.AI / GLM — Zhipu AI models",
    signup_url="https://z.ai/",
    fallback_models=(
        "glm-5.2",
        "glm-5",
        "glm-4-9b",
    ),
    base_url="https://api.z.ai/api/paas/v4",
    default_aux_model="glm-4.5-flash",
)

register_provider(zai)
