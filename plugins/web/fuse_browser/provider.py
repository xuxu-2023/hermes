"""Fuse Browser provider for Hermes web_search / web_extract.

This backend makes Hermes' generic web_search / web_extract tools use the
published Fuse Browser MCP server as the execution substrate. Hermes keeps its
native tool API (query+limit for search, URL list for extract), while Fuse
Browser supplies the browser/HTTP implementation and advanced defaults via env
configuration. For every MCP capability beyond the generic web schemas, Hermes
also exposes fuse_browser_mcp: a full JSON passthrough router to any live Fuse
Browser MCP tool.
"""

from __future__ import annotations

import json
import logging
import math
import os
import select
import shutil
import subprocess
import time
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)


# ─── Commands ────────────────────────────────────────────────────────────────

def _split_cmd(value: str) -> list[str]:
    # Keep compatibility with the previous simple override. If a path contains
    # spaces, point FUSE_BROWSER_*_CMD to a wrapper script instead.
    return value.strip().split()


def _base_cli_cmd() -> list[str]:
    """Return the Fuse Browser CLI command as argv (fallback path)."""
    override = _env("FUSE_BROWSER_CLI")
    if override:
        return _split_cmd(override)
    return ["npx", "-y", "-p", "@fusengine/browser-mcp@latest", "fuse-browser"]


def _base_mcp_cmd() -> list[str]:
    """Return the Fuse Browser MCP stdio command as argv."""
    override = _env("FUSE_BROWSER_MCP_CMD")
    if override:
        return _split_cmd(override)
    return ["npx", "-y", "-p", "@fusengine/browser-mcp@latest", "browser-mcp"]


def _run_fuse_cli(args: list[str], *, timeout: int = 180) -> tuple[int, str, str]:
    cmd = _base_cli_cmd() + args
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ─── Env Helpers ─────────────────────────────────────────────────────────────

def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _env_bool(name: str) -> bool:
    return _env(name).lower() in {"1", "true", "yes", "on"}


def _env_bool_optional(name: str) -> bool | None:
    value = _env(name).lower()
    if not value:
        return None
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _env_int(name: str) -> int | None:
    value = _env(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning("Ignoring invalid integer env %s=%r", name, value)
        return None


def _env_json_obj(name: str) -> dict[str, Any]:
    value = _env(name)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring invalid JSON env %s: %s", name, exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Ignoring JSON env %s because it is not an object", name)
        return {}
    return parsed


def _put_str(opts: dict[str, Any], key: str, env_name: str) -> None:
    value = _env(env_name)
    if value:
        opts[key] = value


def _put_int(opts: dict[str, Any], key: str, env_name: str) -> None:
    value = _env_int(env_name)
    if value is not None:
        opts[key] = value


def _put_bool(opts: dict[str, Any], key: str, env_name: str) -> None:
    value = _env_bool_optional(env_name)
    if value is not None:
        opts[key] = value


def _append_env_opt(args: list[str], flag: str, env_name: str) -> None:
    value = _env(env_name)
    if value:
        args.extend([flag, value])


# ─── MCP stdio client ────────────────────────────────────────────────────────

def _read_jsonrpc(proc: subprocess.Popen[str], msg_id: int, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = ""
            try:
                stderr = proc.stderr.read() if proc.stderr else ""
            except Exception:
                pass
            raise RuntimeError(f"Fuse Browser MCP exited early ({proc.returncode}): {stderr.strip()}")
        remaining = max(0.1, deadline - time.monotonic())
        ready, _, _ = select.select([proc.stdout], [], [], min(1.0, remaining))
        if not ready:
            continue
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON MCP stdout line: %s", line[:300])
            continue
        if msg.get("id") == msg_id:
            return msg
    raise TimeoutError(f"Fuse Browser MCP timed out waiting for response id={msg_id}")


def _mcp_send(proc: subprocess.Popen[str], msg: dict[str, Any]) -> None:
    if not proc.stdin:
        raise RuntimeError("Fuse Browser MCP stdin unavailable")
    proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def _mcp_call(tool_name: str, arguments: dict[str, Any], *, timeout: int = 180) -> dict[str, Any]:
    """Call a Fuse Browser MCP tool over stdio and parse its JSON text result."""
    proc = subprocess.Popen(
        _base_mcp_cmd(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        _mcp_send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "hermes-fuse-browser-provider", "version": "1.0"},
                },
            },
        )
        init = _read_jsonrpc(proc, 1, timeout)
        if "error" in init:
            raise RuntimeError(f"Fuse Browser MCP initialize failed: {init['error']}")
        _mcp_send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _mcp_send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
        resp = _read_jsonrpc(proc, 2, timeout)
        if "error" in resp:
            raise RuntimeError(f"Fuse Browser MCP tool error: {resp['error']}")
        result = resp.get("result") or {}
        if result.get("isError"):
            texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
            raise RuntimeError("\n".join(texts) or "Fuse Browser MCP returned isError=true")
        for item in result.get("content", []):
            if item.get("type") != "text":
                continue
            text = item.get("text") or ""
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
        return result
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


