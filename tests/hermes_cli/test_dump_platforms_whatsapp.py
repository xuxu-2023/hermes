"""`hermes dump` must report the EFFECTIVE WhatsApp platform state.

``WHATSAPP_ENABLED`` is a boolean toggle, not a credential.  The runtime
(``gateway/config.py``) enables WhatsApp only when ``WHATSAPP_ENABLED.lower()``
is in ``{"true", "1", "yes"}`` and treats ``false`` / ``0`` / ``no`` as an
explicit disable.  ``_configured_platforms`` used a bare presence check, so an
explicit ``WHATSAPP_ENABLED=false`` (a documented, runtime-honored disable
value) was still listed under ``platforms:`` — the opposite of the effective
state.
"""

from pathlib import Path
from types import SimpleNamespace

# Env vars that, if set in the ambient environment, would add other platforms
# to the dump output and confuse the assertions below.
_PLATFORM_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "WHATSAPP_ENABLED",
    "SIGNAL_HTTP_URL",
    "EMAIL_ADDRESS",
    "TWILIO_ACCOUNT_SID",
    "MATRIX_HOMESERVER_URL",
    "MATTERMOST_URL",
    "HASS_TOKEN",
    "DINGTALK_CLIENT_ID",
    "FEISHU_APP_ID",
    "WECOM_BOT_ID",
    "WECOM_CALLBACK_CORP_ID",
    "WEIXIN_ACCOUNT_ID",
    "QQ_APP_ID",
)


def _platforms_line(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("  platforms:"):
            return line
    raise AssertionError(f"no 'platforms:' line in dump output:\n{out}")


def _isolate(monkeypatch, home: Path, _tmp_path: Path) -> None:
    """Clear ambient platform env and stop .env fallbacks from leaking in."""
    for var in _PLATFORM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    home.mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text("")


def test_whatsapp_disabled_not_listed(monkeypatch, capsys, tmp_path):
    from hermes_cli import dump
    from hermes_cli.config import get_hermes_home

    monkeypatch.setattr(dump, "get_project_root", lambda: tmp_path / "noproject")
    _isolate(monkeypatch, get_hermes_home(), tmp_path)
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")

    dump.run_dump(SimpleNamespace(show_keys=False))

    line = _platforms_line(capsys.readouterr().out)
    assert "whatsapp" not in line


def test_whatsapp_enabled_listed(monkeypatch, capsys, tmp_path):
    from hermes_cli import dump
    from hermes_cli.config import get_hermes_home

    monkeypatch.setattr(dump, "get_project_root", lambda: tmp_path / "noproject")
    _isolate(monkeypatch, get_hermes_home(), tmp_path)
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")

    dump.run_dump(SimpleNamespace(show_keys=False))

    line = _platforms_line(capsys.readouterr().out)
    assert "whatsapp" in line


def test_whatsapp_zero_not_listed(monkeypatch, capsys, tmp_path):
    from hermes_cli import dump
    from hermes_cli.config import get_hermes_home

    monkeypatch.setattr(dump, "get_project_root", lambda: tmp_path / "noproject")
    _isolate(monkeypatch, get_hermes_home(), tmp_path)
    monkeypatch.setenv("WHATSAPP_ENABLED", "0")

    dump.run_dump(SimpleNamespace(show_keys=False))

    line = _platforms_line(capsys.readouterr().out)
    assert "whatsapp" not in line


def test_whatsapp_padded_true_not_listed(monkeypatch, capsys, tmp_path):
    # Runtime-mirror: gateway/config.py does NOT .strip() WHATSAPP_ENABLED, so
    # a padded " true " is disabled at runtime. The dump must report the same
    # effective state — it must NOT strip and over-report it as configured.
    from hermes_cli import dump
    from hermes_cli.config import get_hermes_home

    monkeypatch.setattr(dump, "get_project_root", lambda: tmp_path / "noproject")
    _isolate(monkeypatch, get_hermes_home(), tmp_path)
    monkeypatch.setenv("WHATSAPP_ENABLED", " true ")

    dump.run_dump(SimpleNamespace(show_keys=False))

    line = _platforms_line(capsys.readouterr().out)
    assert "whatsapp" not in line
