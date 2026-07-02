"""DashScope (Alibaba Cloud) image generation backend.

Exposes DashScope's Tongyi Wanxiang models as an
:class:`ImageGenProvider` implementation.

Features:
- Text-to-image generation via DashScope async task API
- Multiple aspect ratios (landscape, square, portrait)
- Multiple model tiers (wanx2.1-t2i-turbo, wanx2.1-t2i-plus, wanx-v1)
- Async polling with configurable timeout
- Reuses existing DASHSCOPE_API_KEY from chat inference

Selection precedence (first hit wins):
1. ``DASHSCOPE_IMAGE_MODEL`` env var
2. ``image_gen.dashscope.model`` in ``config.yaml``
3. :data:`DEFAULT_MODEL`
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

_MODELS: Dict[str, Dict[str, Any]] = {
    "wanx2.1-t2i-turbo": {
        "display": "Wanx 2.1 Turbo",
        "speed": "~5-10s",
        "strengths": "Fast, good quality — default",
    },
    "wanx2.1-t2i-plus": {
        "display": "Wanx 2.1 Plus",
        "speed": "~15-30s",
        "strengths": "Higher fidelity / detail",
    },
    "wanx-v1": {
        "display": "Wanx v1",
        "speed": "~10-20s",
        "strengths": "Original model, wide compatibility",
    },
}

DEFAULT_MODEL = "wanx2.1-t2i-turbo"

# DashScope size mapping per aspect ratio.
# wanx2.1-t2i-turbo supports: 512*1024, 1024*512, 1024*1024, 768*1024,
# 1024*768, 864*1152, 1152*864, 1440*720, 720*1440
# wanx-v1 supports: 512*512, 768*768, 1024*1024
_SIZES: Dict[str, Dict[str, str]] = {
    "wanx2.1-t2i-turbo": {
        "landscape": "1024*576",
        "square": "1024*1024",
        "portrait": "576*1024",
    },
    "wanx2.1-t2i-plus": {
        "landscape": "1024*576",
        "square": "1024*1024",
        "portrait": "576*1024",
    },
    "wanx-v1": {
        "landscape": "1024*1024",  # v1 has limited aspect ratio support
        "square": "1024*1024",
        "portrait": "1024*1024",
    },
}

# Async polling configuration
_POLL_INTERVAL = 2.0  # seconds between polls
_POLL_TIMEOUT = 120.0  # max seconds to wait for task completion

# DashScope API endpoints
_SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_dashscope_config() -> Dict[str, Any]:
    """Read ``image_gen.dashscope`` from config.yaml."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        ds_section = section.get("dashscope") if isinstance(section, dict) else None
        return ds_section if isinstance(ds_section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen.dashscope config: %s", exc)
        return {}


def _resolve_model() -> Tuple[str, Dict[str, Any]]:
    """Decide which model to use and return ``(model_id, meta)``."""
    env_override = os.environ.get("DASHSCOPE_IMAGE_MODEL")
    if env_override and env_override in _MODELS:
        return env_override, _MODELS[env_override]

    cfg = _load_dashscope_config()
    candidate = cfg.get("model") if isinstance(cfg.get("model"), str) else None
    if candidate and candidate in _MODELS:
        return candidate, _MODELS[candidate]

    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _resolve_api_key() -> str:
    """Resolve DashScope API key from env or config."""
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if key:
        return key
    # Fallback: check .env via hermes constants
    try:
        from hermes_constants import get_hermes_home

        env_file = get_hermes_home() / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("DASHSCOPE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class DashScopeImageGenProvider(ImageGenProvider):
    """DashScope Tongyi Wanxiang image generation backend."""

    @property
    def name(self) -> str:
        return "dashscope"

    @property
    def display_name(self) -> str:
        return "DashScope (Alibaba)"

    def is_available(self) -> bool:
        return bool(_resolve_api_key())

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta.get("display", model_id),
                "speed": meta.get("speed", ""),
                "strengths": meta.get("strengths", ""),
            }
            for model_id, meta in _MODELS.items()
        ]

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "DashScope (Alibaba Cloud)",
            "badge": "paid",
            "tag": "Tongyi Wanxiang — text-to-image; uses DASHSCOPE_API_KEY",
            "env_vars": [
                {
                    "key": "DASHSCOPE_API_KEY",
                    "prompt": "DashScope API key (Alibaba Cloud)",
                    "url": "https://dashscope.console.aliyun.com/apiKey",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Generate an image using DashScope Tongyi Wanxiang."""
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="dashscope",
                aspect_ratio=aspect,
            )

        api_key = _resolve_api_key()
        if not api_key:
            return error_response(
                error=(
                    "DASHSCOPE_API_KEY not set. Run `hermes tools` → Image "
                    "Generation → DashScope to configure, or set "
                    "DASHSCOPE_API_KEY in your environment."
                ),
                error_type="auth_required",
                provider="dashscope",
                aspect_ratio=aspect,
            )

        model_id, meta = _resolve_model()
        sizes = _SIZES.get(model_id, _SIZES[DEFAULT_MODEL])
        size = sizes.get(aspect, sizes["square"])

        # Submit async task
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

        payload = {
            "model": model_id,
            "input": {
                "prompt": prompt,
            },
            "parameters": {
                "size": size,
                "n": 1,
            },
        }

        try:
            response = requests.post(
                _SUBMIT_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            resp = exc.response
            status = resp.status_code if resp is not None else 0
            try:
                err_body = resp.json()
                err_msg = (
                    err_body.get("message")
                    or err_body.get("error", {}).get("message", "")
                    or resp.text[:300]
                )
            except Exception:
                err_msg = resp.text[:300] if resp is not None else str(exc)
            logger.error("DashScope image gen submit failed (%d): %s", status, err_msg)
            return error_response(
                error=f"DashScope image generation failed ({status}): {err_msg}",
                error_type="api_error",
                provider="dashscope",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except requests.Timeout:
            return error_response(
                error="DashScope image generation request timed out (30s)",
                error_type="timeout",
                provider="dashscope",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except requests.ConnectionError as exc:
            return error_response(
                error=f"DashScope connection error: {exc}",
                error_type="connection_error",
                provider="dashscope",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            result = response.json()
        except Exception as exc:
            return error_response(
                error=f"DashScope returned invalid JSON: {exc}",
                error_type="invalid_response",
                provider="dashscope",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Extract task_id from submit response
        output = result.get("output", {})
        task_id = output.get("task_id", "")
        if not task_id:
            return error_response(
                error=f"DashScope did not return a task_id: {result}",
                error_type="invalid_response",
                provider="dashscope",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Poll for task completion
        poll_url = _TASK_URL.format(task_id=task_id)
        poll_headers = {"Authorization": f"Bearer {api_key}"}
        deadline = time.monotonic() + _POLL_TIMEOUT

        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            try:
                poll_resp = requests.get(
                    poll_url,
                    headers=poll_headers,
                    timeout=15,
                )
                poll_resp.raise_for_status()
                task_result = poll_resp.json()
            except Exception as exc:
                logger.warning("DashScope task poll failed: %s", exc)
                continue

            task_output = task_result.get("output", {})
            task_status = task_output.get("task_status", "")

            if task_status == "SUCCEEDED":
                results = task_output.get("results", [])
                if not results:
                    return error_response(
                        error="DashScope task succeeded but returned no images",
                        error_type="empty_response",
                        provider="dashscope",
                        model=model_id,
                        prompt=prompt,
                        aspect_ratio=aspect,
                    )
                image_url = results[0].get("url", "")
                if not image_url:
                    return error_response(
                        error="DashScope task result missing image URL",
                        error_type="empty_response",
                        provider="dashscope",
                        model=model_id,
                        prompt=prompt,
                        aspect_ratio=aspect,
                    )
                # Download and cache the image
                try:
                    saved_path = save_url_image(image_url, prefix=f"dashscope_{model_id}")
                    image_ref = str(saved_path)
                except Exception as exc:
                    logger.warning(
                        "DashScope image URL %s could not be cached (%s); falling back to bare URL.",
                        image_url,
                        exc,
                    )
                    image_ref = image_url

                extra: Dict[str, Any] = {"size": size}
                return success_response(
                    image=image_ref,
                    model=model_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                    provider="dashscope",
                    extra=extra,
                )

            elif task_status == "FAILED":
                code = task_output.get("code", "")
                message = task_output.get("message", "Unknown error")
                return error_response(
                    error=f"DashScope image generation failed: {code} — {message}",
                    error_type="provider_error",
                    provider="dashscope",
                    model=model_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )

            # PENDING or RUNNING — continue polling
            logger.debug(
                "DashScope task %s status: %s, waiting...",
                task_id,
                task_status,
            )

        # Timeout
        return error_response(
            error=f"DashScope image generation timed out ({_POLL_TIMEOUT}s)",
            error_type="timeout",
            provider="dashscope",
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
        )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def register(ctx: Any) -> None:
    """Register this provider with the image gen registry."""
    ctx.register_image_gen_provider(DashScopeImageGenProvider())
