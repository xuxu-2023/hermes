#!/usr/bin/env python3
"""
Skill Curator (skill-curator)
Scans skills, identifies similar groups, generates merge plans.
Reviews new skills for integration decisions.

Usage:
    python3 consolidate.py --scan                    # Scan for similar skills
    python3 consolidate.py --plan <group>             # Generate merge plan for a group
    python3 consolidate.py --execute <group>          # Execute merge (requires approval)
    python3 consolidate.py --health                   # Skill health report
    python3 consolidate.py --rollback <suite_name>    # Rollback a merge
    python3 consolidate.py --review <path>            # Review a new skill for integration
    python3 consolidate.py --review <path> --auto     # Auto-apply best action
"""

import os
import sys
import json
import re
import shutil
import hashlib
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# ── Paths ──────────────────────────────────────────────────────────────

def _get_skills_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        return Path(hermes_home) / "skills"
    fallback = Path.home() / ".hermes" / "profiles" / "coder" / "skills"
    if fallback.exists():
        return fallback
    return Path.home() / ".hermes" / "skills"


SKILLS_DIR = _get_skills_dir()
if not SKILLS_DIR.exists():
    SKILLS_DIR = Path.home() / ".hermes" / "profiles" / "coder" / "skills"

ARCHIVE_DIR = SKILLS_DIR / ".archive"
CONFIG_FILE = SKILLS_DIR / ".skill-curator-config.yaml"
HISTORY_FILE = SKILLS_DIR / "skill-curator" / "references" / "merge-history.md"

# ── Config ─────────────────────────────────────────────────────────────

def load_config():
    """Load skill-curator config (simple YAML parser for our subset)."""
    config = {
        "locked": [],
        "locked_categories": [],
        "rules": {
            "require_approval": True,
            "keep_originals_in_archive": True,
            "max_merge_candidates": 5,
            "preserve_pitfalls": True,
        }
    }
    if not CONFIG_FILE.exists():
        return config

    with open(CONFIG_FILE) as f:
        lines = f.readlines()

    section = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped == "locked:":
            section = "locked"
            continue
        elif stripped == "locked_categories:":
            section = "locked_categories"
            continue
        elif stripped == "rules:":
            section = "rules"
            continue

        if section in ("locked", "locked_categories"):
            if stripped.startswith("- "):
                config[section].append(stripped[2:].strip())
        elif section == "rules":
            if ":" in stripped:
                key, val = stripped.split(":", 1)
                key = key.strip()
                val = val.strip()
                if val.lower() in ("true", "false"):
                    config["rules"][key] = val.lower() == "true"
                elif val.isdigit():
                    config["rules"][key] = int(val)
                else:
                    config["rules"][key] = val

    return config


def is_locked(skill_name, config):
    """Check if a skill is locked."""
    if skill_name in config["locked"]:
        return True
    # Check category lock (scan SKILL.md for category)
    skill_md = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_md.exists():
        skill_md = ARCHIVE_DIR / skill_name / "SKILL.md"
    if skill_md.exists():
        with open(skill_md) as f:
            content = f.read()
        match = re.search(r'category:\s*(\S+)', content)
        if match and match.group(1) in config["locked_categories"]:
            return True
    return False


# ── Skill Scanning ────────────────────────────────────────────────────

def scan_skills():
    """Scan all skills (active + archived)."""
    skills = {}

    for base_dir in [SKILLS_DIR, ARCHIVE_DIR]:
        if not base_dir.exists():
            continue
        for entry in sorted(base_dir.iterdir()):
            if entry.name.startswith('.') or not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                # Might be a category directory
                for sub in entry.iterdir():
                    sub_md = sub / "SKILL.md"
                    if sub_md.exists() and sub.is_dir():
                        skills[sub.name] = parse_skill(sub_md, sub, str(base_dir))
                continue
            skills[entry.name] = parse_skill(skill_md, entry, str(base_dir))

    return skills


