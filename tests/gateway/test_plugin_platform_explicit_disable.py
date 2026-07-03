from gateway.config import GatewayConfig, Platform, PlatformConfig, _apply_env_overrides
from gateway.platform_registry import PlatformEntry, platform_registry


def _register_test_plugin(name: str, *, check_fn=None, env_enablement_fn=None) -> None:
    platform_registry.register(
        PlatformEntry(
            name=name,
            label=name.title(),
            adapter_factory=lambda cfg: object(),
            check_fn=check_fn or (lambda: True),
            env_enablement_fn=env_enablement_fn,
            source="plugin",
            plugin_name="test-plugin",
        )
    )


def test_plugin_platform_auto_enable_respects_explicit_false(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    name = "test-explicit-disabled"
    _register_test_plugin(name)
    platform = Platform(name)
    config = GatewayConfig(platforms={platform: PlatformConfig(enabled=False)})

    _apply_env_overrides(config)

    assert config.platforms[platform].enabled is False


def test_plugin_platform_auto_enable_still_enables_when_not_explicitly_disabled(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    name = "test-auto-enabled"
    _register_test_plugin(name)
    platform = Platform(name)
    config = GatewayConfig()

    _apply_env_overrides(config)

    assert config.platforms[platform].enabled is True
