"""
Smoke tests for the factory-droid built-in skill.

Verifies:
  - SKILL.md frontmatter conforms to the hardline format
  - All reference files are present and parseable
  - Key content sections exist
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "autonomous-ai-agents" / "factory-droid"


@pytest.fixture(scope="module")
def frontmatter() -> dict:
    src = (SKILL_DIR / "SKILL.md").read_text()
    m = re.search(r"^---\n(.*?)\n---", src, re.DOTALL)
    assert m, "SKILL.md missing YAML frontmatter"
    return yaml.safe_load(m.group(1))


def test_skill_dir_exists() -> None:
    assert SKILL_DIR.is_dir(), f"missing skill dir: {SKILL_DIR}"


def test_skill_md_present() -> None:
    assert (SKILL_DIR / "SKILL.md").is_file()


def test_description_under_60_chars(frontmatter) -> None:
    desc = frontmatter["description"]
    assert len(desc) <= 60, f"description is {len(desc)} chars (hardline <=60): {desc!r}"


def test_name_matches_dir(frontmatter) -> None:
    assert frontmatter["name"] == "factory-droid"


def test_platforms_excludes_windows(frontmatter) -> None:
    assert "windows" not in frontmatter["platforms"]
    assert set(frontmatter["platforms"]) >= {"linux", "macos"}


def test_author_present(frontmatter) -> None:
    assert frontmatter.get("author"), "author field must be set"


def test_license_mit(frontmatter) -> None:
    assert frontmatter.get("license") == "MIT"


def test_version_present(frontmatter) -> None:
    assert frontmatter.get("version"), "version field must be set"


def test_metadata_tags_present(frontmatter) -> None:
    tags = frontmatter.get("metadata", {}).get("hermes", {}).get("tags", [])
    assert len(tags) >= 3, f"expected at least 3 tags, got {tags}"
    assert "Coding-Agent" in tags


def test_related_skills_present(frontmatter) -> None:
    related = frontmatter.get("metadata", {}).get("hermes", {}).get("related_skills", [])
    assert len(related) >= 2


def test_prerequisites_section_present() -> None:
    src = (SKILL_DIR / "SKILL.md").read_text()
    assert "## Prerequisites" in src


def test_pitfalls_section_present() -> None:
    src = (SKILL_DIR / "SKILL.md").read_text()
    assert "## Pitfalls" in src


def test_rules_section_present() -> None:
    src = (SKILL_DIR / "SKILL.md").read_text()
    assert "## Rules" in src


def test_reference_files_exist() -> None:
    expected = [
        "authentication.md",
        "autonomy-levels.md",
        "delegation-comparison.md",
        "mission-mode.md",
        "model-ids.md",
    ]
    ref_dir = SKILL_DIR / "references"
    assert ref_dir.is_dir(), "references/ directory missing"
    for fname in expected:
        assert (ref_dir / fname).is_file(), f"missing reference file: {fname}"


@pytest.mark.parametrize(
    "path",
    [
        "references/authentication.md",
        "references/autonomy-levels.md",
        "references/delegation-comparison.md",
        "references/mission-mode.md",
        "references/model-ids.md",
    ],
)
def test_reference_files_parseable(path: str) -> None:
    src = (SKILL_DIR / path).read_text(encoding="utf-8")
    assert len(src) > 50, f"{path} is too short to be meaningful"


def test_autonomy_levels_referenced() -> None:
    src = (SKILL_DIR / "SKILL.md").read_text()
    assert "--auto low" in src
    assert "--auto medium" in src
    assert "--auto high" in src
    assert "--use-spec" in src
    assert "--mission" in src


def test_json_output_mentioned() -> None:
    src = (SKILL_DIR / "SKILL.md").read_text()
    assert "-o json" in src or "--output-format json" in src


def test_verification_command_documented() -> None:
    src = (SKILL_DIR / "SKILL.md").read_text()
    assert "\\\"Respond with: OK\\\"" in src or "Respond with: OK" in src
