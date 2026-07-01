# PR Draft: Scope Responses API conversation names by session key

## Title

Scope Responses API conversation names by session key

## Summary

This scopes Responses API `conversation` chaining to `X-Hermes-Session-Key` when the header is present. Previously, conversation names were global, so two different clients using the same conversation name could chain into each other's stored response history.

## Why

`X-Hermes-Session-Key` is the long-term memory boundary. If two clients use the same conversation name under different keys, the server should keep those chains isolated instead of reusing the latest response ID from another key.

## Changes

- Namespace the stored conversation lookup key with the active session key when present.
- Preserve the existing global behavior for callers that do not send a session key.
- Add a regression test proving the same conversation name stays isolated across two different session keys.

## Tests

```text
python -m pytest tests/gateway/test_api_server.py -k "conversation_names_are_scoped_by_session_key or separate_conversations_are_isolated or conversation_chains_automatically" -q
3 passed, 155 deselected, 3 warnings in 2.49s
```

## Branch

Local branch:

```text
fix/responses-conversation-session-key-scope
```
