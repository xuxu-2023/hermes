"""Regression: install.sh must only warn about a fallback dependency tier
when one was actually used.

The post-install warning guarded on ``_tier_name != "all (with RL/matrix
extras)"``, but ``_tier_name`` is only ever ``"all"``, ``"all minus
known-broken (...)"``, or ``"core only (no extras)"`` (the three
``install_tier`` names). The sentinel ``"all (with RL/matrix extras)"`` is a
stale tier name from before the 2026-05-12 lazy-install migration and is never
produced, so the condition was always true and a fully successful ``.[all]``
install printed a false ``installed via fallback tier (all). Some optional
features may be missing.`` warning. The fix compares against the real primary
tier name ``"all"``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def test_dead_sentinel_gone_and_guard_uses_primary_tier_name() -> None:
    """Static guard: the stale sentinel is gone, the guard compares to "all"."""
    text = INSTALL_SH.read_text()
    assert "all (with RL/matrix extras)" not in text, (
        "the stale fallback-tier sentinel must be removed — _tier_name is "
        'never that string, so the guard was always true'
    )
    assert 'if [ "$_tier_name" != "all" ]; then' in text, (
        "the fallback-tier warning must guard on the real primary tier name "
        '"all"'
    )


def _extract_fallback_warning_block() -> str:
    """Return the post-install fallback-tier warning block from install.sh."""
    text = INSTALL_SH.read_text()
    # Match the guard regardless of the compared value so the behavioral test
    # reproduces the real bug: before the fix the block warns even for "all".
    match = re.search(
        r'(?P<block>if \[ "\$_tier_name" != ".*?" \]; then.*?\n    fi)',
        text,
        re.DOTALL,
    )
    assert match is not None, (
        "Could not locate the fallback-tier warning block in scripts/install.sh"
    )
    return match["block"]


def _run_block(tier_name: str) -> str:
    """Run the warning block under bash with log_* stubbed, return combined output."""
    block = _extract_fallback_warning_block()
    script = (
        "set -e\n"
        'log_warn() { echo "WARN: $*"; }\n'
        'log_info() { echo "INFO: $*"; }\n'
        'log_error() { echo "ERROR: $*"; }\n'
        'log_success() { echo "OK: $*"; }\n'
        "_installed=true\n"
        f'_tier_name="{tier_name}"\n'
        'UV_CMD="uv"\n'
        f"{block}\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"warning block failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    return result.stdout + result.stderr


def test_no_warning_for_primary_all_tier() -> None:
    """A successful primary `.[all]` install must NOT print the fallback warning."""
    out = _run_block("all")
    assert "installed via fallback tier" not in out, (
        "the fallback-tier warning must be suppressed when the primary "
        '"all" tier succeeded'
    )


def test_warning_for_fallback_tier() -> None:
    """A genuine fallback tier must still print the warning."""
    out = _run_block("core only (no extras)")
    assert "installed via fallback tier" in out, (
        "the warning must still fire for a real fallback tier"
    )
