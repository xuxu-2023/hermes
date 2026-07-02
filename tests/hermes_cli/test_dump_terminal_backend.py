"""`hermes debug` must report the EFFECTIVE terminal backend.

``terminal.backend`` in config.yaml is bridged to the ``TERMINAL_ENV`` env var,
but a ``TERMINAL_ENV`` set in .env / the shell overrides config and is what
``terminal_tool`` actually uses.  The dump used to print only the config value,
which hid the override and made users believe the agent was running ``local``
while it was really jailed in a docker/podman sandbox (and vice-versa).
"""

from pathlib import Path
from types import SimpleNamespace


def _terminal_line(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("terminal:"):
            return line
    raise AssertionError(f"no 'terminal:' line in dump output:\n{out}")


def _provider_line(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("provider:"):
            return line
    raise AssertionError(f"no 'provider:' line in dump output:\n{out}")


def _seed(home: Path, *, config_yaml: str, env_text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(config_yaml)
    (home / ".env").write_text(env_text)


def test_dump_surfaces_terminal_env_override(monkeypatch, capsys, tmp_path):
    from hermes_cli import dump
    from hermes_cli.config import get_hermes_home

    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    # Keep run_dump's project-.env fallback from touching the real repo.
    monkeypatch.setattr(dump, "get_project_root", lambda: tmp_path / "noproject")

    home = get_hermes_home()
    _seed(home, config_yaml="terminal:\n  backend: local\n", env_text="TERMINAL_ENV=docker\n")

    dump.run_dump(SimpleNamespace(show_keys=False))

    line = _terminal_line(capsys.readouterr().out)
    # Effective backend (docker) is what actually runs, not the config 'local'.
    assert "docker" in line
    assert "overrides config.yaml" in line
    # The shadowed config value is still shown so the mismatch is obvious.
    assert "terminal.backend=local" in line


def test_dump_reports_config_backend_when_no_override(monkeypatch, capsys, tmp_path):
    from hermes_cli import dump
    from hermes_cli.config import get_hermes_home

    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    monkeypatch.setattr(dump, "get_project_root", lambda: tmp_path / "noproject")

    home = get_hermes_home()
    _seed(home, config_yaml="terminal:\n  backend: docker\n", env_text="")

    dump.run_dump(SimpleNamespace(show_keys=False))

    line = _terminal_line(capsys.readouterr().out)
    assert "docker" in line
    assert "overrides" not in line


def test_dump_surfaces_inference_provider_env_override(monkeypatch, capsys, tmp_path):
    """When config sets no model.provider, the dump must surface the effective
    HERMES_INFERENCE_PROVIDER override instead of the bare "(auto)" sentinel —
    that env value is what the agent actually resolves to."""
    from hermes_cli import dump
    from hermes_cli.config import get_hermes_home

    monkeypatch.delenv("HERMES_INFERENCE_PROVIDER", raising=False)
    monkeypatch.setattr(dump, "get_project_root", lambda: tmp_path / "noproject")

    home = get_hermes_home()
    # No model.provider in config; only the env override is set.
    _seed(
        home,
        config_yaml="model:\n  default: hermes-4-405b\n",
        env_text="HERMES_INFERENCE_PROVIDER=nous\n",
    )

    dump.run_dump(SimpleNamespace(show_keys=False))

    line = _provider_line(capsys.readouterr().out)
    assert "nous" in line
    assert "(auto)" not in line
    assert "HERMES_INFERENCE_PROVIDER" in line


def test_dump_provider_config_wins_over_env(monkeypatch, capsys, tmp_path):
    """Guard rail: config.model.provider wins over HERMES_INFERENCE_PROVIDER
    (resolve_requested_provider precedence). The env override must NOT shadow a
    provider explicitly set in config.yaml."""
    from hermes_cli import dump
    from hermes_cli.config import get_hermes_home

    monkeypatch.delenv("HERMES_INFERENCE_PROVIDER", raising=False)
    monkeypatch.setattr(dump, "get_project_root", lambda: tmp_path / "noproject")

    home = get_hermes_home()
    _seed(
        home,
        config_yaml="model:\n  default: hermes-4-405b\n  provider: openrouter\n",
        env_text="HERMES_INFERENCE_PROVIDER=nous\n",
    )

    dump.run_dump(SimpleNamespace(show_keys=False))

    line = _provider_line(capsys.readouterr().out)
    assert "openrouter" in line
    assert "nous" not in line
    assert "HERMES_INFERENCE_PROVIDER" not in line


def test_dump_no_override_when_env_matches_config(monkeypatch, capsys, tmp_path):
    from hermes_cli import dump
    from hermes_cli.config import get_hermes_home

    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    monkeypatch.setattr(dump, "get_project_root", lambda: tmp_path / "noproject")

    home = get_hermes_home()
    # TERMINAL_ENV agrees with config — no spurious "override" note.
    _seed(home, config_yaml="terminal:\n  backend: docker\n", env_text="TERMINAL_ENV=docker\n")

    dump.run_dump(SimpleNamespace(show_keys=False))

    line = _terminal_line(capsys.readouterr().out)
    assert "docker" in line
    assert "overrides" not in line
