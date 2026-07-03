# Perseus context engine plugin for Hermes Agent.
#
# Perseus is an MIT-licensed live context engine for AI agents.
# Instead of baking static instructions into prompts, Perseus resolves
# directives at inference time — @file, @memory, @search, @query, @agent,
# @tool, @skills, and 20+ more. This plugin makes Perseus a first-class
# context engine in Hermes, complementing the built-in ContextCompressor.
#
# When active (context.engine: perseus in config.yaml), it:
#   1. Resolves Perseus directives on session start, injecting live
#      context (e.g. @file AGENTS.md) into the agent's awareness.
#   2. Delegates compression to the built-in ContextCompressor.
#   3. Optionally exposes Perseus MCP tools to the agent.

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

from agent.context_engine import ContextEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public register() entry point — required for plugin discovery
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register the Perseus context engine via the plugin system."""
    engine = PerseusEngine()
    ctx.register_context_engine(engine)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PerseusEngine(ContextEngine):
    """Context engine that adds live Perseus directive resolution.

    Compression is delegated to the built-in ContextCompressor.  Perseus
    adds live context resolution at session boundaries — pulling in
    workspace files, project memory, git state, and service health on
    every session start via @file, @memory, @query, and 20+ other
    directives.

    Requires ``perseus`` CLI on PATH (``pip install perseus-ctx``).
    """

    def __init__(self) -> None:
        self._compressor = None  # Lazy — ContextCompressor needs model info
        self._perseus_available: Optional[bool] = None
        self._directives: List[str] = []
        self._resolved_count: int = 0

        # ContextEngine state (required by ABC / run_agent.py)
        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0
        self.last_total_tokens: int = 0
        self.threshold_tokens: int = 0
        self.context_length: int = 200_000
        self.compression_count: int = 0

        self.threshold_percent: float = 0.75
        self.protect_first_n: int = 3
        self.protect_last_n: int = 6

        self._workspace_dir: str = ""

    # -- Identity ----------------------------------------------------------

    @property
    def name(self) -> str:
        return "perseus"

    # -- Availability ------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """True when the ``perseus`` CLI is on PATH."""
        try:
            subprocess.run(
                ["perseus", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            return True
        except Exception:
            return False

    # -- Model / config ----------------------------------------------------

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        api_mode: str = "",
    ) -> None:
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)
        if self._compressor is not None:
            self._compressor.update_model(
                model, context_length, base_url, api_key, provider, api_mode,
            )

    # -- Token tracking ----------------------------------------------------

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_total_tokens = usage.get("total_tokens", 0)
        if self._compressor is not None:
            self._compressor.update_from_response(usage)
            # Sync state back
            self.last_prompt_tokens = self._compressor.last_prompt_tokens
            self.last_total_tokens = self._compressor.last_total_tokens

    # -- Compression (delegated) -------------------------------------------

    def should_compress(self, prompt_tokens: int = None) -> bool:
        if self._compressor is None:
            self._ensure_compressor()
        if self._compressor is not None:
            return self._compressor.should_compress(prompt_tokens)
        tokens = (
            prompt_tokens
            if prompt_tokens is not None
            else self.last_prompt_tokens
        )
        return tokens >= self.threshold_tokens

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> List[Dict[str, Any]]:
        if self._compressor is None:
            self._ensure_compressor()
        if self._compressor is not None:
            result = self._compressor.compress(messages, current_tokens, focus_topic)
            self.compression_count = self._compressor.compression_count
            return result
        return messages

    def should_compress_preflight(
        self, messages: List[Dict[str, Any]]
    ) -> bool:
        if self._compressor is not None:
            return self._compressor.should_compress_preflight(messages)
        return False

    def has_content_to_compress(
        self, messages: List[Dict[str, Any]]
    ) -> bool:
        if self._compressor is not None:
            return self._compressor.has_content_to_compress(messages)
        return True

    # -- Session lifecycle -------------------------------------------------

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Resolve Perseus directives at session start.

        When a workspace has an AGENTS.md with @file / @query / @memory
        directives, they are resolved and the result is available to the
        agent for the remainder of the session via tools.
        """
        self._resolved_count = 0
        self._directives = []

        # Determine workspace directory
        hermes_home = str(kwargs.get("hermes_home", ""))
        cwd = os.getenv("TERMINAL_CWD") or os.getcwd()
        self._workspace_dir = cwd

        if not self.is_available():
            return

        # Resolve AGENTS.md directives if present
        try:
            self._resolve_directives(self._workspace_dir)
        except Exception:
            logger.debug("Perseus directive resolution failed", exc_info=True)

        # Start the underlying compressor for this session
        if self._compressor is None:
            self._ensure_compressor()
        if self._compressor is not None:
            self._compressor.on_session_start(session_id, **kwargs)

    def on_session_end(
        self, session_id: str, messages: List[Dict[str, Any]]
    ) -> None:
        if self._compressor is not None:
            self._compressor.on_session_end(session_id, messages)

    def on_session_reset(self) -> None:
        super().on_session_reset()
        self._resolved_count = 0
        self._directives = []
        if self._compressor is not None:
            self._compressor.on_session_reset()

    # -- Tools -------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Expose Perseus tools the agent can call.

        These tools let the agent resolve Perseus directives on demand:
        @file, @memory, @search, @query, @agent, @tool, @skills, etc.
        """
        if not self.is_available():
            return []

        return [
            {
                "name": "perseus_render",
                "description": (
                    "Render a Perseus source file containing @directives "
                    "and return the resolved output. Perseus resolves "
                    "@file (read a file), @memory (query Mneme project "
                    "memory), @search (search the workspace), @query (run "
                    "a Python expression), @agent (invoke an agent), "
                    "@tool (call an MCP tool), @skills (list skills), "
                    "@git (git state), @services (service health), and "
                    "20+ more directives. Use this to get live, resolved "
                    "context from a Perseus-enabled workspace."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": (
                                "Path to a Perseus source file (e.g. "
                                "AGENTS.md, .perseus/context.md) or a "
                                "raw directive string like '@file README.md'"
                            ),
                        },
                        "workspace": {
                            "type": "string",
                            "description": (
                                "Optional workspace directory. Defaults to "
                                "the current session working directory."
                            ),
                        },
                    },
                    "required": ["source"],
                },
            },
            {
                "name": "perseus_list",
                "description": (
                    "List available Perseus commands and their descriptions. "
                    "Use this to discover what context resolution "
                    "capabilities are available."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        if name == "perseus_render":
            source = str(args.get("source", "")).strip()
            workspace = str(args.get("workspace", self._workspace_dir))
            if not source:
                return json.dumps({"error": "source is required"})
            try:
                result = self._run_perseus_render(source, cwd=workspace)
                return json.dumps({"source": source, "result": result})
            except Exception as e:
                return json.dumps({"error": str(e), "source": source})

        if name == "perseus_list":
            try:
                result = self._run_perseus(["--help"], cwd=self._workspace_dir)
                return json.dumps({"commands": result})
            except Exception as e:
                return json.dumps({"error": str(e)})

        # Fall back to compressor tools if any
        if self._compressor is not None:
            return self._compressor.handle_tool_call(name, args, **kwargs)

        return json.dumps({"error": f"Unknown tool: {name}"})

    # -- Status ------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status["engine"] = "perseus"
        status["perseus_available"] = self.is_available()
        status["resolved_directives"] = self._resolved_count
        return status

    # -- Internals ---------------------------------------------------------

    def _ensure_compressor(self) -> None:
        """Lazy-init the built-in ContextCompressor."""
        try:
            from agent.context_compressor import ContextCompressor

            self._compressor = ContextCompressor(
                model="gpt-4",  # placeholder — update_model() called next
                quiet_mode=True,
                config_context_length=self.context_length,
            )
        except Exception:
            logger.debug("Failed to create ContextCompressor fallback", exc_info=True)

    def _resolve_directives(self, workspace: str) -> None:
        """Resolve @file / @memory / etc. by rendering AGENTS.md if present."""
        agents_path = os.path.join(workspace, "AGENTS.md")
        if not os.path.isfile(agents_path):
            return

        try:
            result = self._run_perseus_render(agents_path, cwd=workspace)
            if result:
                self._directives.append(f"@file AGENTS.md")
                self._resolved_count += 1
        except Exception:
            logger.debug("Failed to render AGENTS.md with Perseus", exc_info=True)

    @staticmethod
    def _run_perseus_render(source: str, cwd: str = "") -> str:
        """Render a Perseus source file or directive string.

        If ``source`` looks like a file path (contains a path separator
        or ends in .md/.perseus), render it as a file.  Otherwise treat
        it as a single directive and render via stdin.
        """
        if os.path.sep in source or source.endswith((".md", ".perseus")):
            cmd = ["perseus", "render", source]
        else:
            # Single directive — render via stdin
            cmd = ["perseus", "render"]

        proc = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=30,
            cwd=cwd or None,
            input=source if cmd[-1] == "render" else None,
        )
        if proc.returncode != 0:
            error = proc.stderr.strip() or f"exit code {proc.returncode}"
            raise RuntimeError(error)
        return proc.stdout.strip()

    @staticmethod
    def _run_perseus(args: List[str], cwd: str = "") -> str:
        """Run a perseus CLI command and return stdout."""
        cmd = ["perseus"] + args
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=30,
            cwd=cwd or None,
        )
        if proc.returncode != 0:
            error = proc.stderr.strip() or f"exit code {proc.returncode}"
            raise RuntimeError(error)
        return proc.stdout.strip()
