"""Tests for dashboard/desktop filesystem write endpoints."""

import pytest


def _client():
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


def test_fs_write_text_rejects_memory_md_over_configured_char_limit(_isolate_hermes_home):
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    memories = home / "memories"
    memories.mkdir(exist_ok=True)
    target = memories / "MEMORY.md"
    target.write_text("old memory\n", encoding="utf-8")
    (home / "config.yaml").write_text(
        "memory:\n"
        "  memory_char_limit: 5\n"
        "  user_char_limit: 99\n",
        encoding="utf-8",
    )

    response = _client().post(
        "/api/fs/write-text",
        json={"path": str(target), "content": "123456"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "MEMORY.md" in detail
    assert "6/5 chars" in detail
    assert "memory.memory_char_limit" in detail
    assert target.read_text(encoding="utf-8") == "old memory\n"


def test_fs_write_text_rejects_profile_user_md_over_configured_char_limit(_isolate_hermes_home):
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    profile_home = home / "profiles" / "work"
    memories = profile_home / "memories"
    memories.mkdir(parents=True)
    target = memories / "USER.md"
    target.write_text("old user\n", encoding="utf-8")
    (profile_home / "config.yaml").write_text(
        "memory:\n"
        "  memory_char_limit: 99\n"
        "  user_char_limit: 4\n",
        encoding="utf-8",
    )

    response = _client().post(
        "/api/fs/write-text",
        json={"path": str(target), "content": "12345"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "USER.md" in detail
    assert "5/4 chars" in detail
    assert "memory.user_char_limit" in detail
    assert target.read_text(encoding="utf-8") == "old user\n"
