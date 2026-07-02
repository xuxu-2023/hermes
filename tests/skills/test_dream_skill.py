"""Tests for optional-skills/autonomous-ai-agents/dream/SKILL.md.

The skill wraps an external single-file tool, so these tests enforce the
skill contract itself, hermetically: frontmatter standards, the modern
section order, and internal consistency between the declared version, the
pinned install URL, and the integrity checksum (a drifting pin is exactly
the failure mode a wrapper skill can silently develop).
"""
import re
from pathlib import Path

SKILL = (
    Path(__file__).resolve().parents[2]
    / "optional-skills"
    / "autonomous-ai-agents"
    / "dream"
    / "SKILL.md"
)
TEXT = SKILL.read_text(encoding="utf-8")
FRONT = TEXT.split("---")[1]


def _front_value(key):
    m = re.search(r"^%s: (.+)$" % key, FRONT, re.MULTILINE)
    return m.group(1).strip() if m else None


class TestFrontmatter:
    def test_description_within_60_chars_one_sentence(self):
        desc = _front_value("description")
        assert desc is not None
        assert len(desc) <= 60, len(desc)
        assert desc.endswith(".")
        assert desc.count(".") == 1

    def test_description_has_no_marketing_words(self):
        desc = _front_value("description").lower()
        for banned in ("powerful", "comprehensive", "seamless", "advanced"):
            assert banned not in desc

    def test_platforms_declared(self):
        assert _front_value("platforms") == "[linux, macos, windows]"

    def test_prerequisite_commands_declared(self):
        assert "commands: [python3, curl]" in FRONT


class TestSectionOrder:
    def test_modern_section_order(self):
        wanted = ["## When to Use", "## Prerequisites", "## How to Run",
                  "## Quick Reference", "## Procedure", "## Pitfalls",
                  "## Verification"]
        positions = [TEXT.find(h) for h in wanted]
        assert all(p != -1 for p in positions), positions
        assert positions == sorted(positions), "sections out of order"

    def test_title_is_skill_form(self):
        assert "\n# dream Skill\n" in TEXT


class TestInstallPinConsistency:
    def test_version_matches_pinned_url(self):
        version = _front_value("version")
        pinned = re.findall(r"raw\.githubusercontent\.com/Da7-Tech/dream/v([\d.]+)/dream\.py", TEXT)
        assert pinned, "install must pin a release tag, not a branch"
        assert all(v == version for v in pinned), (version, pinned)

    def test_no_install_from_moving_branch(self):
        assert "/dream/main/dream.py" not in TEXT

    def test_integrity_checksum_present_and_wellformed(self):
        m = re.search(r"([0-9a-f]{64})  dream\.py", TEXT)
        assert m, "install must include a sha256 integrity check"

    def test_terminal_tool_framing(self):
        assert "`terminal`" in TEXT
