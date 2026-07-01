"""Tests for tui_gateway.ws — WebSocket transport write timeout."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestWSWriteTimeout:
    """Verify the write timeout constant and timeout-triggered closure."""

    def test_timeout_constant_is_30s(self):
        """_WS_WRITE_TIMEOUT_S must be 30 s — 10 s was too aggressive on Windows."""
        from tui_gateway.ws import _WS_WRITE_TIMEOUT_S

        assert _WS_WRITE_TIMEOUT_S == 30.0

    def test_write_marks_transport_closed_on_timeout(self):
        """A write that times out must close the transport so subsequent writes fail."""
        from tui_gateway.ws import WSTransport

        mock_ws = MagicMock()
        loop = asyncio.new_event_loop()
        try:
            transport = WSTransport(mock_ws, loop, peer="test:1234")
            assert not transport._closed

            # Simulate: get_running_loop returns a DIFFERENT loop → off-loop path
            other_loop = asyncio.new_event_loop()
            try:
                with (
                    patch("asyncio.get_running_loop", return_value=other_loop),
                    patch(
                        "agent.async_utils.safe_schedule_threadsafe",
                        return_value=None,
                    ),
                ):
                    result = transport.write({"method": "test"})
            finally:
                other_loop.close()

            assert result is False
            assert transport._closed is True
        finally:
            loop.close()

    def test_write_returns_false_after_closed(self):
        """Once closed, write() returns False without attempting I/O."""
        from tui_gateway.ws import WSTransport

        mock_ws = MagicMock()
        loop = asyncio.new_event_loop()
        try:
            transport = WSTransport(mock_ws, loop, peer="test:1234")
            transport._closed = True
            assert transport.write({"method": "test"}) is False
        finally:
            loop.close()
