"""Regression coverage for desktop installer npm tree self-repair.

A partially corrupted ``node_modules`` tree can make npm fail with ENOTEMPTY
while renaming a package directory to a hidden ``node_modules/.<pkg>-*`` staging
name. When this happened in the desktop stage, users only saw a generic
"install didn't finish" / exit-code-1 failure. The installer should recognize
that corruption class, clear generated desktop dependency/build artifacts, and
retry once with actionable logs.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def _install_script() -> str:
    return INSTALL_SH.read_text()


def _extract_function_body(name: str) -> str:
    text = _install_script()
    match = re.search(
        rf"^{re.escape(name)}\(\)\s*\{{\s*\n(?P<body>.*?)^\}}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{name}() not found in scripts/install.sh"
    return match["body"]


def test_desktop_npm_install_output_is_logged_for_diagnosis() -> None:
    body = _extract_function_body("_desktop_run_logged")

    assert 'tee -a "$log_file"' in body
    assert "PIPESTATUS[0]" in body
    assert 'set +e' in body
    assert 'set -e' in body


def test_desktop_npm_corruption_detector_matches_enotempty_rename_class() -> None:
    body = _extract_function_body("_desktop_npm_log_has_tree_corruption")

    assert "ENOTEMPTY" in body
    assert "directory not empty, rename" in body
    assert "node_modules" in body
    assert "grep -Eiq" in body


def test_desktop_npm_repair_purges_only_generated_artifacts() -> None:
    body = _extract_function_body("_purge_desktop_npm_artifacts")

    assert '"$install_dir/node_modules"' in body
    assert '"$desktop_dir/node_modules"' in body
    assert '"$desktop_dir/dist"' in body
    assert '"$desktop_dir/release"' in body
    assert "package-lock" not in body
    assert "config.yaml" not in body
    assert ".env" not in body


def test_install_desktop_retries_after_detected_tree_corruption() -> None:
    body = _extract_function_body("install_desktop")

    first_attempt = body.find('_desktop_workspace_npm_install_attempt "$INSTALL_DIR"')
    detector = body.find('_desktop_npm_log_has_tree_corruption "$_deps_log"')
    purge = body.find('_purge_desktop_npm_artifacts "$INSTALL_DIR"')
    retry = body.find(
        '_desktop_workspace_npm_install_attempt "$INSTALL_DIR"',
        first_attempt + 1,
    )

    assert first_attempt != -1, "install_desktop must run the initial npm install attempt"
    assert detector != -1, "install_desktop must detect npm tree corruption from the captured log"
    assert purge != -1, "install_desktop must purge generated artifacts before retrying"
    assert retry != -1, "install_desktop must retry npm install after purging generated artifacts"
    assert first_attempt < detector < purge < retry


def test_install_desktop_reports_actionable_manual_repair_for_persistent_corruption() -> None:
    body = _extract_function_body("install_desktop")

    assert "corrupted node_modules rename" in body
    assert "already removed generated desktop dependency/build artifacts and retried once" in body
    assert 'rm -rf \\"$INSTALL_DIR/node_modules\\"' in body
    assert '\\"$desktop_dir/node_modules\\"' in body
