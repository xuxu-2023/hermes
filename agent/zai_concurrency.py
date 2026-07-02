"""Process-local concurrency gate for Z.AI / GLM model calls.

Why this exists
---------------
Z.AI / Zhipu Coding Plan (``api.z.ai``, model family ``glm-*``) returns
``HTTP 429 / code 1305`` ("service may be temporarily overloaded") under
concurrent load. Part of that is genuine provider-side saturation, and part
of it is a prompt-level block (#47685 / #53002) addressed separately by the
prompt-sanitization work. But even with a clean prompt, a *swarm* of
subagents fanned out via ``delegate_task`` opens one long-lived streaming
request per child, all hitting the same endpoint at once. Each rejected
request then retries (up to ``maxAttempts``), and because the retries are
decorrelated only by jitter, the in-flight count never actually drops — the
retries keep the concurrency ceiling pegged and the 429s persist.

This module bounds the number of *simultaneous in-flight model calls* to the
Z.AI endpoint within a single Hermes process. Subagents run as threads in a
``ThreadPoolExecutor`` (``tools/delegate_tool.py``), so a process-local
``threading`` semaphore is the correct and sufficient coordination primitive
for the in-process swarm case. The gate is **Z.AI-only**: every other
provider passes straight through with no added latency, no allocation, and
no behavior change.

Scope and limitations (stated up front)
---------------------------------------
- **Process-local.** The semaphore coordinates sibling agents within one
  Hermes process (the common swarm case). Two independently launched CLI
  processes each get their own gate; a cross-process limiter is out of
  scope here and is the domain of #7479's broader provider-concurrency
  work. The two are complementary, not overlapping.
- **Opt-in by host.** Activation is keyed on the resolved base URL and
  provider/model, so this never touches a non-Z.AI request path.
- **Non-fatal.** If the gate cannot be acquired within the configured
  window, the call proceeds anyway — a best-effort throttle, never a new
  way to hang the agent.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from utils import env_int, env_float

logger = logging.getLogger(__name__)

# Tunable knob. Kept conservative (4) so a typical parent + up to three
# concurrent subagents can still proceed in parallel, but a larger fan-out
# (e.g. an orchestrator role dispatching many children) no longer drives the
# in-flight count unbounded against an endpoint that rejects under load.
# 0 disables the gate entirely (pass-through).
_ZAI_MAX_CONCURRENT = max(0, env_int("HERMES_ZAI_MAX_CONCURRENT", 4))

# How long to wait for a slot before giving up and just proceeding. Kept
# short so the gate degrades to a no-op (not a hang) if the window is
# starved, and so a stalled stream holding a slot does not block its
# siblings indefinitely. 0 means never wait (non-blocking probe). Parsed as
# a float (the var name ends in _S / seconds and the value is passed to
# sem.acquire(timeout=...)) so a fractional tuning like 0.5 is honored
# instead of silently snapping to the default (env_int can't parse "0.5").
_ZAI_ACQUIRE_TIMEOUT = max(0.0, env_float("HERMES_ZAI_ACQUIRE_TIMEOUT_S", 30.0))

# Hosts that resolve to the Z.AI / Zhipu backend (coding-plan + generic +
# China mirror). Matches the detection logic in ``agent/model_metadata.py``
# and ``agent/retry_utils.py``.
_ZAI_HOST_MARKERS = ("api.z.ai", "bigmodel.cn")


class _ZaiConcurrencyGate:
    """Lazy, thread-safe holder for the single process-wide Z.AI semaphore."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sem: Optional[threading.BoundedSemaphore] = None
        self._configured_max = _ZAI_MAX_CONCURRENT

    def _semaphore(self) -> Optional[threading.BoundedSemaphore]:
        if self._configured_max <= 0:
            return None
        if self._sem is None:
            with self._lock:
                if self._sem is None:
                    self._sem = threading.BoundedSemaphore(self._configured_max)
        return self._sem

    def is_zai(self, *, provider: Any, model: Any, base_url: Any) -> bool:
        """Return True when this request targets the Z.AI / Zhipu backend.

        Conservative: a request is treated as Z.AI only when the base URL or
        provider string carries a Z.AI marker. This mirrors the detection
        used by ``is_zai_coding_overload_error`` so the gate and the
        overload-aware backoff agree on what counts as Z.AI.
        """
        host = str(base_url or "").lower()
        if any(marker in host for marker in _ZAI_HOST_MARKERS):
            return True
        prov = str(provider or "").lower()
        if prov in {"zai", "zhipu", "glm", "z-ai", "z.ai"}:
            return True
        mdl = str(model or "").lower()
        # A glm-* model name is a strong signal only when paired with a
        # Z.AI-looking host or provider; on its own it could be a local or
        # proxied GLM, so we don't gate on the bare model prefix.
        return False


