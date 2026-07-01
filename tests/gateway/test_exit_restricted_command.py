"""Tests for /exit-restricted gateway command (issue #42824).

Verifies that the /exit-restricted slash command resets the iteration budget
on a cached agent, clearing restricted mode and restoring full tool access
for the next turn.
"""

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.iteration_budget import IterationBudget
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str = "/exit-restricted") -> MessageEvent:
    return MessageEvent(
        text=text,
        source=_make_source(),
        message_id="m1",
    )


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner._running_agents = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._is_user_authorized = lambda _source: True
    return runner


def _make_mock_agent(max_iterations: int = 90) -> MagicMock:
    """Create a minimal mock agent with iteration budget."""
    agent = MagicMock()
    agent.max_iterations = max_iterations
    agent.iteration_budget = IterationBudget(max_total=max_iterations)
    agent._budget_exhausted_injected = False
    agent._budget_grace_call = False
    return agent


@pytest.mark.asyncio
async def test_exit_restricted_resets_cached_agent_budget():
    """When an agent is cached with an exhausted budget, /exit-restricted resets it."""
    runner = _make_runner()
    source = _make_source()
    sk = build_session_key(source)

    # Create a cached agent with exhausted budget
    agent = _make_mock_agent(max_iterations=90)
    for _ in range(90):
        agent.iteration_budget.consume()
    assert agent.iteration_budget.remaining == 0

    # Set up the session store mock to return a session entry
    session_entry = SimpleNamespace(session_key=sk)
    runner.session_store.get_or_create_session.return_value = session_entry

    # Put the agent in the cache
    runner._agent_cache[sk] = (agent, "timestamp")

    # Run the handler
    result = await runner._handle_exit_restricted_command(_make_event())

    # Budget should be reset
    assert agent.iteration_budget.used == 0
    assert agent.iteration_budget.remaining == 90
    assert "✅ Restricted mode cleared" in result


@pytest.mark.asyncio
async def test_exit_restricted_resets_running_agent_budget():
    """When an agent is mid-turn, /exit-restricted resets its budget."""
    runner = _make_runner()
    source = _make_source()
    sk = build_session_key(source)

    agent = _make_mock_agent(max_iterations=90)
    for _ in range(90):
        agent.iteration_budget.consume()

    session_entry = SimpleNamespace(session_key=sk)
    runner.session_store.get_or_create_session.return_value = session_entry

    # Put the agent in running agents (mid-turn)
    runner._running_agents[sk] = agent

    result = await runner._handle_exit_restricted_command(_make_event())

    assert agent.iteration_budget.used == 0
    assert agent.iteration_budget.remaining == 90
    assert "✅ Restricted mode cleared" in result


@pytest.mark.asyncio
async def test_exit_restricted_resets_budget_flags():
    """Budget-related flags must be cleared alongside the counter."""
    runner = _make_runner()
    source = _make_source()
    sk = build_session_key(source)

    agent = _make_mock_agent()
    for _ in range(90):
        agent.iteration_budget.consume()
    agent._budget_exhausted_injected = True
    agent._budget_grace_call = True

    session_entry = SimpleNamespace(session_key=sk)
    runner.session_store.get_or_create_session.return_value = session_entry

    runner._agent_cache[sk] = (agent, "timestamp")

    await runner._handle_exit_restricted_command(_make_event())

    assert agent._budget_exhausted_injected is False
    assert agent._budget_grace_call is False
    assert agent.iteration_budget.used == 0


@pytest.mark.asyncio
async def test_exit_restricted_no_active_agent():
    """When no agent exists, return an informative message."""
    runner = _make_runner()
    source = _make_source()
    sk = build_session_key(source)

    session_entry = SimpleNamespace(session_key=sk)
    runner.session_store.get_or_create_session.return_value = session_entry

    result = await runner._handle_exit_restricted_command(_make_event())

    assert "No active agent" in result