def parse_skill(skill_md, skill_dir, base_dir):
    """Parse a SKILL.md file."""
    with open(skill_md) as f:
        content = f.read()

    info = {
        "name": skill_dir.name,
        "path": str(skill_dir),
        "base": "active" if base_dir == str(SKILLS_DIR) else "archived",
        "description": "",
        "category": "",
        "triggers": [],
        "size": 0,
        "references": [],
        "content_hash": hashlib.md5(content.encode()).hexdigest()[:8],
        "pitfalls": [],
    }

    # Extract frontmatter fields
    for line in content.split('\n')[:15]:
        if line.startswith('description:'):
            info["description"] = line.split(':', 1)[1].strip().strip('"').strip("'")
        elif line.startswith('category:'):
            info["category"] = line.split(':', 1)[1].strip()
        elif line.startswith('triggers:'):
            info["triggers"] = [t.strip() for t in line.split(':', 1)[1].split(',')]
        elif line.startswith('name:'):
            info["name"] = line.split(':', 1)[1].strip()

    # Extract keywords from description for similarity
    info["keywords"] = extract_keywords(info["description"] + " " + content[:500])

    # Find pitfall sections
    info["pitfalls"] = extract_pitfalls(content)

    # Scan references
    refs_dir = skill_dir / "references"
    if refs_dir.exists():
        for ref in refs_dir.rglob("*.md"):
            info["references"].append(str(ref.relative_to(skill_dir)))

    # Calculate size
    info["size"] = sum(f.stat().st_size for f in skill_dir.rglob("*") if f.is_file())

    return info


def extract_keywords(text):
    """Extract meaningful keywords from text."""
    # Remove common stop words
    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'shall', 'can', 'need', 'dare', 'ought',
        'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
        'as', 'into', 'through', 'during', 'before', 'after', 'above',
        'below', 'between', 'under', 'again', 'further', 'then', 'once',
        'here', 'there', 'when', 'where', 'why', 'how', 'all', 'each',
        'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
        'only', 'own', 'same', 'so', 'than', 'too', 'very', 's', 't', 'just',
        'don', 'now', 'use', 'use when', 'this', 'that', 'these', 'those',
        'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'you',
        'your', 'yours', 'yourself', 'yourselves', 'he', 'him', 'his',
        'himself', 'she', 'her', 'hers', 'herself', 'it', 'its', 'itself',
        'they', 'them', 'their', 'theirs', 'themselves', 'what', 'which',
        'who', 'whom', 'about', 'against', 'and', 'because', 'but', 'or',
        'if', 'while', 'until', 'up', 'down', 'out', 'off', 'over',
    }

    words = re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', text.lower())
    return set(w for w in words if w not in stop_words and len(w) > 2)


