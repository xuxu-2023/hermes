import asyncio

from hermes_cli import web_server


class _FakeSessionDB:
    """Fake backing the /api/sessions/search endpoint.

    The endpoint surfaces direct session-id matches first, then FTS message
    matches, deduping both by compression lineage root. This fake has no
    compression chains (get_session returns no parent), so each session is its
    own lineage root.
    """

    closed = False

    def search_sessions_by_id(self, query, limit=20, include_archived=True):
        assert query == "20260603"
        assert include_archived is True
        return [
            {
                "id": "20260603_090200_exact",
                "preview": "ID match preview",
                "source": "cli",
                "model": "claude",
                "started_at": 100,
            }
        ]

    def search_messages(self, query, limit=20):
        assert query == "20260603*"
        return [
            {
                "session_id": "20260603_090200_exact",
                "snippet": "duplicate content hit should not replace ID hit",
                "role": "user",
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "session_id": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]

    def get_session(self, session_id):
        # No compression chains in this fixture — every session is its own root.
        return {"id": session_id, "parent_session_id": None}

    def get_compression_tip(self, session_id):
        return session_id

    def close(self):
        self.closed = True


def test_desktop_session_search_merges_id_matches_before_content_matches(monkeypatch):
    monkeypatch.setattr("hermes_state.SessionDB", _FakeSessionDB)

    response = asyncio.run(web_server.search_sessions(q="20260603", limit=2))

    # ID match surfaces first; the content hit on the SAME session is deduped
    # by lineage root (not double-listed); the unrelated content hit follows.
    assert response == {
        "results": [
            {
                "session_id": "20260603_090200_exact",
                "lineage_root": "20260603_090200_exact",
                "snippet": "ID match preview",
                "role": None,
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "session_id": "content_session",
                "lineage_root": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]
    }


class _ScopeRecordingSessionDB:
    """Fake that records how /api/sessions/search drives the db layer.

    Captures whether the session-id pass ran and what ``role_filter`` reached
    ``search_messages``, so the scope tests can assert on the wiring rather than
    a particular result shape. Returns one user, one assistant and one tool hit
    so a role-scoped query has something to narrow.
    """

    def __init__(self):
        self.id_search_called = False
        self.search_messages_kwargs = None

    def search_sessions_by_id(self, query, limit=20, include_archived=True):
        self.id_search_called = True
        return [
            {
                "id": "id_hit",
                "preview": "id preview",
                "source": "cli",
                "model": "claude",
                "started_at": 10,
            }
        ]

    def search_messages(self, query, limit=20, role_filter=None):
        self.search_messages_kwargs = {
            "query": query,
            "limit": limit,
            "role_filter": role_filter,
        }
        rows = [
            {
                "session_id": "user_session",
                "snippet": "user hit",
                "role": "user",
                "source": "cli",
                "model": "claude",
                "session_started": 20,
            },
            {
                "session_id": "assistant_session",
                "snippet": "assistant hit",
                "role": "assistant",
                "source": "cli",
                "model": "claude",
                "session_started": 30,
            },
            {
                "session_id": "tool_session",
                "snippet": "tool hit",
                "role": "tool",
                "source": "cli",
                "model": "claude",
                "session_started": 40,
            },
        ]
        # Mirror the db layer: when a role_filter is supplied, only rows with a
        # matching role come back.
        if role_filter is not None:
            rows = [r for r in rows if r["role"] in role_filter]
        return rows

    def get_session(self, session_id):
        return {"id": session_id, "parent_session_id": None}

    def get_compression_tip(self, session_id):
        return session_id

    def close(self):
        pass


def test_session_search_scope_all_is_unchanged_default(monkeypatch):
    """scope defaults to "all": id-pass runs and no role_filter is sent.

    This is the byte-identical-to-stock path — search_messages is called with
    exactly the kwargs the old handler used (no role_filter key).
    """
    fake = _ScopeRecordingSessionDB()
    monkeypatch.setattr("hermes_state.SessionDB", lambda *a, **k: fake)

    explicit = asyncio.run(web_server.search_sessions(q="docker", limit=10, scope="all"))
    assert fake.id_search_called is True
    assert fake.search_messages_kwargs["role_filter"] is None

    fake_default = _ScopeRecordingSessionDB()
    monkeypatch.setattr("hermes_state.SessionDB", lambda *a, **k: fake_default)
    omitted = asyncio.run(web_server.search_sessions(q="docker", limit=10))

    # Omitting scope and passing scope="all" produce the same result and the
    # same db driving (id-pass on, role_filter off).
    assert omitted == explicit
    assert fake_default.id_search_called is True
    assert fake_default.search_messages_kwargs["role_filter"] is None
    # The id-match session leads (every role present in the result set).
    roots = [r["lineage_root"] for r in explicit["results"]]
    assert roots == ["id_hit", "user_session", "assistant_session", "tool_session"]


def test_session_search_scope_messages_filters_to_prose(monkeypatch):
    """scope="messages" → user+assistant role_filter and no session-id pass."""
    fake = _ScopeRecordingSessionDB()
    monkeypatch.setattr("hermes_state.SessionDB", lambda *a, **k: fake)

    response = asyncio.run(
        web_server.search_sessions(q="docker", limit=10, scope="messages")
    )

    assert fake.id_search_called is False
    assert fake.search_messages_kwargs["role_filter"] == ["user", "assistant"]
    roots = [r["lineage_root"] for r in response["results"]]
    assert roots == ["user_session", "assistant_session"]
    assert "id_hit" not in roots
    assert "tool_session" not in roots


def test_session_search_scope_code_filters_to_tool(monkeypatch):
    """scope="code" → tool-only role_filter and no session-id pass."""
    fake = _ScopeRecordingSessionDB()
    monkeypatch.setattr("hermes_state.SessionDB", lambda *a, **k: fake)

    response = asyncio.run(
        web_server.search_sessions(q="docker", limit=10, scope="code")
    )

    assert fake.id_search_called is False
    assert fake.search_messages_kwargs["role_filter"] == ["tool"]
    roots = [r["lineage_root"] for r in response["results"]]
    assert roots == ["tool_session"]


def test_session_search_invalid_scope_falls_back_to_all(monkeypatch):
    """An unrecognised scope is handled gracefully — treated as "all".

    No error is raised; the id-pass runs and search_messages gets no
    role_filter, exactly like scope="all".
    """
    fake = _ScopeRecordingSessionDB()
    monkeypatch.setattr("hermes_state.SessionDB", lambda *a, **k: fake)

    response = asyncio.run(
        web_server.search_sessions(q="docker", limit=10, scope="bogus")
    )

    assert fake.id_search_called is True
    assert fake.search_messages_kwargs["role_filter"] is None
    roots = [r["lineage_root"] for r in response["results"]]
    assert roots == ["id_hit", "user_session", "assistant_session", "tool_session"]
