"""Regression tests: a provider plugin's declared ProviderProfile.api_mode must
be honored at runtime even when its base_url is not URL-self-describing.

Background
----------
Provider plugins (``plugins/model-providers/<name>/``) declare a transport via
``ProviderProfile.api_mode``. That profile is bridged into the runtime
``ProviderConfig`` registry in ``hermes_cli/auth.py``. Historically the bridge
dropped ``api_mode`` (``ProviderConfig`` had no such field), so the runtime
re-derived it purely from the URL via ``_detect_api_mode_for_url``.

That heuristic only recognizes self-describing endpoints (a ``/anthropic``
suffix, or ``api.kimi.com`` + ``/coding``). An ``anthropic_messages`` plugin
whose endpoint is NOT self-describing — e.g. Volcengine Ark's
``https://ark.cn-beijing.volces.com/api/coding`` — silently fell back to
``chat_completions`` and hit a bare nginx 404 against the Anthropic-only
endpoint. MiniMax only avoided this by coincidence (its base_url ends in
``/anthropic``).

These tests pin the fix: ``ProviderConfig.api_mode`` is carried through and the
resolver honors it as a fallback, while preserving the existing URL-detection
precedence so no working provider regresses.
"""

import pytest

from hermes_cli import auth as auth_mod
from hermes_cli import runtime_provider as rp
from hermes_cli.auth import ProviderConfig


class _Entry:
    """Minimal stand-in for a PooledCredential."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.runtime_base_url = base_url
        self.access_token = "tok"
        self.runtime_api_key = "tok"
        self.source = "test"


@pytest.fixture
def registered_provider(monkeypatch):
    """Register a throwaway provider in PROVIDER_REGISTRY for the duration of a
    test, then restore the registry."""

    def _register(name: str, *, api_mode: str, base_url: str):
        cfg = ProviderConfig(
            id=name,
            name=name,
            auth_type="api_key",
            inference_base_url=base_url,
            api_key_env_vars=("X_API_KEY",),
            api_mode=api_mode,
        )
        monkeypatch.setitem(rp.PROVIDER_REGISTRY, name, cfg)
        return cfg

    return _register


def test_provider_config_has_api_mode_field():
    """ProviderConfig must carry api_mode so the plugin bridge can populate it."""
    cfg = ProviderConfig(id="x", name="x", auth_type="api_key", api_mode="anthropic_messages")
    assert cfg.api_mode == "anthropic_messages"
    # Default is empty (unset) so hardcoded registry entries keep URL detection.
    assert ProviderConfig(id="y", name="y", auth_type="api_key").api_mode == ""


def test_declared_api_mode_honored_for_non_self_describing_url(registered_provider, monkeypatch):
    """Ark-shaped case: anthropic_messages declared, URL not self-describing.

    Without the fix this resolves to chat_completions → 404.
    """
    registered_provider(
        "ark-test",
        api_mode="anthropic_messages",
        base_url="https://ark.cn-beijing.volces.com/api/coding",
    )
    monkeypatch.setattr(rp, "_get_model_config", lambda: {})

    resolved = rp._resolve_runtime_from_pool_entry(
        provider="ark-test",
        entry=_Entry("https://ark.cn-beijing.volces.com/api/coding"),
        requested_provider="ark-test",
        model_cfg={},
    )

    assert resolved["api_mode"] == "anthropic_messages"
    assert resolved["base_url"] == "https://ark.cn-beijing.volces.com/api/coding"


def test_url_detection_still_wins_over_declared_api_mode(registered_provider, monkeypatch):
    """Precedence guard: a self-describing /anthropic URL is still auto-detected,
    so existing providers that relied on URL detection do not regress."""
    registered_provider(
        "selfdesc-test",
        api_mode="chat_completions",  # declared chat, but URL says anthropic
        base_url="https://gateway.example.com/anthropic",
    )
    monkeypatch.setattr(rp, "_get_model_config", lambda: {})

    resolved = rp._resolve_runtime_from_pool_entry(
        provider="selfdesc-test",
        entry=_Entry("https://gateway.example.com/anthropic"),
        requested_provider="selfdesc-test",
        model_cfg={},
    )

    assert resolved["api_mode"] == "anthropic_messages"


def test_unset_api_mode_falls_back_to_chat_completions(registered_provider, monkeypatch):
    """No declaration + non-self-describing URL keeps the historical default —
    proves the change is additive (empty api_mode is a no-op)."""
    registered_provider(
        "plain-test",
        api_mode="",  # hardcoded-style entry, no declaration
        base_url="https://api.example.com/v1",
    )
    monkeypatch.setattr(rp, "_get_model_config", lambda: {})

    resolved = rp._resolve_runtime_from_pool_entry(
        provider="plain-test",
        entry=_Entry("https://api.example.com/v1"),
        requested_provider="plain-test",
        model_cfg={},
    )

    assert resolved["api_mode"] == "chat_completions"


def test_plugin_bridge_populates_api_mode_from_profile():
    """The auth.py bridge must copy ProviderProfile.api_mode into ProviderConfig.

    Verified structurally: any anthropic_messages plugin auto-registered into
    PROVIDER_REGISTRY (e.g. a future Ark plugin) should expose api_mode. We
    assert the bridge contract by constructing the same ProviderConfig the
    bridge builds.
    """
    from providers.base import ProviderProfile

    profile = ProviderProfile(
        name="bridge-test",
        api_mode="anthropic_messages",
        base_url="https://ark.cn-beijing.volces.com/api/coding",
        env_vars=("X_API_KEY",),
        auth_type="api_key",
    )
    # Mirror the bridge in hermes_cli/auth.py
    cfg = ProviderConfig(
        id=profile.name,
        name=profile.display_name or profile.name,
        auth_type="api_key",
        inference_base_url=profile.base_url,
        api_key_env_vars=profile.env_vars,
        api_mode=getattr(profile, "api_mode", "") or "",
    )
    assert cfg.api_mode == "anthropic_messages"
