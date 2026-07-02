"""Local HTTP fetch + readability extract provider — plugin form.

Subclasses the plugin-facing :class:`agent.web_search_provider.WebSearchProvider`.
No API key required — uses httpx for HTTP GET and readability-lxml for
main-content extraction. Extract-only (``supports_search() -> False``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time as time_module
from typing import Any, Dict, List, Optional

import httpx

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)


# ─── Configurable via environment variables (HERMES_WEB_FETCH_*) ─────────────

_WEB_FETCH_CACHE_TTL = int(os.environ.get("HERMES_WEB_FETCH_CACHE_TTL", "900"))  # 15 min
_WEB_FETCH_TIMEOUT = int(os.environ.get("HERMES_WEB_FETCH_TIMEOUT", "30"))
_WEB_FETCH_MAX_RESPONSE_BYTES = int(os.environ.get("HERMES_WEB_FETCH_MAX_RESPONSE_BYTES", "2000000"))  # 2 MB
_WEB_FETCH_MAX_REDIRECTS = int(os.environ.get("HERMES_WEB_FETCH_MAX_REDIRECTS", "5"))
_WEB_FETCH_MAX_CHARS = int(os.environ.get("HERMES_WEB_FETCH_MAX_CHARS", "50000"))
_WEB_FETCH_MAX_CHARS_CAP = int(os.environ.get("HERMES_WEB_FETCH_MAX_CHARS_CAP", "200000"))
_WEB_FETCH_READABILITY = os.environ.get("HERMES_WEB_FETCH_READABILITY", "true").lower() in ("true", "1", "yes")
_WEB_FETCH_USE_TRUSTED_PROXY = os.environ.get("HERMES_WEB_FETCH_USE_TRUSTED_PROXY", "false").lower() in ("true", "1", "yes")
_WEB_FETCH_USER_AGENT = os.environ.get(
    "HERMES_WEB_FETCH_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# In-memory cache
_WEB_FETCH_CACHE: Dict[str, tuple[float, str]] = {}


def _clamp_max_chars(max_chars: Optional[int]) -> int:
    if max_chars is None or max_chars < 1:
        max_chars = _WEB_FETCH_MAX_CHARS
    return min(max_chars, _WEB_FETCH_MAX_CHARS_CAP)


async def _fetch_single_url(
    url: str,
    max_chars: int = 50000,
    extract_mode: str = "markdown",
) -> Dict[str, Any]:
    """Fetch a single URL and extract readable content."""
    if not url or not isinstance(url, str):
        return {"url": str(url), "title": "", "content": "", "raw_content": "", "error": "URL is required"}

    url = url.strip()
    max_chars = _clamp_max_chars(max_chars)

    # ── SSRF check ────────────────────────────────────────────────
    try:
        from tools.url_safety import async_is_safe_url, normalize_url_for_request

        normalized_url = normalize_url_for_request(url)
        if not await async_is_safe_url(normalized_url):
            return {
                "url": url, "title": "", "content": "", "raw_content": "",
                "error": "Blocked: URL targets a private or internal network address",
            }
    except Exception:
        normalized_url = url

    # ── Cache check ───────────────────────────────────────────────
    cache_key = normalized_url
    cached = _WEB_FETCH_CACHE.get(cache_key)
    if cached and (time_module.monotonic() - cached[0]) < _WEB_FETCH_CACHE_TTL:
        result = cached[1]
        raw = result
        if max_chars and len(result) > max_chars:
            result = result[:max_chars] + "\n\n[... truncated ...]"
        return {"url": url, "content": result, "raw_content": raw}

    # ── Proxy ─────────────────────────────────────────────────────
    proxy_url = None
    if _WEB_FETCH_USE_TRUSTED_PROXY:
        proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or None

    # ── Fetch ─────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_WEB_FETCH_TIMEOUT),
            follow_redirects=True,
            max_redirects=_WEB_FETCH_MAX_REDIRECTS,
            proxy=proxy_url,
        ) as client:
            response = await client.get(
                normalized_url,
                headers={
                    "User-Agent": _WEB_FETCH_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "text/" not in content_type and "application/xhtml" not in content_type:
                body = response.text
                raw = body
                if max_chars and len(body) > max_chars:
                    body = body[:max_chars] + "\n\n[... truncated ...]"
                return {"url": url, "content": body, "raw_content": raw, "content_type": content_type}
            html = response.text
    except httpx.TimeoutException:
        return {"url": url, "title": "", "content": "", "raw_content": "", "error": f"Request timed out after {_WEB_FETCH_TIMEOUT}s"}
    except httpx.HTTPStatusError as e:
        return {"url": url, "title": "", "content": "", "raw_content": "", "error": f"HTTP {e.response.status_code}: {e.response.reason_phrase}"}
    except Exception as e:
        return {"url": url, "title": "", "content": "", "raw_content": "", "error": f"Fetch failed: {type(e).__name__}: {e}"}

    # ── Response size check ──────────────────────────────────────
    if len(html) > _WEB_FETCH_MAX_RESPONSE_BYTES:
        html = html[:_WEB_FETCH_MAX_RESPONSE_BYTES]

    # ── Extract readable content ──────────────────────────────────
    readable_title = ""
    readable_html = html

    if _WEB_FETCH_READABILITY:
        try:
            from readability import Document

            doc = Document(html, url=normalized_url)
            readable_title = doc.title()
            readable_html = doc.summary()
        except Exception as e:
            try:
                from lxml import html as lhtml

                tree = lhtml.fromstring(html)
                for tag in tree.xpath("//script|//style|//nav|//footer|//header|//aside"):
                    tag.getparent().remove(tag)
                readable_html = lhtml.tostring(tree, encoding="unicode")
                readable_title = tree.findtext(".//title", default="")
            except Exception:
                return {"url": url, "title": "", "content": "", "raw_content": "", "error": f"Content extraction failed: {e}"}
    else:
        try:
            from lxml import html as lhtml

            tree = lhtml.fromstring(html)
            readable_title = tree.findtext(".//title", default="")
            for tag in tree.xpath("//script|//style"):
                tag.getparent().remove(tag)
            readable_html = lhtml.tostring(tree, encoding="unicode")
        except Exception:
            readable_title = ""
            readable_html = html

    # ── Convert to markdown ───────────────────────────────────────
    try:
        import html2text

        converter = html2text.HTML2Text()
        converter.body_width = 0
        converter.ignore_links = False
        converter.ignore_images = True
        converter.ignore_emphasis = False
        converter.protect_links = True
        converter.unicode_snob = True
        converter.skip_internal_links = True
        if extract_mode == "text":
            converter.ignore_links = True
            converter.ignore_emphasis = True
        content = converter.handle(readable_html)
    except Exception as e:
        return {"url": url, "title": "", "content": "", "raw_content": "", "error": f"Markdown conversion failed: {e}"}

    # ── Clean up ──────────────────────────────────────────────────
    content = re.sub(r"\n{4,}", "\n\n\n", content)
    content = content.strip()
    full_content = f"# {readable_title}\n\n{content}" if readable_title else content
    raw_content = full_content
    if max_chars and len(full_content) > max_chars:
        full_content = full_content[:max_chars] + "\n\n[... truncated ...]"

    _WEB_FETCH_CACHE[cache_key] = (time_module.monotonic(), raw_content)

    return {"url": url, "title": readable_title or "", "content": full_content, "raw_content": raw_content}


class WebFetchWebSearchProvider(WebSearchProvider):
    """Local HTTP fetch extract provider — no API key needed."""

    @property
    def name(self) -> str:
        return "native"

    @property
    def display_name(self) -> str:
        return "Native Web Fetch"

    def is_available(self) -> bool:
        try:
            import readability  # noqa: F401
            import html2text  # noqa: F401

            return True
        except ImportError:
            return False

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        extract_mode = "markdown"
        fmt = kwargs.get("format")
        if fmt and isinstance(fmt, str) and fmt.lower().strip() == "text":
            extract_mode = "text"

        tasks = [_fetch_single_url(u, extract_mode=extract_mode) for u in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final: List[Dict[str, Any]] = []
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                final.append({
                    "url": urls[i] if i < len(urls) else "",
                    "title": "", "content": "", "raw_content": "", "error": f"Internal error: {r}",
                })
            else:
                final.append(r)
        return final

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Local Web Fetch (web-fetch)",
            "badge": "free · no key · extract only",
            "tag": "Fetches content via httpx + readability — no API key. Pair with any search provider.",
            "env_vars": [],
        }