# Single process-wide instance. Subagent threads share it.
_gate = _ZaiConcurrencyGate()


def is_zai_request(*, provider: Any, model: Any, base_url: Any) -> bool:
    """Public detector: does this model call target Z.AI / Zhipu?"""
    return _gate.is_zai(provider=provider, model=model, base_url=base_url)


def acquire_zai_slot(
    *, provider: Any, model: Any, base_url: Any
) -> "_SlotHandle":
    """Acquire a Z.AI concurrency slot, or return a no-op handle.

    Returns a context-manager-like handle whose ``__enter__`` is the acquire
    point and whose ``__exit__`` releases the slot (if one was taken).
    Non-Z.AI requests — and any Z.AI request when the gate is disabled
    (``HERMES_ZAI_MAX_CONCURRENT=0``) — get a pass-through handle that does
    nothing, so the call path is unchanged for everyone else.
    """
    if not is_zai_request(provider=provider, model=model, base_url=base_url):
        return _NoopHandle()
    sem = _gate._semaphore()
    if sem is None:
        return _NoopHandle()

    acquired = False
    try:
        if _ZAI_ACQUIRE_TIMEOUT > 0:
            acquired = sem.acquire(timeout=_ZAI_ACQUIRE_TIMEOUT)
        else:
            acquired = sem.acquire(blocking=False)
    except Exception:  # pragma: no cover - defensive; never block the call
        acquired = False

    if not acquired:
        # Gate starved: proceed rather than hanging the agent. A short log
        # so a saturation episode is observable without being noisy.
        logger.debug(
            "zai-concurrency-gate: no slot within %.1fs; proceeding uncapped",
            _ZAI_ACQUIRE_TIMEOUT,
        )
        return _NoopHandle()
    return _SlotHandle(sem)


class _SlotHandle:
    """Holds one acquired semaphore slot; releases it on exit."""

    __slots__ = ("_sem",)

    def __init__(self, sem: "threading.BoundedSemaphore") -> None:
        self._sem = sem

    def __enter__(self) -> "_SlotHandle":
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self._sem.release()
        except ValueError:
            # BoundedSemaphore raises if release() over-counts — e.g. a
            # double release path. Safe to ignore; the slot is already free.
            pass
        except Exception:  # pragma: no cover - defensive
            pass


class _NoopHandle:
    """Pass-through handle for non-Z.AI requests / disabled gate."""

    __slots__ = ()

    def __enter__(self) -> "_NoopHandle":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


# Test/inspection helpers (not used on the hot path).
def configured_max_concurrent() -> int:
    """The configured Z.AI in-flight cap (0 = disabled)."""
    return _ZAI_MAX_CONCURRENT


def _reset_for_tests(max_concurrent: int, timeout: float) -> None:
    """Rebuild the gate with explicit settings; tests only."""
    global _ZAI_MAX_CONCURRENT, _ZAI_ACQUIRE_TIMEOUT, _gate
    _ZAI_MAX_CONCURRENT = max(0, max_concurrent)
    _ZAI_ACQUIRE_TIMEOUT = max(0.0, timeout)
    _gate = _ZaiConcurrencyGate()
