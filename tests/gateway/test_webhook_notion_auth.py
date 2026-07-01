"""Notion webhook auth tests for the generic webhook adapter."""

import asyncio
import hashlib
import hmac
import json
import os
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.webhook import WebhookAdapter


def _make_adapter(
    token_store: str,
    *,
    deliver: str = "log",
    deliver_extra: dict | None = None,
    route_overrides: dict | None = None,
) -> WebhookAdapter:
    route = {
        "provider": "notion",
        "auth": {
            "mode": "notion-signature",
            "token_store": token_store,
        },
        "events": ["comment.created"],
        "prompt": "Notion event: {type}",
        "deliver": deliver,
    }
    if deliver_extra is not None:
        route["deliver_extra"] = deliver_extra
    if route_overrides is not None:
        route.update(route_overrides)
    config = PlatformConfig(
        enabled=True,
        extra={
            "host": "127.0.0.1",
            "port": 0,
            "routes": {"notion": route},
        },
    )
    return WebhookAdapter(config)


def _create_app(adapter: WebhookAdapter) -> web.Application:
    app = web.Application()
    app.router.add_get("/health", adapter._handle_health)
    app.router.add_post("/webhooks/{route_name}", adapter._handle_webhook)
    return app


def _notion_signature(body: bytes, token: str) -> str:
    return "sha256=" + hmac.new(token.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_notion_verification_token_is_captured_before_dispatch(tmp_path):
    token_store = tmp_path / "notion_tokens.json"
    adapter = _make_adapter(str(token_store))
    captured_events = []

    async def _capture(event):
        captured_events.append(event)

    adapter.handle_message = _capture
    app = _create_app(adapter)

    body = b'{"verification_token":"notion-test-token"}'
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.post(
            "/webhooks/notion",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 200
        assert await resp.json() == {
            "status": "verification_token_captured",
            "route": "notion",
        }

    with token_store.open(encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["routes"]["notion"]["verification_token"] == "notion-test-token"
    if os.name != "nt":
        assert token_store.stat().st_mode & 0o777 == 0o600
    assert captured_events == []


@pytest.mark.asyncio
async def test_notion_signed_event_accepts_and_rejects_raw_body_mutation(tmp_path):
    token_store = tmp_path / "notion_tokens.json"
    adapter = _make_adapter(str(token_store))
    captured_events = []

    async def _capture(event):
        captured_events.append(event)

    adapter.handle_message = _capture
    app = _create_app(adapter)

    verify_body = b'{"verification_token":"notion-test-token"}'
    event_body = b'{"id":"evt_1","type":"comment.created","entity":{"id":"page_1"}}'
    valid_sig = _notion_signature(event_body, "notion-test-token")

    async with TestClient(TestServer(app)) as cli:
        verify_resp = await cli.post(
            "/webhooks/notion",
            data=verify_body,
            headers={"Content-Type": "application/json"},
        )
        assert verify_resp.status == 200

        accepted = await cli.post(
            "/webhooks/notion",
            data=event_body,
            headers={
                "Content-Type": "application/json",
                "X-Notion-Signature": valid_sig,
            },
        )
        assert accepted.status == 202
        assert (await accepted.json())["status"] == "accepted"

        duplicate = await cli.post(
            "/webhooks/notion",
            data=event_body,
            headers={
                "Content-Type": "application/json",
                "X-Notion-Signature": valid_sig,
            },
        )
        assert duplicate.status == 200
        duplicate_json = await duplicate.json()
        assert duplicate_json["status"] == "duplicate"
        assert duplicate_json["delivery_id"] == "evt_1"

        mutated = await cli.post(
            "/webhooks/notion",
            data=event_body + b"\n",
            headers={
                "Content-Type": "application/json",
                "X-Notion-Signature": valid_sig,
            },
        )
        assert mutated.status == 401
        assert (await mutated.json())["error"] == "Invalid Notion signature"

        missing = await cli.post(
            "/webhooks/notion",
            data=event_body,
            headers={"Content-Type": "application/json"},
        )
        assert missing.status == 401

    assert len(captured_events) == 1
    assert captured_events[0].raw_message["type"] == "comment.created"


@pytest.mark.asyncio
async def test_notion_signed_event_ignores_configured_author_before_dispatch(tmp_path):
    token_store = tmp_path / "notion_tokens.json"
    adapter = _make_adapter(
        str(token_store),
        route_overrides={"ignore_actor_ids": ["hermes-bot-user"]},
    )
    captured_events = []

    async def _capture(event):
        captured_events.append(event)

    adapter.handle_message = _capture
    app = _create_app(adapter)

    verify_body = b'{"verification_token":"notion-test-token"}'
    event_body = json.dumps(
        {
            "id": "evt_self",
            "type": "comment.created",
            "authors": [{"id": "hermes-bot-user", "type": "bot"}],
            "entity": {"id": "comment_1", "type": "comment"},
            "data": {"parent": {"id": "page_1", "type": "page"}},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    async with TestClient(TestServer(app)) as cli:
        verify_resp = await cli.post(
            "/webhooks/notion",
            data=verify_body,
            headers={"Content-Type": "application/json"},
        )
        assert verify_resp.status == 200

        ignored = await cli.post(
            "/webhooks/notion",
            data=event_body,
            headers={
                "Content-Type": "application/json",
                "X-Notion-Signature": _notion_signature(
                    event_body, "notion-test-token"
                ),
            },
        )
        ignored_json = await ignored.json()

    assert ignored.status == 200
    assert ignored_json == {
        "status": "ignored",
        "event": "comment.created",
        "reason": "ignored_actor",
        "actor_id": "hermes-bot-user",
    }
    assert captured_events == []


@pytest.mark.asyncio
async def test_notion_trigger_filter_ignores_comment_without_mention(tmp_path, monkeypatch):
    token_store = tmp_path / "notion_tokens.json"
    monkeypatch.setenv("NOTION_API_TOKEN", "notion-api-token")
    adapter = _make_adapter(
        str(token_store),
        route_overrides={
            "notion_trigger_filter": {
                "enabled": True,
                "bot_user_id": "hermes-bot-user",
                "bot_names": ["Hermes"],
                "allow_plain_text_mentions": True,
            }
        },
    )
    captured_events = []

    async def _capture(event):
        captured_events.append(event)

    adapter.handle_message = _capture
    app = _create_app(adapter)

    verify_body = b'{"verification_token":"notion-test-token"}'
    event_body = b'{"id":"evt_no_mention","type":"comment.created","authors":[{"id":"human-user","type":"person"}],"entity":{"id":"comment_1","type":"comment"}}'

    with patch.object(
        adapter,
        "_get_notion_api_json",
        return_value={
            "object": "comment",
            "id": "comment_1",
            "created_by": {"id": "human-user", "type": "person"},
            "rich_text": [{"type": "text", "plain_text": "hello"}],
        },
    ) as get_json:
        async with TestClient(TestServer(app)) as cli:
            assert (
                await cli.post(
                    "/webhooks/notion",
                    data=verify_body,
                    headers={"Content-Type": "application/json"},
                )
            ).status == 200

            ignored = await cli.post(
                "/webhooks/notion",
                data=event_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Notion-Signature": _notion_signature(
                        event_body, "notion-test-token"
                    ),
                },
            )
            ignored_json = await ignored.json()

    assert ignored.status == 200
    assert ignored_json == {
        "status": "ignored",
        "event": "comment.created",
        "reason": "notion_trigger_required",
    }
    get_json.assert_called_once()
    assert captured_events == []


@pytest.mark.asyncio
async def test_notion_trigger_filter_accepts_comment_plaintext_mention(tmp_path, monkeypatch):
    token_store = tmp_path / "notion_tokens.json"
    monkeypatch.setenv("NOTION_API_TOKEN", "notion-api-token")
    adapter = _make_adapter(
        str(token_store),
        route_overrides={
            "notion_trigger_filter": {
                "enabled": True,
                "bot_user_id": "hermes-bot-user",
                "bot_names": ["Hermes"],
                "allow_plain_text_mentions": True,
            }
        },
    )
    captured_events = []

    async def _capture(event):
        captured_events.append(event)

    adapter.handle_message = _capture
    app = _create_app(adapter)

    verify_body = b'{"verification_token":"notion-test-token"}'
    event_body = b'{"id":"evt_mention","type":"comment.created","authors":[{"id":"human-user","type":"person"}],"entity":{"id":"comment_1","type":"comment"}}'

    with patch.object(
        adapter,
        "_get_notion_api_json",
        return_value={
            "object": "comment",
            "id": "comment_1",
            "created_by": {"id": "human-user", "type": "person"},
            "rich_text": [{"type": "text", "plain_text": "@Hermes please ack"}],
        },
    ):
        async with TestClient(TestServer(app)) as cli:
            assert (
                await cli.post(
                    "/webhooks/notion",
                    data=verify_body,
                    headers={"Content-Type": "application/json"},
                )
            ).status == 200

            accepted = await cli.post(
                "/webhooks/notion",
                data=event_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Notion-Signature": _notion_signature(
                        event_body, "notion-test-token"
                    ),
                },
            )
            accepted_json = await accepted.json()
            await asyncio.sleep(0)

    assert accepted.status == 202
    assert accepted_json["status"] == "accepted"
    assert len(captured_events) == 1
    assert captured_events[0].raw_message["_notion_context"]["trigger"] == "comment_mention"


@pytest.mark.asyncio
async def test_notion_trigger_filter_accepts_people_assignment(tmp_path, monkeypatch):
    token_store = tmp_path / "notion_tokens.json"
    monkeypatch.setenv("NOTION_API_TOKEN", "notion-api-token")
    adapter = _make_adapter(
        str(token_store),
        route_overrides={
            "events": ["comment.created", "page.properties_updated"],
            "notion_trigger_filter": {
                "enabled": True,
                "bot_user_id": "hermes-bot-user",
                "bot_names": ["Hermes"],
                "assignment_people_properties": ["Assignee"],
            },
        },
    )
    captured_events = []

    async def _capture(event):
        captured_events.append(event)

    adapter.handle_message = _capture
    app = _create_app(adapter)

    verify_body = b'{"verification_token":"notion-test-token"}'
    event_body = b'{"id":"evt_assigned","type":"page.properties_updated","authors":[{"id":"human-user","type":"person"}],"entity":{"id":"page_1","type":"page"},"data":{"updated_properties":[{"name":"Assignee","action":"updated"}]}}'

    with patch.object(
        adapter,
        "_get_notion_api_json",
        return_value={
            "object": "page",
            "id": "page_1",
            "properties": {
                "Assignee": {
                    "type": "people",
                    "people": [{"id": "hermes-bot-user", "name": "Hermes"}],
                }
            },
        },
    ):
        async with TestClient(TestServer(app)) as cli:
            assert (
                await cli.post(
                    "/webhooks/notion",
                    data=verify_body,
                    headers={"Content-Type": "application/json"},
                )
            ).status == 200

            accepted = await cli.post(
                "/webhooks/notion",
                data=event_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Notion-Signature": _notion_signature(
                        event_body, "notion-test-token"
                    ),
                },
            )
            accepted_json = await accepted.json()
            await asyncio.sleep(0)

    assert accepted.status == 202
    assert accepted_json["status"] == "accepted"
    assert len(captured_events) == 1
    assert captured_events[0].raw_message["_notion_context"]["trigger"] == "people_assignment"