# ─── Fuse Browser option mapping ─────────────────────────────────────────────

def _agent_options() -> dict[str, Any]:
    """Env-backed defaults for Fuse Browser MCP tools using agentOptionShape."""
    opts: dict[str, Any] = {}

    # Sensible Swiss/Fusengine defaults for daily searches.
    opts["engine"] = _env("FUSE_BROWSER_ENGINE") or "patchright"
    opts["countryCode"] = _env("FUSE_BROWSER_COUNTRY") or _env("FUSE_BROWSER_COUNTRY_CODE") or "CH"

    _put_str(opts, "channel", "FUSE_BROWSER_CHANNEL")
    _put_str(opts, "executablePath", "FUSE_BROWSER_EXECUTABLE_PATH")
    _put_str(opts, "cdpEndpoint", "FUSE_BROWSER_CDP_ENDPOINT")
    headers = _env_json_obj("FUSE_BROWSER_CDP_HEADERS_JSON")
    if headers:
        opts["cdpHeaders"] = headers
    _put_bool(opts, "cdpCloseOnDone", "FUSE_BROWSER_CDP_CLOSE_ON_DONE")
    _put_int(opts, "cdpTimeoutMs", "FUSE_BROWSER_CDP_TIMEOUT_MS")
    _put_bool(opts, "headless", "FUSE_BROWSER_HEADLESS")
    if _env_bool("FUSE_BROWSER_HEADED"):
        opts["headless"] = False
    _put_bool(opts, "humanMode", "FUSE_BROWSER_HUMAN_MODE")
    _put_str(opts, "locale", "FUSE_BROWSER_LOCALE")
    _put_str(opts, "timezoneId", "FUSE_BROWSER_TIMEZONE_ID")
    _put_str(opts, "currency", "FUSE_BROWSER_CURRENCY")
    _put_str(opts, "userDataDir", "FUSE_BROWSER_USER_DATA_DIR")
    _put_str(opts, "proxyUrl", "FUSE_BROWSER_PROXY")
    _put_str(opts, "proxyMapPath", "FUSE_BROWSER_PROXY_MAP")
    _put_str(opts, "proxiesPath", "FUSE_BROWSER_PROXIES_PATH")
    _put_str(opts, "storageStatePath", "FUSE_BROWSER_STORAGE_STATE")
    _put_str(opts, "harPath", "FUSE_BROWSER_HAR_PATH")
    _put_str(opts, "harMode", "FUSE_BROWSER_HAR_MODE")
    _put_str(opts, "harReplay", "FUSE_BROWSER_HAR_REPLAY")
    _put_bool(opts, "realisticProfile", "FUSE_BROWSER_REALISTIC_PROFILE")
    _put_bool(opts, "respectRobots", "FUSE_BROWSER_RESPECT_ROBOTS")
    _put_bool(opts, "replayEnabled", "FUSE_BROWSER_REPLAY")
    _put_str(opts, "replayDir", "FUSE_BROWSER_REPLAY_DIR")
    _put_str(opts, "siteMemoryDir", "FUSE_BROWSER_SITE_MEMORY_DIR")
    _put_str(opts, "outputDir", "FUSE_BROWSER_OUTPUT_DIR")

    for env_name, key in (
        ("FUSE_BROWSER_RETRY_JSON", "retry"),
        ("FUSE_BROWSER_CAPTCHA_JSON", "captcha"),
        ("FUSE_BROWSER_CIRCUIT_BREAKER_JSON", "circuitBreaker"),
        ("FUSE_BROWSER_PROBE_QUEUE_JSON", "probeQueue"),
    ):
        value = _env_json_obj(env_name)
        if value:
            opts[key] = value

    # Escape hatch: complete MCP agentOptionShape passthrough/override.
    opts.update(_env_json_obj("FUSE_BROWSER_AGENT_OPTIONS_JSON"))
    return {k: v for k, v in opts.items() if v is not None and v != ""}


