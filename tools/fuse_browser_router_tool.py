"""Generic Fuse Browser MCP router tool.

This tool is intentionally thin: it lets Hermes route any Fuse Browser MCP
capability through the same MCP stdio transport used by the native
fuse-browser web provider. Generic web_search/web_extract keep their small
schemas, but advanced browser operations can pass the full MCP argument object
without losing options when the generated MCP tool schema is stale or absent.
"""

from __future__ import annotations

import json
import subprocess
import threading
from typing import Any

from plugins.web.fuse_browser.provider import (
    FuseBrowserWebProvider,
    _base_mcp_cmd,
    _mcp_send,
    _read_jsonrpc,
)
from tools.registry import registry


FUSE_BROWSER_MCP_TOOLS = {
    "browser_probe",
    "browser_probe_html",
    "browser_fetch",
    "browser_serp_batch",
    "browser_open",
    "browser_status",
    "browser_close",
    "browser_connect",
    "browser_navigate",
    "browser_click",
    "browser_fill",
    "browser_scroll",
    "browser_press",
    "browser_select",
    "browser_back",
    "browser_forward",
    "browser_wait",
    "browser_login",
    "browser_snapshot",
    "browser_act",
    "browser_collect",
    "browser_wait_for",
    "browser_run",
    "browser_extract",
    "browser_extract_schema",
    "browser_screenshot",
    "browser_inspect",
    "browser_visual_diff",
    "browser_handoff",
    "browser_live_view",
    "browser_live_view_stop",
    "browser_metrics",
}


PREFIXES = (
    "mcp_fuse_browser_",
    "mcp_fuse_browser_browser_",
    "fuse_browser_",
)

_CLIENT_LOCK = threading.RLock()
_CLIENT_PROC: subprocess.Popen[str] | None = None
_CLIENT_NEXT_ID = 2


def _normalize_tool_name(tool_name: str) -> str:
    name = (tool_name or "").strip()
    for prefix in PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            if prefix.endswith("browser_") and not name.startswith("browser_"):
                name = f"browser_{name}"
            break
    return name


def _parse_mcp_tool_result(resp: dict[str, Any]) -> dict[str, Any]:
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


def _reset_client() -> None:
    global _CLIENT_PROC, _CLIENT_NEXT_ID
    if _CLIENT_PROC is not None:
        try:
            _CLIENT_PROC.terminate()
            _CLIENT_PROC.wait(timeout=5)
        except Exception:
            try:
                _CLIENT_PROC.kill()
            except Exception:
                pass
    _CLIENT_PROC = None
    _CLIENT_NEXT_ID = 2


def _ensure_client(timeout: int) -> subprocess.Popen[str]:
    global _CLIENT_PROC, _CLIENT_NEXT_ID
    if _CLIENT_PROC is not None and _CLIENT_PROC.poll() is None:
        return _CLIENT_PROC
    _reset_client()
    proc = subprocess.Popen(
        _base_mcp_cmd(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    _mcp_send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "hermes-fuse-browser-router", "version": "1.0"},
            },
        },
    )
    init = _read_jsonrpc(proc, 1, timeout)
    if "error" in init:
        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError(f"Fuse Browser MCP initialize failed: {init['error']}")
    _mcp_send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    _CLIENT_PROC = proc
    _CLIENT_NEXT_ID = 2
    return proc


def _persistent_mcp_call(tool_name: str, arguments: dict[str, Any], *, timeout: int = 180) -> dict[str, Any]:
    """Call a Fuse Browser MCP tool while keeping one stdio server alive.

    Session tools (browser_open → browser_navigate → browser_act...) only work
    if calls hit the same browser-mcp process. The web provider can use one-shot
    calls, but this all-tools router must keep the MCP server alive.
    """
    global _CLIENT_NEXT_ID
    with _CLIENT_LOCK:
        proc = _ensure_client(timeout)
        msg_id = _CLIENT_NEXT_ID
        _CLIENT_NEXT_ID += 1
        _mcp_send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
        try:
            return _parse_mcp_tool_result(_read_jsonrpc(proc, msg_id, timeout))
        except Exception:
            if proc.poll() is not None:
                _reset_client()
            raise


def fuse_browser_mcp_router(tool_name: str, arguments: dict[str, Any] | None = None, timeout: int = 180) -> str:
    """Call any Fuse Browser MCP tool with full JSON argument passthrough."""
    name = _normalize_tool_name(tool_name)
    if name not in FUSE_BROWSER_MCP_TOOLS:
        return json.dumps(
            {
                "success": False,
                "error": f"Unsupported Fuse Browser MCP tool: {tool_name}",
                "available_tools": sorted(FUSE_BROWSER_MCP_TOOLS),
            },
            ensure_ascii=False,
        )
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return json.dumps({"success": False, "error": "arguments must be a JSON object"}, ensure_ascii=False)
    try:
        payload = _persistent_mcp_call(name, arguments, timeout=max(1, min(int(timeout or 180), 600)))
        return json.dumps(
            {
                "success": True,
                "provider": "fuse-browser",
                "transport": "mcp",
                "tool": name,
                "data": payload,
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {
                "success": False,
                "provider": "fuse-browser",
                "transport": "mcp",
                "tool": name,
                "error": str(exc),
            },
            ensure_ascii=False,
        )


FUSE_BROWSER_MCP_SCHEMA = {
    "name": "fuse_browser_mcp",
    "description": (
        "Route ANY Fuse Browser MCP tool through the live browser-mcp server with full JSON argument passthrough. "
        "Use this when a web/browser task needs MCP options beyond Hermes' small web_search/web_extract schemas, "
        "or when the generated MCP tool schema is stale. Supports browser_probe/fetch/serp_batch/open/navigate/"
        "snapshot/act/run/extract/screenshot/collect/inspect/visual_diff/live_view/metrics and the rest of Fuse Browser MCP."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Exact Fuse Browser MCP tool name, e.g. browser_probe, browser_open, browser_act, browser_run, browser_collect.",
                "enum": sorted(FUSE_BROWSER_MCP_TOOLS),
            },
            "arguments": {
                "type": "object",
                "description": "Full MCP arguments object for the selected tool. Passed through unchanged.",
                "additionalProperties": True,
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait for the MCP call. Clamped to 1..600.",
                "minimum": 1,
                "maximum": 600,
                "default": 180,
            },
        },
        "required": ["tool_name"],
    },
}


registry.register(
    name="fuse_browser_mcp",
    toolset="web",
    schema=FUSE_BROWSER_MCP_SCHEMA,
    handler=lambda args, **kw: fuse_browser_mcp_router(
        tool_name=args.get("tool_name", ""),
        arguments=args.get("arguments") or {},
        timeout=args.get("timeout") or 180,
    ),
    check_fn=lambda: FuseBrowserWebProvider().is_available(),
    emoji="🌐",
    max_result_size_chars=100_000,
)
