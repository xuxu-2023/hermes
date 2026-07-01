"""Tests for the shared cross-profile Codex (openai-codex) OAuth store.

Hermes keeps a per-profile openai-codex singleton token, but OAuth
refresh_tokens are single-use. When one gateway (e.g. the default profile)
refreshes it rotates the shared ChatGPT/Codex account token, so a sibling
profile (e.g. a separate ``--profile`` gateway) holding the old refresh token
401s until a manual re-auth.

The shared store (``<hermes-root>/shared/codex_auth.json``) is written on every
singleton save so sibling profiles can recover the freshly rotated token
instead of bouncing to device-code. It complements — does not replace — the
existing ``~/.codex/auth.json`` self-heal (which only recovers tokens the Codex
CLI rotated; Hermes does not write back to ~/.codex, see #12360).
"""

import base64
import json
import time

import pytest

import hermes_cli.auth as auth
from hermes_cli.auth import (
    AuthError,
    _read_shared_codex_state,
    _recover_codex_tokens_from_shared,
    _refresh_codex_auth_tokens,
    _write_shared_codex_state,
)


@pytest.fixture
def shared_store_env(tmp_path, monkeypatch):
    """Redirect HERMES_SHARED_AUTH_DIR to a tmp_path.

    Required for every test that exercises the shared Codex store — the
    in-auth.py seat belt refuses to touch the real user's shared store under
    pytest, so tests that forget this fixture fail loudly instead of
    corrupting real state.
    """
    shared_dir = tmp_path / "shared"
    monkeypatch.setenv("HERMES_SHARED_AUTH_DIR", str(shared_dir))
    return shared_dir


def _expired_jwt() -> str:
    """A minimal JWT whose exp is in the past (so it reads as expiring)."""
    def b64(obj):
        raw = json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = b64({"alg": "none", "typ": "JWT"})
    payload = b64({"exp": int(time.time()) - 3600})
    return f"{header}.{payload}.sig"


# ── store read/write primitives ──────────────────────────────────────────────

def test_shared_write_and_read_roundtrip(shared_store_env):
    _write_shared_codex_state(
        {"access_token": "access-1", "refresh_token": "refresh-1"},
        last_refresh="2026-06-16T00:00:00Z",
    )
    out = _read_shared_codex_state()
    assert out is not None
    assert out["access_token"] == "access-1"
    assert out["refresh_token"] == "refresh-1"
    assert out["last_refresh"] == "2026-06-16T00:00:00Z"


def test_shared_write_skips_without_refresh_token(shared_store_env):
    _write_shared_codex_state({"access_token": "access-only"})
    assert _read_shared_codex_state() is None


def test_shared_read_missing_returns_none(shared_store_env):
    assert _read_shared_codex_state() is None


def test_shared_read_malformed_returns_none(shared_store_env):
    shared_store_env.mkdir(parents=True, exist_ok=True)
    (shared_store_env / "codex_auth.json").write_text("{ not json")
    assert _read_shared_codex_state() is None


def test_shared_file_is_owner_only(shared_store_env):
    import os
    import stat as stat_mod

    _write_shared_codex_state(
        {"access_token": "access-1", "refresh_token": "refresh-1"}
    )
    path = shared_store_env / "codex_auth.json"
    assert path.is_file()
    if os.name != "nt":
        mode = stat_mod.S_IMODE(path.stat().st_mode)
        assert mode == 0o600


# ── write-through hook on _save_codex_tokens ─────────────────────────────────

def test_save_codex_tokens_publishes_to_shared(shared_store_env):
    # HERMES_HOME is redirected to a tmp dir by the autouse conftest fixture,
    # so this writes a real per-profile auth.json under the test root.
    auth._save_codex_tokens(
        {"access_token": "saved-access", "refresh_token": "saved-refresh"}
    )
    shared = _read_shared_codex_state()
    assert shared is not None
    assert shared["access_token"] == "saved-access"
    assert shared["refresh_token"] == "saved-refresh"


# ── recovery from the shared store ───────────────────────────────────────────

def test_recover_from_shared_adopts_valid_token(shared_store_env, monkeypatch):
    saved = {}
    monkeypatch.setattr(auth, "_save_codex_tokens", lambda t, *a, **k: saved.update(t))
    _write_shared_codex_state(
        {"access_token": "fresh-access", "refresh_token": "fresh-refresh"}
    )

    out = _recover_codex_tokens_from_shared("test")

    assert out == {"access_token": "fresh-access", "refresh_token": "fresh-refresh"}
    # adopted token was persisted into the per-profile store
    assert saved["access_token"] == "fresh-access"


def test_recover_from_shared_returns_none_when_empty(shared_store_env):
    assert _recover_codex_tokens_from_shared("test") is None


def test_recover_from_shared_skips_expiring_token(shared_store_env, monkeypatch):
    save_spy = {"n": 0}
    monkeypatch.setattr(auth, "_save_codex_tokens", lambda *a, **k: save_spy.__setitem__("n", save_spy["n"] + 1))
    # An expiring access_token must NOT be adopted — caller should fall through
    # to the Codex CLI store / device-code instead of taking a soon-dead token.
    _write_shared_codex_state(
        {"access_token": _expired_jwt(), "refresh_token": "refresh-x"}
    )

    assert _recover_codex_tokens_from_shared("test") is None
    assert save_spy["n"] == 0


# ── integration: refresh prefers shared store over the Codex CLI store ───────

def test_refresh_prefers_shared_over_cli(shared_store_env, monkeypatch):
    """On a relogin-required refresh failure, a valid sibling-published token in
    the shared store is adopted before falling back to ~/.codex."""
    cli_calls = {"n": 0}

    def _rejected(*_a, **_k):
        raise AuthError(
            "refresh token reused",
            provider="openai-codex",
            code="refresh_token_reused",
            relogin_required=True,
        )

    def _cli_spy():
        cli_calls["n"] += 1
        return {"access_token": "cli-access", "refresh_token": "cli-refresh"}

    monkeypatch.setattr(auth, "refresh_codex_oauth_pure", _rejected)
    monkeypatch.setattr(auth, "_import_codex_cli_tokens", _cli_spy)
    monkeypatch.setattr(auth, "_save_codex_tokens", lambda *a, **k: None)
    _write_shared_codex_state(
        {"access_token": "sibling-access", "refresh_token": "sibling-refresh"}
    )

    out = _refresh_codex_auth_tokens(
        {"access_token": "stale-access", "refresh_token": "stale-refresh"}, 20.0
    )

    assert out["access_token"] == "sibling-access"
    assert cli_calls["n"] == 0  # shared store satisfied the recovery; ~/.codex untouched
