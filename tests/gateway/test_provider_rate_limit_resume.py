import asyncio
import time
from typing import Any

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.run import (
    GatewayRunner,
    _coerce_provider_rate_limit_reset_at,
    _is_auto_continue_noise,
    _provider_rate_limit_reset_at,
    _provider_rate_limit_resume_message,
    _strip_auto_continue_noise,
)
from gateway.session import SessionSource


class _Adapter:
    def __init__(self):
        self.events = []

    async def handle_message(self, event: MessageEvent):
        self.events.append(event)


def _runner(adapter: _Adapter) -> GatewayRunner:
    runner: Any = object.__new__(GatewayRunner)
    runner.adapters = {Platform.SLACK: adapter}
    runner._background_tasks = set()
    runner._provider_rate_limit_resume_tasks = {}
    runner._session_run_generation = {"s1": 7}
    runner._running_agents = {}
    runner._is_user_authorized = lambda source: True
    return runner


def test_provider_limit_reset_time_requires_future_timestamp():
    assert _coerce_provider_rate_limit_reset_at(1200, now=1000) == 1200
    assert (
        _coerce_provider_rate_limit_reset_at(
            "1970-01-01T00:20:00+00:00", now=1000
        )
        == 1200
    )
    assert _coerce_provider_rate_limit_reset_at(900, now=1000) is None
    assert _coerce_provider_rate_limit_reset_at("not-a-time", now=1000) is None


def test_provider_limit_reset_only_uses_rate_limit_results():
    future_reset = time.time() + 120
    assert (
        _provider_rate_limit_reset_at(
            {"failure_reason": "billing", "error_context": {"reset_at": future_reset}}
        )
        is None
    )
    assert (
        _provider_rate_limit_reset_at(
            {"failure_reason": "rate_limit", "error_context": {}}
        )
        is None
    )
    assert (
        _provider_rate_limit_reset_at(
            {
                "failure_reason": "rate_limit",
                "error_context": {"reset_at": future_reset},
            }
        )
        == future_reset
    )


def test_provider_limit_resume_note_is_not_replayed_as_user_history():
    note = _provider_rate_limit_resume_message()
    assert _is_auto_continue_noise(note)
    assert _strip_auto_continue_noise(note) == ""


@pytest.mark.asyncio
async def test_provider_limit_resume_dispatches_internal_continuation():
    adapter = _Adapter()
    runner = _runner(adapter)
    source = SessionSource(platform=Platform.SLACK, chat_id="C1", user_id="U1")

    await runner._run_provider_rate_limit_resume_after_delay(
        session_key="s1",
        source=source,
        reset_at=0,
        run_generation=7,
    )

    assert len(adapter.events) == 1
    event = adapter.events[0]
    assert event.internal is True
    assert event.source is source
    assert _is_auto_continue_noise(event.text)


@pytest.mark.asyncio
async def test_provider_limit_resume_skips_when_newer_turn_exists():
    adapter = _Adapter()
    runner = _runner(adapter)
    source = SessionSource(platform=Platform.SLACK, chat_id="C1", user_id="U1")
    runner._session_run_generation["s1"] = 8

    await runner._run_provider_rate_limit_resume_after_delay(
        session_key="s1",
        source=source,
        reset_at=0,
        run_generation=7,
    )

    assert adapter.events == []


@pytest.mark.asyncio
async def test_provider_limit_resume_scheduler_is_in_memory_only():
    adapter = _Adapter()
    runner = _runner(adapter)
    source = SessionSource(platform=Platform.SLACK, chat_id="C1", user_id="U1")

    scheduled = runner._schedule_provider_rate_limit_resume(
        session_key="s1",
        source=source,
        reset_at=time.time() + 10_000,
        run_generation=7,
    )

    assert scheduled is True
    assert "s1" in runner._provider_rate_limit_resume_tasks
    task = runner._provider_rate_limit_resume_tasks["s1"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task