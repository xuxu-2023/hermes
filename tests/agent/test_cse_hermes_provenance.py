from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


RAW_CHAT_ID = "123456789"
RAW_USER_ID = "987654321"
RAW_THREAD_ID = "555777"
RAW_PROMPT_SENTINEL = "RAW-HERMES-CSE-PROVENANCE-SHOULD-NOT-LEAK"


@pytest.fixture(autouse=True)
def _fixed_ref_salt(monkeypatch):
    monkeypatch.setenv("HERMES_CSE_PROVENANCE_REF_SALT", "test-cse-hermes-provenance-salt")


def _agent(**overrides):
    values = {
        "model": "cse-live",
        "provider": "custom",
        "base_url": "http://127.0.0.1:18080/v1",
        "session_id": "raw-session-id-for-test",
        "platform": "cli",
        "chat_type": None,
        "chat_id": None,
        "thread_id": None,
        "user_id": None,
        "user_id_alt": None,
        "gateway_session_key": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cse_live_chat_kwargs_receive_native_sideband_metadata_without_prompt_smuggling():
    from agent.cse_hermes_provenance import (
        PROVENANCE_METADATA_KEY,
        attach_cse_hermes_provenance_metadata,
    )

    messages = [{"role": "user", "content": f"hello {RAW_PROMPT_SENTINEL}"}]
    kwargs = {"model": "cse-live", "messages": [message.copy() for message in messages]}

    attached = attach_cse_hermes_provenance_metadata(_agent(), kwargs, messages)

    provenance = attached["metadata"][PROVENANCE_METADATA_KEY]
    assert provenance["schema_version"] == "cse.hermes.provenance.v1"
    assert provenance["source_kind"] == "cli"
    assert provenance["surface_kind"] == "cli"
    assert provenance["message_kind"] == "user_message"
    assert provenance["entity_id"] == "cse-live"
    assert provenance["capability_mode"] == "text_only_pr2"
    assert provenance["provenance_policy"] == "required_for_v1"
    assert provenance["provenance_completeness"] == "complete"
    assert provenance["payload_redaction_state"] == "redacted"
    assert provenance["producer"] == {
        "system": "hermes-agent",
        "kind": "native_body_signal_producer",
        "authority": "hermes",
    }
    assert provenance["delivery_context"]["delivery_authority"] == "hermes"
    assert provenance["audit_context"]["audit_ref"].startswith("ref:audit:")
    assert provenance["hermes_session_id"].startswith("session_")
    assert provenance["conversation_id"].startswith("conversation_")
    assert RAW_PROMPT_SENTINEL not in json.dumps(provenance)
    assert attached["messages"] == kwargs["messages"]
    assert "metadata" not in attached["messages"][0]


def test_gateway_provenance_redacts_raw_chat_user_thread_and_gateway_session_values():
    from agent.cse_hermes_provenance import build_cse_hermes_provenance

    agent = _agent(
        platform="telegram",
        chat_type="group",
        chat_id=RAW_CHAT_ID,
        user_id=RAW_USER_ID,
        user_id_alt="raw-alt-user-id",
        thread_id=RAW_THREAD_ID,
        gateway_session_key="telegram:123456789:555777",
    )

    provenance = build_cse_hermes_provenance(agent, [{"role": "user", "content": "hi"}])
    serialized = json.dumps(provenance, sort_keys=True)

    assert provenance["source_kind"] == "telegram"
    assert provenance["surface_kind"] == "topic_thread"
    refs = provenance["platform_refs"]
    assert refs["platform"] == "telegram"
    assert refs["chat_ref"].startswith("ref:chat:")
    assert refs["user_ref"].startswith("ref:user:")
    assert refs["topic_ref"].startswith("ref:topic:")
    for raw in (RAW_CHAT_ID, RAW_USER_ID, RAW_THREAD_ID, "telegram:123456789:555777"):
        assert raw not in serialized
    assert provenance["raw_identifier_values_included"] is False


def test_tool_result_request_sets_tool_round_trip_kind_without_copying_tool_result_text():
    from agent.cse_hermes_provenance import build_cse_hermes_provenance

    messages = [
        {"role": "user", "content": "use a tool"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_safe_123", "type": "function", "function": {"name": "skills_list", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_safe_123", "content": RAW_PROMPT_SENTINEL},
    ]

    provenance = build_cse_hermes_provenance(_agent(), messages)

    assert provenance["message_kind"] == "tool_result"
    assert provenance["capability_mode"] == "tool_round_trip"
    assert RAW_PROMPT_SENTINEL not in json.dumps(provenance)


def test_non_cse_models_are_not_given_cse_body_signal_metadata_by_default():
    from agent.cse_hermes_provenance import attach_cse_hermes_provenance_metadata

    kwargs = {"model": "openai/gpt-5.1", "messages": [{"role": "user", "content": "hi"}]}

    attached = attach_cse_hermes_provenance_metadata(
        _agent(model="openai/gpt-5.1", provider="openrouter"),
        kwargs,
        kwargs["messages"],
    )

    assert "metadata" not in attached


def test_cse_live_route_overwrites_preexisting_spoofable_provenance_metadata():
    from agent.cse_hermes_provenance import (
        PROVENANCE_METADATA_KEY,
        attach_cse_hermes_provenance_metadata,
    )

    kwargs = {
        "model": "cse-live",
        "messages": [{"role": "user", "content": "hi"}],
        "metadata": {PROVENANCE_METADATA_KEY: {"request_id": "req_fake_spoof"}, "keep": "me"},
    }

    attached = attach_cse_hermes_provenance_metadata(_agent(), kwargs, kwargs["messages"])

    assert attached["metadata"]["keep"] == "me"
    provenance = attached["metadata"][PROVENANCE_METADATA_KEY]
    assert provenance["request_id"] != "req_fake_spoof"
    assert provenance["producer"]["kind"] == "native_body_signal_producer"


def test_disable_env_override_does_not_enable_cse_metadata_for_non_cse_models(monkeypatch):
    from agent.cse_hermes_provenance import attach_cse_hermes_provenance_metadata

    monkeypatch.setenv("HERMES_CSE_HERMES_PROVENANCE", "1")
    kwargs = {"model": "openai/gpt-5.1", "messages": [{"role": "user", "content": "hi"}]}

    attached = attach_cse_hermes_provenance_metadata(
        _agent(model="openai/gpt-5.1", provider="openrouter"),
        kwargs,
        kwargs["messages"],
    )

    assert "metadata" not in attached


def test_unknown_origin_does_not_claim_complete_required_v1_provenance():
    from agent.cse_hermes_provenance import build_cse_hermes_provenance

    provenance = build_cse_hermes_provenance(
        _agent(platform="mystery", chat_id=None, user_id=None, thread_id=None),
        [{"role": "user", "content": "hi"}],
    )

    assert provenance["source_kind"] == "unknown"
    assert provenance["surface_kind"] == "unknown"
    assert provenance["provenance_policy"] == "optional_transient"
    assert provenance["provenance_completeness"] == "partial"


def test_build_api_kwargs_attaches_native_metadata_for_cse_live_custom_provider():
    from agent.transports.chat_completions import ChatCompletionsTransport
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent.api_mode = "chat_completions"
    agent.model = "cse-live"
    agent.provider = "custom"
    agent.base_url = "http://127.0.0.1:18080/v1"
    agent._base_url_lower = agent.base_url.lower()
    agent._base_url_hostname = "127.0.0.1"
    agent.tools = []
    agent.max_tokens = None
    agent.reasoning_config = None
    agent.request_overrides = {}
    agent.session_id = "integration-session-id"
    agent.platform = "cli"
    agent.chat_type = None
    agent.chat_id = None
    agent.thread_id = None
    agent.user_id = None
    agent.user_id_alt = None
    agent.gateway_session_key = None
    agent._api_call_count = 1
    agent._ephemeral_max_output_tokens = None
    agent._ollama_num_ctx = None
    agent.providers_allowed = None
    agent.providers_ignored = None
    agent.providers_order = None
    agent.provider_sort = None
    agent.provider_require_parameters = False
    agent.provider_data_collection = None
    agent.openrouter_min_coding_score = None
    agent._get_transport = lambda: ChatCompletionsTransport()
    agent._is_qwen_portal = lambda: False
    agent._is_openrouter_url = lambda: False
    agent._resolved_api_call_timeout = lambda: None
    agent._max_tokens_param = lambda value: {"max_tokens": value}
    agent._supports_reasoning_extra_body = lambda: False
    agent._github_models_reasoning_extra_body = lambda: None
    agent._lmstudio_reasoning_options_cached = lambda: None
    agent._prepare_messages_for_non_vision_model = lambda messages: messages
    agent._qwen_prepare_chat_messages = lambda messages: messages
    agent._qwen_prepare_chat_messages_inplace = lambda messages: None

    kwargs = agent._build_api_kwargs([{"role": "user", "content": "hi"}])

    assert kwargs["metadata"]["cse_hermes_provenance"]["entity_id"] == "cse-live"
    assert kwargs["metadata"]["cse_hermes_provenance"]["source_kind"] == "cli"
