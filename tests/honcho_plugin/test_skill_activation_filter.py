"""Tests for _flush_session skill-activation filtering in HonchoSessionManager."""

from unittest.mock import MagicMock, patch

from plugins.memory.honcho.session import (
    HonchoSession,
    HonchoSessionManager,
    _FRAMEWORK_INJECTED_PREFIXES,
)


class TestFlushSessionSkillFilter:
    """Verify that framework-injected skill activation messages are excluded
    from the Honcho sync pipeline in _flush_session."""

    def _make_manager_and_session(self):
        """Create a manager with mocked Honcho client dependencies."""
        mgr = HonchoSessionManager()
        session = HonchoSession(
            key="test:filter",
            user_peer_id="user-1",
            assistant_peer_id="assistant-1",
            honcho_session_id="session-1",
        )
        # Stub out Honcho client calls
        mgr._get_or_create_peer = MagicMock(side_effect=lambda pid: MagicMock())
        mock_honcho_session = MagicMock()
        mgr._get_or_create_honcho_session = MagicMock(
            return_value=(mock_honcho_session, False)
        )
        mgr._sessions_cache[session.honcho_session_id] = mock_honcho_session
        return mgr, session, mock_honcho_session

    def test_skill_activation_message_is_not_synced(self):
        """A user message starting with a known skill-injection prefix is
        marked synced without being sent to Honcho."""
        mgr, session, mock_honcho = self._make_manager_and_session()

        skill_msg = (
            '[IMPORTANT: The user has invoked the "my-skill" skill, '
            "indicating they want you to follow its instructions. "
            "The full skill content is loaded below.]\n\n# Skill Content\n..."
        )
        session.add_message("user", skill_msg)

        result = mgr._flush_session(session)

        assert result is True
        # Message was marked synced locally
        assert session.messages[0]["_synced"] is True
        # But was NOT sent to Honcho
        mock_honcho.add_messages.assert_not_called()

    def test_regular_user_message_is_synced_normally(self):
        """A normal user message (no skill prefix) is synced as before."""
        mgr, session, mock_honcho = self._make_manager_and_session()

        session.add_message("user", "Hello, what's the weather?")
        session.add_message("assistant", "It's sunny.")

        result = mgr._flush_session(session)

        assert result is True
        # Two messages sent to Honcho
        assert mock_honcho.add_messages.call_count == 1
        synced_args = mock_honcho.add_messages.call_args[0][0]
        assert len(synced_args) == 2

    def test_mixed_skill_and_regular_messages(self):
        """Skill messages are filtered; regular messages pass through."""
        mgr, session, mock_honcho = self._make_manager_and_session()

        session.add_message("user", "Hello")
        session.add_message("user", '[IMPORTANT: The user has invoked the "test" skill, ...]')
        session.add_message("assistant", "Sure!")
        session.add_message("user", "What about task B?")

        result = mgr._flush_session(session)

        assert result is True
        synced_args = mock_honcho.add_messages.call_args[0][0]
        # Only 3 messages synced (skill message filtered out)
        assert len(synced_args) == 3
        # Skill message is marked synced
        assert session.messages[1]["_synced"] is True

    def test_all_known_prefixes_are_filtered(self):
        """Every prefix in _FRAMEWORK_INJECTED_PREFIXES is recognized."""
        mgr, session, mock_honcho = self._make_manager_and_session()

        for prefix in _FRAMEWORK_INJECTED_PREFIXES:
            session.add_message("user", prefix + " rest of content")

        result = mgr._flush_session(session)

        assert result is True
        # All filtered, nothing sent to Honcho
        mock_honcho.add_messages.assert_not_called()
        for msg in session.messages:
            assert msg["_synced"] is True

    def test_skill_message_not_retried_on_failure(self):
        """When a subsequent Honcho sync fails, previously filtered skill
        messages should NOT be reset to _synced=False."""
        mgr, session, mock_honcho = self._make_manager_and_session()

        session.add_message("user", '[IMPORTANT: The user has invoked the "x" skill, ...]')
        session.add_message("user", "Real message")
        session.add_message("assistant", "Reply")

        # First flush succeeds
        mgr._flush_session(session)
        # Skill message should be synced (filtered)
        assert session.messages[0]["_synced"] is True

        # Add a new message that will fail
        session.add_message("user", "Another message")
        session.messages[-1]["_synced"] = False
        mock_honcho.add_messages.side_effect = RuntimeError("API down")

        mgr._flush_session(session)

        # Skill message should STILL be synced (not retried)
        assert session.messages[0]["_synced"] is True
        # The new message should be reset for retry
        assert session.messages[-1]["_synced"] is False
