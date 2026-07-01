"""Fuse Browser web provider — bundled backend.

Routes Hermes' generic ``web_search`` / ``web_extract`` tools through the
Fuse Browser CLI so agents can keep using the standard Hermes tool names while
getting Fuse Browser evidence-first behaviour underneath.
"""

from __future__ import annotations

from plugins.web.fuse_browser.provider import FuseBrowserWebProvider


def register(ctx) -> None:
    """Register Fuse Browser as a native web search/extract provider."""
    ctx.register_web_search_provider(FuseBrowserWebProvider())
