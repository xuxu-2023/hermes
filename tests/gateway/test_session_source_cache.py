from collections import OrderedDict

from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_session_key


def _runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner._session_sources = OrderedDict()
    runner._session_sources_max = 512
    return runner


def _source(thread_id: str | None, message_id: str = "msg-1") -> SessionSource:
    return SessionSource(
        platform=Platform.MATTERMOST,
        chat_id="channel-1",
        chat_type="channel",
        user_id="user-1",
        thread_id=thread_id,
        message_id=message_id,
    )


def _telegram_topic_source(thread_id: str | None) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        user_id="user-1",
        thread_id=thread_id,
        message_id="telegram-msg",
    )


def test_cache_session_source_if_absent_preserves_original_thread_source():
    runner = _runner()

    original = _source("thread-root", "thread-msg")
    interrupting = _source(None, "channel-msg")
    session_key = build_session_key(original)

    assert build_session_key(interrupting) != session_key

    runner._cache_session_source_if_absent(session_key, original)
    runner._cache_session_source_if_absent(session_key, interrupting)

    cached = runner._get_cached_session_source(session_key)
    assert cached is not None
    assert cached.thread_id == "thread-root"
    assert cached.message_id == "thread-msg"


def test_thread_metadata_for_session_source_falls_back_to_cached_thread():
    runner = _runner()
    session_key = build_session_key(_source("thread-root"))

    runner._cache_session_source_if_absent(session_key, _source("thread-root"))

    metadata = runner._thread_metadata_for_session_source(
        session_key,
        _source(None, "channel-msg"),
        reply_to_message_id="channel-msg",
    )

    assert metadata == {"thread_id": "thread-root"}


def test_thread_metadata_for_session_source_prefers_current_thread():
    runner = _runner()
    session_key = build_session_key(_source("thread-root"))

    runner._cache_session_source_if_absent(session_key, _source("thread-root"))

    metadata = runner._thread_metadata_for_session_source(
        session_key,
        _source("current-thread"),
        reply_to_message_id="current-msg",
    )

    assert metadata == {"thread_id": "current-thread"}


def test_thread_metadata_for_session_source_falls_back_to_cached_telegram_topic():
    runner = _runner()
    session_key = build_session_key(_telegram_topic_source("topic-42"))

    runner._cache_session_source_if_absent(
        session_key,
        _telegram_topic_source("topic-42"),
    )

    metadata = runner._thread_metadata_for_session_source(
        session_key,
        _telegram_topic_source(None),
    )

    assert metadata == {"thread_id": "topic-42"}
