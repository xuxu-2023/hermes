"""Tests for the Teams DLP redaction engine."""

from __future__ import annotations

from plugins.platforms.teams.dlp import DlpConfig, redact

ON = DlpConfig(enabled=True)


def test_disabled_is_noop():
    cfg = DlpConfig(enabled=False)
    out, n = redact("my key is sk-proj-abcdefghijklmnop and a@b.com", cfg)
    assert n == 0 and "sk-proj-" in out and "a@b.com" in out


def test_redacts_email():
    out, n = redact("contact me at alaa.h@example.com please", ON)
    assert "alaa.h@example.com" not in out
    assert "[REDACTED:email]" in out and n == 1


def test_redacts_openai_anthropic_keys():
    out, n = redact("key sk-proj-ABCDEFGH12345678ZZ and sk-ant-api03-aaaaaaaaaa", ON)
    assert "sk-proj-" not in out and "sk-ant-" not in out
    assert out.count("[REDACTED:secret]") == 2 and n == 2


def test_redacts_aws_and_github_and_jwt():
    out, _ = redact("AKIAIOSFODNN7EXAMPLE ghp_abcdefghijklmnopqrstuvwxyz0123", ON)
    assert "AKIA" not in out and "ghp_" not in out


def test_redacts_assignment_value_keeps_key():
    out, n = redact("password = hunter2supersecret", ON)
    assert "hunter2supersecret" not in out
    assert "password" in out and "[REDACTED:secret]" in out and n == 1


def test_custom_pattern():
    cfg = DlpConfig(enabled=True, custom_patterns=(r"PROJ-\d{4}",))
    out, n = redact("ticket PROJ-1234 is internal", cfg)
    assert "PROJ-1234" not in out and "[REDACTED:custom]" in out and n == 1


def test_malformed_custom_pattern_skipped():
    cfg = DlpConfig(enabled=True, custom_patterns=(r"[unclosed",))
    out, n = redact("nothing to redact here", cfg)  # must not raise
    assert out == "nothing to redact here" and n == 0


def test_category_selection_email_only():
    cfg = DlpConfig(enabled=True, categories=("email",))
    out, _ = redact("sk-proj-ABCDEFGH12345678 and a@b.com", cfg)
    assert "[REDACTED:email]" in out
    assert "sk-proj-" in out  # secret category off → key untouched


def test_from_dict_camel_and_snake():
    cfg = DlpConfig.from_dict({"enabled": True, "customPatterns": [r"X-\d+"]})
    assert cfg.enabled and cfg.custom_patterns == (r"X-\d+",)