def extract_pitfalls(content):
    """Extract pitfall/warning sections from content."""
    pitfalls = []
    # Look for common pitfall markers
    patterns = [
        r'(?:⚠️|⚠|WARNING|CAUTION|PITFALL|陷阱|注意|坑)[:\s]*(.+?)(?=\n\n|\n#|\n##|$)',
        r'(?:pitfall|trap|gotcha|edge case)[:\s]*(.+?)(?=\n\n|\n#|\n##|$)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
        pitfalls.extend(matches)
    return pitfalls[:10]  # Cap at 10


# ── Similarity Detection ──────────────────────────────────────────────

def compute_similarity(s1, s2):
    """Compute similarity score between two skills."""
    # Jaccard similarity on keywords
    kw1 = s1["keywords"]
    kw2 = s2["keywords"]
    if not kw1 or not kw2:
        return 0.0

    intersection = kw1 & kw2
    union = kw1 | kw2
    jaccard = len(intersection) / len(union) if union else 0

    # Category bonus
    cat_bonus = 0.1 if s1["category"] and s1["category"] == s2["category"] else 0

    # Name overlap bonus
    name_bonus = 0.05 if any(w in s2["name"] for w in s1["name"].split('-')) else 0

    score = jaccard + cat_bonus + name_bonus
    return min(score, 1.0)


def find_similar_groups(skills, threshold=0.15):
    """Find groups of similar skills."""
    skill_list = list(skills.values())
    groups = defaultdict(list)
    processed = set()

    for i, s1 in enumerate(skill_list):
        if s1["name"] in processed:
            continue

        group = [s1["name"]]
        for j, s2 in enumerate(skill_list):
            if i == j or s2["name"] in processed:
                continue

            score = compute_similarity(s1, s2)
            if score >= threshold:
                group.append(s2["name"])
                processed.add(s2["name"])

        if len(group) >= 2:
            processed.add(s1["name"])
            # Use first skill's dominant keyword theme as group name
            group_skills = [skills[n] for n in group if n in skills]
            if group_skills:
                group_key = identify_group_theme(group_skills)
                groups[group_key] = group

    return dict(groups)


def identify_group_theme(skills_in_group):
    """Identify the dominant theme of a skill group."""
    # Count keyword frequencies
    freq = defaultdict(int)
    for s in skills_in_group:
        for kw in s["keywords"]:
            freq[kw] += 1

    # Sort by frequency
    top_keywords = sorted(freq.items(), key=lambda x: -x[1])[:5]

    # Create group name from top keywords
    theme_words = []
    for kw, count in top_keywords:
        if len(kw) > 3 and count >= 2:
            theme_words.append(kw)
        if len(theme_words) >= 2:
            break

    if theme_words:
        return "_".join(theme_words[:2])

    # Fallback: use first skill's name prefix
    return skills_in_group[0]["name"].split("-")[0] if skills_in_group else "unknown"


# ── Merge Planning ────────────────────────────────────────────────────

def generate_merge_plan(group_name, skills, config):
    """Generate a merge plan for a skill group."""
    group_skills = [skills[n] for n in group_name if n in skills]

    if not group_skills:
        print(f"❌ Group '{group_name}' not found or empty")
        return None

    # Filter out locked skills
    unlocked = [s for s in group_skills if not is_locked(s["name"], config)]
    locked = [s for s in group_skills if is_locked(s["name"], config)]

    if not unlocked:
        print(f"⚠️ All skills in '{group_name}' are locked. No merge possible.")
        return None

    # Generate suite name
    theme = identify_group_theme(unlocked)
    suite_name = f"{theme}-suite"

    # Build plan
    plan = {
        "suite_name": suite_name,
        "source_skills": [s["name"] for s in unlocked],
        "locked_skills": [s["name"] for s in locked],
        "total_size": sum(s["size"] for s in unlocked),
        "estimated_savings": calculate_savings(unlocked),
        "modules": [],
        "pitfalls_preserved": [],
    }

    for s in unlocked:
        module = {
            "name": s["name"].replace("-", "_"),
            "source": s["name"],
            "file": f"references/{s['name'].replace('-', '_')}.md",
            "triggers": list(s["keywords"])[:10],
            "standalone": True,
            "size": s["size"],
            "references": s["references"],
        }
        plan["modules"].append(module)

        # Collect pitfalls
        if s["pitfalls"]:
            plan["pitfalls_preserved"].append({
                "source": s["name"],
                "count": len(s["pitfalls"]),
            })

    return plan


def calculate_savings(skills):
    """Estimate token savings from merging."""
    total = sum(s["size"] for s in skills)
    # Estimate 30-40% reduction from removing duplicates
    estimated = total * 0.35
    return int(estimated)


# ── Merge Execution ───────────────────────────────────────────────────

def execute_merge(plan, skills, config):
    """Execute a merge plan."""
    if not config["rules"]["require_approval"]:
        print("⚠️ Approval bypassed (not recommended)")
    else:
        print("\n📋 Merge Plan Review:")
        print(f"  Suite: {plan['suite_name']}")
        print(f"  Sources: {', '.join(plan['source_skills'])}")
        if plan['locked_skills']:
            print(f"  Locked (skipped): {', '.join(plan['locked_skills'])}")
        print(f"  Modules: {len(plan['modules'])}")
        print(f"  Pitfalls preserved: {sum(p['count'] for p in plan['pitfalls_preserved'])} from {len(plan['pitfalls_preserved'])} skills")
        print(f"  Estimated savings: {plan['estimated_savings']:,} bytes")

        response = input("\n✅ Execute this merge? (yes/no): ").strip().lower()
        if response != "yes":
            print("❌ Merge cancelled.")
            return False

    # Create suite directory
    suite_dir = SKILLS_DIR / plan["suite_name"]
    refs_dir = suite_dir / "references"

    if suite_dir.exists():
        print(f"⚠️ {plan['suite_name']} already exists, updating...")

    suite_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(exist_ok=True)

    # Generate modules.json
    modules_data = {
        "skill": plan["suite_name"],
        "source_skills": plan["source_skills"],
        "locked_skills": plan.get("locked_skills", []),
        "created": datetime.now().isoformat(),
        "modules": {m["name"]: {k: v for k, v in m.items() if k != "references"} for m in plan["modules"]},
    }

    with open(suite_dir / "modules.json", "w") as f:
        json.dump(modules_data, f, indent=2, ensure_ascii=False)

    # Generate main SKILL.md
    skill_md_content = generate_suite_skillmd(plan, skills)
    with open(suite_dir / "SKILL.md", "w") as f:
        f.write(skill_md_content)

    # Copy reference files
    for module in plan["modules"]:
        source_name = module["source"]
        target_file = refs_dir / module["file"].split("/")[-1]

        # Find source
        source_dir = SKILLS_DIR / source_name
        if not source_dir.exists():
            source_dir = ARCHIVE_DIR / source_name

        if source_dir.exists():
            # Copy SKILL.md content
            src_md = source_dir / "SKILL.md"
            if src_md.exists() and not target_file.exists():
                shutil.copy2(src_md, target_file)

            # Copy references
            src_refs = source_dir / "references"
            if src_refs.exists():
                for ref in src_refs.rglob("*.md"):
                    target_ref = refs_dir / ref.name
                    if not target_ref.exists():
                        shutil.copy2(ref, target_ref)

    # Move originals to archive
    if config["rules"]["keep_originals_in_archive"]:
        for name in plan["source_skills"]:
            src = SKILLS_DIR / name
            if src.exists():
                dest = ARCHIVE_DIR / name
                if not dest.exists():
                    shutil.move(str(src), str(dest))
                    print(f"  📦 Archived: {name}")

    # Record in history
    record_merge(plan)

    print(f"\n✅ Merge complete: {plan['suite_name']}")
    print(f"   Location: {suite_dir}")
    print(f"   Modules: {len(plan['modules'])}")
    return True


def generate_suite_skillmd(plan, skills):
    """Generate the main SKILL.md for a merged suite."""
    modules = plan["modules"]

    md = f"""---
name: {plan['suite_name']}
description: "Integrated suite combining {', '.join(plan['source_skills'])}. Use when working with {identify_group_theme_text(modules)}."
category: merged
source_skills: {plan['source_skills']}
merged_date: "{datetime.now().strftime('%Y-%m-%d')}"
---

# {plan['suite_name'].replace('-', ' ').title()}

Integrated skill suite combining {len(plan['source_skills'])} related skills.

## 📚 Available Modules

| Module | Source | When to Use |
|--------|--------|-------------|
"""

    for m in modules:
        triggers_str = ", ".join(m["triggers"][:5])
        md += f"| **{m['name']}** | {m['source']} | {triggers_str} |\n"

    md += f"""
## 🔀 How to Use

### Full Suite
Load this skill for comprehensive coverage of all modules.

### Individual Module
Reference a specific module by name: `{plan['suite_name']}/references/<module>.md`

### Combination
Load multiple modules for cross-module workflows.

## 📦 Module Details

"""

    for m in modules:
        md += f"### {m['name']}\n"
        md += f"- **Source**: {m['source']}\n"
        md += f"- **Triggers**: {', '.join(m['triggers'][:8])}\n"
        md += f"- **File**: `references/{m['file'].split('/')[-1]}`\n\n"

    # Add pitfalls section
    if plan["pitfalls_preserved"]:
        md += "## ⚠️ Preserved Pitfalls\n\n"
        for p in plan["pitfalls_preserved"]:
            md += f"- **{p['source']}**: {p['count']} known pitfalls preserved in module references\n"
        md += "\n> All pitfalls from source skills are preserved in their respective reference files.\n"

    return md


def identify_group_theme_text(modules):
    """Extract theme text from modules for description."""
    all_triggers = []
    for m in modules:
        all_triggers.extend(m.get("triggers", []))
    # Take top unique triggers
    seen = set()
    top = []
    for t in all_triggers:
        if t not in seen and len(t) > 3:
            seen.add(t)
            top.append(t)
        if len(top) >= 3:
            break
    return ", ".join(top[:3]) if top else "related topics"


# ── Health Report ─────────────────────────────────────────────────────

def health_report(skills):
    """Generate skill health report."""
    active = [s for s in skills.values() if s["base"] == "active"]
    archived = [s for s in skills.values() if s["base"] == "archived"]

    print("📊 Skill Health Report")
    print("=" * 50)
    print(f"  Active skills:   {len(active)}")
    print(f"  Archived skills: {len(archived)}")
    print(f"  Total:           {len(skills)}")

    # Size analysis
    total_active = sum(s["size"] for s in active)
    total_archived = sum(s["size"] for s in archived)
    print(f"\n  Active size:   {total_active / 1024:.0f} KB")
    print(f"  Archived size: {total_archived / 1024:.0f} KB")

    # Duplicates check
    print("\n  ⚠️ Potential Duplicates:")
    for name, s in skills.items():
        matches = [other for other in skills.values()
                   if other["name"] != name and other["content_hash"] == s["content_hash"]]
        if matches:
            print(f"    {name} == {matches[0]['name']}")

    # Skills without description
    print("\n  ⚠️ Missing Descriptions:")
    for name, s in sorted(skills.items()):
        if not s["description"]:
            print(f"    {name}")

    # Large skills
    print("\n  📏 Large Skills (>100KB):")
    large = sorted([s for s in active if s["size"] > 100 * 1024], key=lambda x: -x["size"])
    for s in large:
        print(f"    {s['name']}: {s['size'] / 1024:.0f} KB")
    if not large:
        print("    (none)")


# ── Rollback ──────────────────────────────────────────────────────────

def rollback(suite_name):
    """Rollback a merge, restoring originals from archive."""
    suite_dir = SKILLS_DIR / suite_name
    if not suite_dir.exists():
        print(f"❌ {suite_name} not found")
        return False

    # Read modules.json
    modules_file = suite_dir / "modules.json"
    if not modules_file.exists():
        print(f"❌ modules.json not found in {suite_name}")
        return False

    with open(modules_file) as f:
        modules_data = json.load(f)

    source_skills = modules_data.get("source_skills", [])

    print(f"🔄 Rolling back {suite_name}...")
    print(f"   Restoring: {', '.join(source_skills)}")

    for name in source_skills:
        archived = ARCHIVE_DIR / name
        if archived.exists():
            target = SKILLS_DIR / name
            if not target.exists():
                shutil.move(str(archived), str(target))
                print(f"  ✅ Restored: {name}")
            else:
                print(f"  ⚠️ {name} already exists in active, skipping")

    # Remove suite
    shutil.rmtree(suite_dir)
    print(f"  🗑️  Removed: {suite_name}")

    print("\n✅ Rollback complete")
    return True


# ── History ───────────────────────────────────────────────────────────

def record_merge(plan):
    """Record merge in history."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

    entry = f"""## {datetime.now().strftime('%Y-%m-%d %H:%M')} — {plan['suite_name']}

**Sources**: {', '.join(plan['source_skills'])}
**Modules**: {len(plan['modules'])}
**Pitfalls preserved**: {sum(p['count'] for p in plan['pitfalls_preserved'])} from {len(plan['pitfalls_preserved'])} skills
**Status**: ✅ Executed

"""

    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            existing = f.read()
        with open(HISTORY_FILE, "w") as f:
            f.write(entry + existing)
    else:
        with open(HISTORY_FILE, "w") as f:
            f.write(f"# Merge History\n\n{entry}")


# ── New Skill Review ─────────────────────────────────────────────────

def review_new_skill(new_skill_path, skills, config):
    """Review a new skill and decide: merge, add, or skip."""
    new_path = Path(new_skill_path)
    if not new_path.exists():
        print(f"❌ Path not found: {new_skill_path}")
        return None

    # Parse new skill
    skill_md = new_path / "SKILL.md"
    if not skill_md.exists():
        print(f"❌ No SKILL.md found in {new_skill_path}")
        return None

    new_skill = parse_skill(skill_md, new_path, "new")

    # Compare against all existing skills
    candidates = []
    for name, existing in skills.items():
        if existing["base"] == "archived":
            continue  # Only compare against active skills

        score = compute_similarity(new_skill, existing)
        if score > 0.05:  # Any meaningful overlap
            candidates.append({
                "skill": existing,
                "score": score,
                "same_capability": detect_same_capability(new_skill, existing),
                "different_implementation": detect_different_implementation(new_skill, existing),
            })

    candidates.sort(key=lambda x: -x["score"])

    # Decision logic
    decision = make_review_decision(new_skill, candidates, config)

    return {
        "new_skill": new_skill,
        "candidates": candidates[:5],  # Top 5 matches
        "decision": decision,
    }


def detect_same_capability(s1, s2):
    """Detect if two skills have the same core capability."""
    # Extract capability keywords (verbs + core nouns)
    cap_patterns = [
        r'(?:generate|create|build|make|convert|extract|analyze|search|query|monitor|detect|deploy|install|configure|manage|optimize|review|debug|train|fine-tune)',
    ]

    caps1 = set()
    caps2 = set()

    for pattern in cap_patterns:
        caps1.update(re.findall(pattern, s1["description"].lower()))
        caps2.update(re.findall(pattern, s2["description"].lower()))

    # Also check keywords
    caps1.update(kw for kw in s1["keywords"] if len(kw) > 4)
    caps2.update(kw for kw in s2["keywords"] if len(kw) > 4)

    overlap = caps1 & caps2
    union = caps1 | caps2

    if not union:
        return False

    jaccard = len(overlap) / len(union)
    return jaccard > 0.3  # 30% capability overlap


def detect_different_implementation(s1, s2):
    """Detect if two skills use different implementation methods."""
    impl_keywords = {
        "langchain", "llamaindex", "haystack", "crewai", "autogen",
        "playwright", "selenium", "puppeteer", "camoufox",
        "docker", "podman", "kubernetes", "compose",
        "fastapi", "flask", "django", "streamlit", "gradio",
        "pytorch", "tensorflow", "jax", "mlx",
        "ollama", "vllm", "tgi", "sglang",
        "qdrant", "chroma", "milvus", "pinecone", "weaviate",
        "openai", "anthropic", "google", "mistral",
        "curl", "requests", "httpx", "aiohttp",
    }

    impls1 = set()
    impls2 = set()

    for content in [s1["description"], s1.get("content_snippet", "")]:
        for kw in impl_keywords:
            if kw in content.lower():
                impls1.add(kw)

    for content in [s2["description"], s2.get("content_snippet", "")]:
        for kw in impl_keywords:
            if kw in content.lower():
                impls2.add(kw)

    # Different if they share capability but use different tools
    return impls1 != impls2 and (impls1 or impls2)


def make_review_decision(new_skill, candidates, config):
    """Make decision: merge_as_redundant, merge_as_module, add_new, or skip."""
    if not candidates:
        return {
            "action": "add_new",
            "reason": "No similar skills found. This is a new capability.",
            "confidence": 0.8,
        }

    top = candidates[0]
    score = top["score"]
    same_cap = top["same_capability"]
    diff_impl = top["different_implementation"]

    # Check if locked
    if is_locked(top["skill"]["name"], config):
        return {
            "action": "add_new",
            "reason": f"Most similar skill '{top['skill']['name']}' is locked. Adding as separate skill.",
            "confidence": 0.7,
            "locked_target": top["skill"]["name"],
        }

    if same_cap and diff_impl:
        return {
            "action": "merge_as_redundant",
            "reason": f"Same capability as '{top['skill']['name']}' but different implementation. Merge as redundant/fallback option.",
            "target": top["skill"]["name"],
            "similarity": score,
            "confidence": 0.85,
            "implementation_diff": True,
        }

    if same_cap and not diff_impl:
        if score > 0.5:
            return {
                "action": "skip_duplicate",
                "reason": f"Nearly identical to '{top['skill']['name']}' (similarity: {score:.2f}). Same capability, same implementation.",
                "target": top["skill"]["name"],
                "similarity": score,
                "confidence": 0.9,
            }
        else:
            return {
                "action": "merge_as_module",
                "reason": f"Related to '{top['skill']['name']}' but different focus. Add as module to existing suite or create new suite.",
                "target": top["skill"]["name"],
                "similarity": score,
                "confidence": 0.7,
            }

    if score > 0.3:
        return {
            "action": "merge_as_module",
            "reason": f"Significant overlap with '{top['skill']['name']}'. Consider merging.",
            "target": top["skill"]["name"],
            "similarity": score,
            "confidence": 0.6,
        }

    return {
        "action": "add_new",
        "reason": f"Low similarity to existing skills. New capability.",
        "confidence": 0.8,
    }


def execute_review(review_result, auto=False):
    """Execute the review decision."""
    new_skill = review_result["new_skill"]
    decision = review_result["decision"]

    print(f"\n📋 New Skill Review: {new_skill['name']}")
    print(f"  Description: {new_skill['description'][:100]}")
    print(f"  Size: {new_skill['size'] / 1024:.1f} KB")
    print(f"\n  Decision: **{decision['action']}**")
    print(f"  Reason: {decision['reason']}")
    print(f"  Confidence: {decision['confidence']:.0%}")

    if review_result["candidates"]:
        print(f"\n  Top matches:")
        for c in review_result["candidates"][:3]:
            impl_tag = "🔄 different impl" if c["different_implementation"] else "same impl"
            cap_tag = "✅ same capability" if c["same_capability"] else "different capability"
            print(f"    - {c['skill']['name']} (score: {c['score']:.2f}) [{impl_tag}] [{cap_tag}]")

    if auto:
        print(f"\n  Auto-executing: {decision['action']}")
        if decision["action"] == "add_new":
            dest = SKILLS_DIR / new_skill["name"]
            if dest.exists():
                print(f"  ⚠️ {new_skill['name']} already exists, skipping")
                return
            shutil.copytree(new_skill["path"], dest)
            print(f"  ✅ Added: {new_skill['name']}")
        elif decision["action"] == "merge_as_redundant":
            target = decision["target"]
            # Add as redundant implementation
            target_dir = SKILLS_DIR / target
            if not target_dir.exists():
                print(f"  ⚠️ Target {target} not found, adding as new skill instead")
                shutil.copytree(new_skill["path"], SKILLS_DIR / new_skill["name"])
                return

            refs_dir = target_dir / "references"
            refs_dir.mkdir(exist_ok=True)

            # Copy as redundant implementation
            impl_name = new_skill["name"].replace("-", "_")
            target_file = refs_dir / f"redundant_{impl_name}.md"
            src_md = new_skill["path"] / "SKILL.md"
            if src_md.exists():
                shutil.copy2(src_md, target_file)

            # Update modules.json if exists
            modules_file = target_dir / "modules.json"
            if modules_file.exists():
                with open(modules_file) as f:
                    modules = json.load(f)
                modules.setdefault("redundant_implementations", []).append({
                    "name": new_skill["name"],
                    "source": new_skill["name"],
                    "added": datetime.now().isoformat(),
                    "file": f"references/redundant_{impl_name}.md",
                })
                with open(modules_file, "w") as f:
                    json.dump(modules, f, indent=2, ensure_ascii=False)

            print(f"  ✅ Merged as redundant implementation into {target}")
            print(f"     File: {target_file}")
        elif decision["action"] == "skip_duplicate":
            print(f"  ⏭️  Skipped - duplicate of {decision['target']}")
        elif decision["action"] == "merge_as_module":
            print(f"  ⚠️  Complex merge - manual review recommended")
            print(f"     Run: --execute to create merge plan")
    else:
        print(f"\n  Run with --auto to execute automatically")
        print(f"  Or manually: mv {new_skill['path']} {SKILLS_DIR}/{new_skill['name']}")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]
    config = load_config()
    skills = scan_skills()

    if action == "--scan":
        groups = find_similar_groups(skills)
        print("🔍 Similar Skill Groups\n")
        for name, members in sorted(groups.items(), key=lambda x: -len(x[1])):
            locked_count = sum(1 for m in members if is_locked(m, config))
            print(f"## {name} ({len(members)} skills, {locked_count} locked)")
            for m in members:
                lock_icon = "🔒" if is_locked(m, config) else "  "
                s = skills.get(m)
                if s:
                    desc = s['description'][:80] if s['description'] else "(no description)"
                    print(f"  {lock_icon} {m}: {desc}")
                else:
                    print(f"  {lock_icon} {m}: (not found)")
            print()

    elif action == "--plan":
        if len(sys.argv) < 3:
            print("Usage: consolidate.py --plan <group_name>")
            sys.exit(1)

        group_name = sys.argv[2]
        groups = find_similar_groups(skills)

        if group_name not in groups:
            print(f"❌ Group '{group_name}' not found. Available groups:")
            for g in groups:
                print(f"  - {g}")
            sys.exit(1)

        plan = generate_merge_plan(groups[group_name], skills, config)
        if plan:
            print(f"\n📋 Merge Plan: {plan['suite_name']}")
            print(f"  Sources: {', '.join(plan['source_skills'])}")
            if plan['locked_skills']:
                print(f"  Locked (skipped): {', '.join(plan['locked_skills'])}")
            print(f"  Modules: {len(plan['modules'])}")
            for m in plan['modules']:
                print(f"    - {m['name']} ({m['size'] / 1024:.0f} KB)")
            if plan['pitfalls_preserved']:
                print(f"  Pitfalls preserved:")
                for p in plan['pitfalls_preserved']:
                    print(f"    - {p['source']}: {p['count']} pitfalls")
            print(f"\n  Run with --execute {plan['suite_name']} to apply")

    elif action == "--execute":
        if len(sys.argv) < 3:
            print("Usage: consolidate.py --execute <group_name>")
            sys.exit(1)

        group_name = sys.argv[2]
        groups = find_similar_groups(skills)

        if group_name not in groups:
            print(f"❌ Group '{group_name}' not found")
            sys.exit(1)

        plan = generate_merge_plan(groups[group_name], skills, config)
        if plan:
            execute_merge(plan, skills, config)

    elif action == "--health":
        health_report(skills)

    elif action == "--rollback":
        if len(sys.argv) < 3:
            print("Usage: consolidate.py --rollback <suite_name>")
            sys.exit(1)
        rollback(sys.argv[2])

    elif action == "--review":
        if len(sys.argv) < 3:
            print("Usage: consolidate.py --review <path_to_new_skill> [--auto]")
            sys.exit(1)

        new_path = sys.argv[2]
        auto = "--auto" in sys.argv

        result = review_new_skill(new_path, skills, config)
        if result:
            execute_review(result, auto=auto)

    else:
        print(f"❌ Unknown action: {action}")
        print(__doc__)


if __name__ == "__main__":
    main()