def _search_args(query: str, safe_limit: int, pages: int) -> dict[str, Any]:
    args = _agent_options()
    args.update(
        {
            "queries": [query],
            "pages": pages,
            "hl": _env("FUSE_BROWSER_HL") or "fr",
            "gl": _env("FUSE_BROWSER_GL") or "ch",
            "delayMs": _env_int("FUSE_BROWSER_SERP_DELAY_MS") or 800,
        }
    )
    rank_domain = _env("FUSE_BROWSER_RANK_DOMAIN")
    if rank_domain:
        args["rankDomain"] = rank_domain
    args.update(_env_json_obj("FUSE_BROWSER_SEARCH_OPTIONS_JSON"))
    return args


def _fetch_args(url: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {
        "url": url,
        "format": kwargs.get("format") or _env("FUSE_BROWSER_FETCH_FORMAT") or "markdown",
        "countryCode": _env("FUSE_BROWSER_COUNTRY") or _env("FUSE_BROWSER_COUNTRY_CODE") or "CH",
    }
    proxy = _env("FUSE_BROWSER_PROXY")
    if proxy:
        args["proxyUrl"] = proxy
    max_chars = _env_int("FUSE_BROWSER_MAX_CHARS") or _env_int("FUSE_BROWSER_FETCH_MAX_CHARS")
    if max_chars is not None:
        args["maxChars"] = max_chars
    if _env_bool_optional("FUSE_BROWSER_EXTRACT_PRICES") is not None:
        args["extractPrices"] = _env_bool("FUSE_BROWSER_EXTRACT_PRICES")
    if _env_bool_optional("FUSE_BROWSER_EXTRACT_CONTACTS") is not None:
        args["extractContacts"] = _env_bool("FUSE_BROWSER_EXTRACT_CONTACTS")
    contact_filter = _env("FUSE_BROWSER_CONTACT_FILTER")
    if contact_filter:
        args["contactFilter"] = contact_filter
    args.update(_env_json_obj("FUSE_BROWSER_FETCH_OPTIONS_JSON"))
    return {k: v for k, v in args.items() if v is not None and v != ""}


def _probe_args(url: str) -> dict[str, Any]:
    args = _agent_options()
    args["url"] = url
    for env_name, key in (
        ("FUSE_BROWSER_AUTO_CONSENT", "autoConsent"),
        ("FUSE_BROWSER_EXTRACT_PRICES", "extractPrices"),
        ("FUSE_BROWSER_DETECT_CHALLENGES", "detectChallenges"),
        ("FUSE_BROWSER_OBSERVE_VISUAL", "observeVisual"),
        ("FUSE_BROWSER_HUMAN_APPROVED", "humanApproved"),
        ("FUSE_BROWSER_SOLVE_CAPTCHA", "solveCaptcha"),
        ("FUSE_BROWSER_EXTRACT_SERP", "extractSerp"),
        ("FUSE_BROWSER_EXTRACT_CONTACTS", "extractContacts"),
    ):
        _put_bool(args, key, env_name)
    for env_name, key in (
        ("FUSE_BROWSER_WAIT_MS", "waitMs"),
        ("FUSE_BROWSER_SERP_PAGES", "serpPages"),
    ):
        _put_int(args, key, env_name)
    _put_str(args, "rankDomain", "FUSE_BROWSER_RANK_DOMAIN")
    _put_str(args, "contactFilter", "FUSE_BROWSER_CONTACT_FILTER")
    for env_name, key in (
        ("FUSE_BROWSER_ACTIONS_JSON", "actions"),
        ("FUSE_BROWSER_CONTACT_CRAWL_JSON", "contactCrawl"),
    ):
        value = _env_json_obj(env_name)
        if value:
            args[key] = value
    args.update(_env_json_obj("FUSE_BROWSER_PROBE_OPTIONS_JSON"))
    return {k: v for k, v in args.items() if v is not None and v != ""}


def _title_from_markdown(text: str, fallback: str) -> str:
    if text.startswith("---"):
        for line in text.splitlines()[:10]:
            if line.startswith("title:"):
                return line.split(":", 1)[1].strip().strip('"') or fallback
    return fallback


# ─── Provider ────────────────────────────────────────────────────────────────

class FuseBrowserWebProvider(WebSearchProvider):
    """Fuse Browser MCP-backed web provider."""

    @property
    def name(self) -> str:
        return "fuse-browser"

    @property
    def display_name(self) -> str:
        return "Fuse Browser"

    def is_available(self) -> bool:
        mcp_override = _env("FUSE_BROWSER_MCP_CMD")
        if mcp_override:
            return bool(shutil.which(_split_cmd(mcp_override)[0]))
        cli_override = _env("FUSE_BROWSER_CLI")
        if cli_override:
            return bool(shutil.which(_split_cmd(cli_override)[0]))
        return bool(shutil.which("npx"))

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        if not self.is_available():
            return {"success": False, "error": "Fuse Browser MCP unavailable: npx/browser-mcp not found"}

        safe_limit = max(1, min(int(limit or 5), 100))
        pages = max(1, min(5, math.ceil(safe_limit / 10)))
        timeout = int(_env("FUSE_BROWSER_SEARCH_TIMEOUT") or "180")

        try:
            payload = _mcp_call("browser_serp_batch", _search_args(query, safe_limit, pages), timeout=timeout)
            rows = payload.get("rows") if isinstance(payload, dict) else None
            raw_results: list[dict[str, Any]] = []
            if isinstance(rows, list) and rows:
                raw_results = rows[0].get("results") or []
            web = []
            for i, hit in enumerate(raw_results[:safe_limit]):
                web.append(
                    {
                        "title": str(hit.get("title") or ""),
                        "url": str(hit.get("url") or ""),
                        "description": str(hit.get("snippet") or hit.get("description") or ""),
                        "position": int(hit.get("position") or (i + 1)),
                    }
                )
            return {
                "success": True,
                "data": {"web": web},
                "metadata": {
                    "provider": self.name,
                    "transport": "mcp",
                    "tool": "browser_serp_batch",
                    "pages": pages,
                    "hl": _env("FUSE_BROWSER_HL") or "fr",
                    "gl": _env("FUSE_BROWSER_GL") or "ch",
                },
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fuse Browser MCP search error: %s", exc)
            if _env_bool("FUSE_BROWSER_DISABLE_CLI_FALLBACK"):
                return {"success": False, "error": f"Fuse Browser MCP search error: {exc}"}
            return self._search_cli_fallback(query, safe_limit, pages, timeout, str(exc))

    def _search_cli_fallback(self, query: str, safe_limit: int, pages: int, timeout: int, reason: str) -> Dict[str, Any]:
        args = [
            "serp-batch",
            query,
            "--serp-pages",
            str(pages),
            "--hl",
            _env("FUSE_BROWSER_HL") or "fr",
            "--gl",
            _env("FUSE_BROWSER_GL") or "ch",
            "--country",
            _env("FUSE_BROWSER_COUNTRY") or "CH",
            "--engine",
            _env("FUSE_BROWSER_ENGINE") or "patchright",
            "--delay-ms",
            _env("FUSE_BROWSER_SERP_DELAY_MS") or "800",
        ]
        if _env_bool("FUSE_BROWSER_HEADED"):
            args.append("--headed")
        _append_env_opt(args, "--proxy", "FUSE_BROWSER_PROXY")
        _append_env_opt(args, "--output-dir", "FUSE_BROWSER_OUTPUT_DIR")
        _append_env_opt(args, "--rank-domain", "FUSE_BROWSER_RANK_DOMAIN")
        try:
            code, stdout, stderr = _run_fuse_cli(args, timeout=timeout)
            if code != 0:
                return {"success": False, "error": f"Fuse Browser search failed: {stderr.strip() or stdout.strip()}"}
            rows = json.loads(stdout)
            raw_results = rows[0].get("results") if isinstance(rows, list) and rows else []
            web = [
                {
                    "title": str(hit.get("title") or ""),
                    "url": str(hit.get("url") or ""),
                    "description": str(hit.get("snippet") or hit.get("description") or ""),
                    "position": int(hit.get("position") or (i + 1)),
                }
                for i, hit in enumerate((raw_results or [])[:safe_limit])
            ]
            return {
                "success": True,
                "data": {"web": web},
                "metadata": {"provider": self.name, "transport": "cli-fallback", "fallback_reason": reason},
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"Fuse Browser search error: {exc}; MCP error was: {reason}"}

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        if not self.is_available():
            return [
                {"url": url, "title": "", "content": "", "raw_content": "", "error": "Fuse Browser MCP unavailable: npx/browser-mcp not found"}
                for url in urls
            ]

        results: list[dict[str, Any]] = []
        timeout = int(_env("FUSE_BROWSER_FETCH_TIMEOUT") or "120")
        tool = "browser_probe" if (_env("FUSE_BROWSER_EXTRACT_TOOL") or "fetch").lower() == "probe" else "browser_fetch"

        for url in urls:
            try:
                if tool == "browser_probe":
                    payload = _mcp_call("browser_probe", _probe_args(url), timeout=timeout)
                    text = str(payload.get("text") or "")
                    final_url = str(payload.get("url") or url)
                    title = str(payload.get("title") or final_url)
                    metadata = {
                        "provider": self.name,
                        "transport": "mcp",
                        "tool": "browser_probe",
                        "screenshotPath": payload.get("screenshotPath"),
                        "reportPath": payload.get("reportPath"),
                        "challenges": payload.get("challenges"),
                        "visual": payload.get("visual"),
                    }
                else:
                    payload = _mcp_call("browser_fetch", _fetch_args(url, kwargs), timeout=timeout)
                    text = str(payload.get("text") or "")
                    final_url = str(payload.get("url") or url)
                    title = _title_from_markdown(text, final_url)
                    metadata = {
                        "provider": self.name,
                        "transport": "mcp",
                        "tool": "browser_fetch",
                        "status": payload.get("status"),
                        "format": payload.get("format"),
                        "prices": payload.get("prices"),
                        "contacts": payload.get("contacts"),
                    }
                results.append({"url": final_url, "title": title, "content": text, "raw_content": text, "metadata": metadata})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Fuse Browser MCP extract error for %s: %s", url, exc)
                if _env_bool("FUSE_BROWSER_DISABLE_CLI_FALLBACK") or tool == "browser_probe":
                    results.append({"url": url, "title": "", "content": "", "raw_content": "", "error": f"Fuse Browser MCP extract error: {exc}"})
                    continue
                results.append(self._extract_cli_fallback(url, timeout, str(exc)))
        return results

    def _extract_cli_fallback(self, url: str, timeout: int, reason: str) -> dict[str, Any]:
        try:
            args = ["fetch", url, "--country", _env("FUSE_BROWSER_COUNTRY") or "CH"]
            _append_env_opt(args, "--proxy", "FUSE_BROWSER_PROXY")
            if _env_bool("FUSE_BROWSER_EXTRACT_PRICES"):
                args.append("--extract-prices")
            code, stdout, stderr = _run_fuse_cli(args, timeout=timeout)
            if code != 0:
                return {"url": url, "title": "", "content": "", "raw_content": "", "error": stderr.strip() or stdout.strip()}
            payload = json.loads(stdout)
            text = str(payload.get("text") or "")
            final_url = str(payload.get("url") or url)
            return {
                "url": final_url,
                "title": _title_from_markdown(text, final_url),
                "content": text,
                "raw_content": text,
                "metadata": {"provider": self.name, "transport": "cli-fallback", "fallback_reason": reason, "status": payload.get("status"), "format": payload.get("format")},
            }
        except Exception as exc:  # noqa: BLE001
            return {"url": url, "title": "", "content": "", "raw_content": "", "error": f"Fuse Browser extract error: {exc}; MCP error was: {reason}"}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Fuse Browser",
            "badge": "Fusengine · MCP-backed · Google SERP · no search API key",
            "tag": "Route Hermes web_search/web_extract through the Fuse Browser MCP server; all other Fuse Browser MCP tools/options are reachable through the fuse_browser_mcp JSON passthrough router.",
            "env_vars": [
                {"key": "FUSE_BROWSER_MCP_CMD", "prompt": "Optional Fuse Browser MCP stdio command", "url": "https://github.com/fusengine/fuse-browser"},
                {"key": "FUSE_BROWSER_AGENT_OPTIONS_JSON", "prompt": "Optional MCP agentOptionShape JSON defaults", "url": "https://github.com/fusengine/fuse-browser"},
                {"key": "FUSE_BROWSER_SEARCH_OPTIONS_JSON", "prompt": "Optional browser_serp_batch JSON overrides", "url": "https://github.com/fusengine/fuse-browser"},
                {"key": "FUSE_BROWSER_FETCH_OPTIONS_JSON", "prompt": "Optional browser_fetch JSON overrides", "url": "https://github.com/fusengine/fuse-browser"},
                {"key": "FUSE_BROWSER_PROBE_OPTIONS_JSON", "prompt": "Optional browser_probe JSON overrides when FUSE_BROWSER_EXTRACT_TOOL=probe", "url": "https://github.com/fusengine/fuse-browser"},
            ],
        }