@pytest.mark.asyncio
async def test_notion_event_before_token_capture_fails_closed(tmp_path):
    adapter = _make_adapter(str(tmp_path / "notion_tokens.json"))
    app = _create_app(adapter)
    body = b'{"id":"evt_1","type":"comment.created"}'
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.post(
            "/webhooks/notion",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Notion-Signature": _notion_signature(body, "unknown-token"),
            },
        )
        assert resp.status == 401
        assert (await resp.json())["error"] == "Notion verification token is missing"


@pytest.mark.asyncio
async def test_notion_comment_delivery_posts_only_final_response(tmp_path, monkeypatch):
    adapter = _make_adapter(
        str(tmp_path / "notion_tokens.json"),
        deliver="notion_comment",
        deliver_extra={
            "token_env": "NOTION_API_TOKEN",
            "notion_version": "2026-03-11",
        },
    )
    monkeypatch.setenv("NOTION_API_TOKEN", "notion-api-token")
    chat_id = "webhook:notion:evt_final"
    adapter._delivery_info[chat_id] = {
        "deliver": "notion_comment",
        "deliver_extra": {
            "token_env": "NOTION_API_TOKEN",
            "notion_version": "2026-03-11",
        },
        "payload": {
            "id": "evt_final",
            "type": "comment.created",
            "entity": {"id": "page_1", "type": "page"},
        },
    }

    with patch.object(
        adapter,
        "_post_notion_comment",
        return_value={"object": "comment", "id": "comment_1"},
    ) as post_comment:
        interim = await adapter.send(chat_id, "Working on it")
        final = await adapter.send(
            chat_id,
            "Acknowledged: I received the Notion mention.",
            metadata={"notify": True},
        )

    assert interim.success is True
    assert final.success is True
    assert final.message_id == "comment_1"
    post_comment.assert_called_once_with(
        {
            "markdown": "Acknowledged: I received the Notion mention.",
            "parent": {"page_id": "page_1"},
        },
        "notion-api-token",
        "2026-03-11",
    )
