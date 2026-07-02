"""Regression tests for the bundled Hermes safe-update skill.

The safe-update route is a procedural contract, not an executable CLI command:
Nick uses short natural-language messages, and the agent must infer the correct
Hermes or macOS preflight/postflight phase while preserving the operator
boundary. These tests pin that contract in the shipped SKILL.md so future edits
do not regress it into exact-phrase-only prompts or agent-run updates/restarts.
"""

from __future__ import annotations

from pathlib import Path

from agent.skill_utils import parse_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD = REPO_ROOT / "skills" / "autonomous-ai-agents" / "hermes-safe-update" / "SKILL.md"
HERMES_AGENT_SKILL_MD = REPO_ROOT / "skills" / "autonomous-ai-agents" / "hermes-agent" / "SKILL.md"
GENERATED_DOC = REPO_ROOT / "website" / "docs" / "user-guide" / "skills" / "bundled" / "autonomous-ai-agents" / "autonomous-ai-agents-hermes-safe-update.md"
SKILLS_CATALOG = REPO_ROOT / "website" / "docs" / "reference" / "skills-catalog.md"
SIDEBAR = REPO_ROOT / "website" / "sidebars.ts"


def _skill() -> tuple[dict, str, str]:
    content = SKILL_MD.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)
    return frontmatter, body, content


def test_safe_update_skill_exists_with_trigger_description():
    frontmatter, body, content = _skill()

    assert frontmatter["name"] == "hermes-safe-update"
    assert frontmatter["description"].startswith("Use when")
    assert len(frontmatter["description"]) <= 1024
    assert "Hermes update" in frontmatter["description"]
    assert "macOS" in frontmatter["description"]
    assert body.strip()
    assert len(content) <= 100_000


def test_semantic_short_triggers_are_pinned_for_both_routes():
    _frontmatter, body, _content = _skill()

    required_pairs = {
        "I'm going to update macOS": "Mac Host Update preflight",
        "macOS update is done": "Mac Host Update postflight",
        "I'm going to update Hermes": "Hermes Safe Update pre-update readiness",
        "Hermes update is done": "Hermes Safe Update post-update reconciliation",
    }
    for trigger, route in required_pairs.items():
        assert trigger in body
        assert route in body

    assert "semantic, not literal" in body
    assert "optional guardrails" in body
    assert "not required incantations" in body


def test_operator_boundary_for_updates_reboots_gateway_and_credentials():
    _frontmatter, body, _content = _skill()

    must_keep = [
        "Nick is the operator",
        "Do not run `softwareupdate --install`",
        "Do not click the macOS update UI",
        "Do not reboot",
        "Do not run `hermes update`",
        "Do not restart the Hermes gateway",
        "Do not print or mutate credentials",
    ]
    for phrase in must_keep:
        assert phrase in body


def test_preflight_sections_include_copy_paste_postflight_prompts():
    _frontmatter, body, _content = _skill()

    assert "After a successful Mac preflight" in body
    assert "Ares, macOS update is done." in body
    assert "After a successful Hermes pre-update readiness" in body
    assert "Ares, Hermes update is done." in body


def test_hermes_agent_skill_cross_links_safe_update_route():
    content = HERMES_AGENT_SKILL_MD.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    related = frontmatter.get("metadata", {}).get("hermes", {}).get("related_skills", [])
    assert "hermes-safe-update" in related
    assert 'skill_view(name="hermes-safe-update")' in body
    assert "Mac Host Update" in body
    assert "Nick remains the only operator" in body


def test_generated_docs_catalog_and_sidebar_include_safe_update():
    generated = GENERATED_DOC.read_text(encoding="utf-8")
    catalog = SKILLS_CATALOG.read_text(encoding="utf-8")
    sidebar = SIDEBAR.read_text(encoding="utf-8")

    assert "title: \"Hermes Safe Update\"" in generated
    assert "Mac Host Update preflight" in generated
    assert "autonomous-ai-agents-hermes-safe-update" in catalog
    assert "autonomous-ai-agents-hermes-safe-update" in sidebar


def test_verification_language_distinguishes_canonical_from_ad_hoc():
    _frontmatter, body, _content = _skill()

    assert "ad-hoc verification" in body
    assert "Do not claim a canonical suite passed unless it actually ran and passed" in body
    assert "focused verification" in body
