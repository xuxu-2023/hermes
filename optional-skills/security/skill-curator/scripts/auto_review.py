#!/usr/bin/env python3
"""
Skill Auto-Review Pipeline
Scans for recently added skills, auto-reviews them, and generates a report.

Usage:
    python3 auto_review.py [--days N] [--auto] [--report-only]
    
    --days N        Check skills modified in last N days (default: 7)
    --auto          Auto-apply safe decisions (add_new + skip_duplicate)
    --report-only   Just generate report, no actions
"""

import os
import sys
import json
import time
import shutil
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from consolidate import (
    scan_skills, load_config, parse_skill, compute_similarity,
    detect_same_capability, detect_different_implementation,
    make_review_decision, SKILLS_DIR, ARCHIVE_DIR, HISTORY_FILE
)

REPORT_DIR = SKILLS_DIR / "skill-curator" / "references"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

def find_recent_skills(days=7):
    """Find skills modified in the last N days."""
    cutoff = time.time() - (days * 86400)
    recent = []
    
    for base, label in [(SKILLS_DIR, "active"), (ARCHIVE_DIR, "archive")]:
        if not base.exists():
            continue
        for entry in base.iterdir():
            if entry.name.startswith('.') or not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            
            mtime = skill_md.stat().st_mtime
            if mtime >= cutoff:
                recent.append({
                    "name": entry.name,
                    "path": entry,
                    "loc": label,
                    "modified": datetime.fromtimestamp(mtime).isoformat(),
                    "mtime": mtime,
                })
    
    return sorted(recent, key=lambda x: -x["mtime"])


def review_skill_against_active(new_skill_info, all_skills, config):
    """Review a skill against all active skills."""
    new_skill = parse_skill(new_skill_info["path"] / "SKILL.md", new_skill_info["path"], new_skill_info["loc"])
    
    candidates = []
    for name, existing in all_skills.items():
        if existing["base"] == "archived":
            continue
        
        # Skip if this is the same skill (self-match)
        if name == new_skill["name"]:
            continue
        
        # Skip merged suites (they're already consolidated, not real skills)
        if existing.get("category") == "merged":
            continue
        
        # Skip if this is a suite and the new skill is one of its source_skills
        if existing.get("base") == "active":
            modules_file = Path(existing["path"]) / "modules.json" if "path" in existing else SKILLS_DIR / name / "modules.json"
            if modules_file.exists():
                try:
                    with open(modules_file) as f:
                        mod_data = json.load(f)
                    source_skills = mod_data.get("source_skills", [])
                    if new_skill["name"] in source_skills:
                        continue
                except (json.JSONDecodeError, FileNotFoundError):
                    pass
        
        score = compute_similarity(new_skill, existing)
        if score > 0.05:
            candidates.append({
                "skill": existing,
                "score": score,
                "same_capability": detect_same_capability(new_skill, existing),
                "different_implementation": detect_different_implementation(new_skill, existing),
            })
    
    candidates.sort(key=lambda x: -x["score"])
    decision = make_review_decision(new_skill, candidates, config)
    
    return {
        "new_skill": new_skill,
        "candidates": candidates[:3],
        "decision": decision,
    }


def execute_auto_review(review_result, auto=False):
    """Execute the review decision."""
    new_skill = review_result["new_skill"]
    decision = review_result["decision"]
    
    # If skill is already active and decision is add_new, it's fine
    if new_skill["base"] == "active" and decision["action"] == "add_new":
        return f"✅ Already active: {new_skill['name']}"
    
    if decision["action"] == "add_new" and decision["confidence"] >= 0.8:
        if new_skill["base"] == "archive":
            # Move from archive to active
            src = ARCHIVE_DIR / new_skill["name"]
            dest = SKILLS_DIR / new_skill["name"]
            if not dest.exists():
                if auto:
                    shutil.move(str(src), str(dest))
                    return f"✅ Moved to active: {new_skill['name']}"
                else:
                    return f"📋 Recommend adding: {new_skill['name']} (from archive)"
            else:
                return f"✅ Already exists: {new_skill['name']}"
        else:
            return f"✅ Already active: {new_skill['name']}"
    
    elif decision["action"] == "skip_duplicate":
        return f"⏭️ Skip duplicate of {decision['target']}: {new_skill['name']}"
    
    elif decision["action"] == "merge_as_redundant":
        target = decision["target"]
        target_dir = SKILLS_DIR / target
        if not target_dir.exists():
            return f"⚠️ Target {target} not found for {new_skill['name']}"
        
        refs_dir = target_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        
        impl_name = new_skill["name"].replace("-", "_")
        target_file = refs_dir / f"redundant_{impl_name}.md"
        src_md = new_skill["path"] / "SKILL.md"
        
        if auto and src_md.exists() and not target_file.exists():
            shutil.copy2(src_md, target_file)
            
            # Update modules.json
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
            
            return f"✅ Merged as redundant into {target}: {new_skill['name']}"
        else:
            return f"📋 Recommend merging as redundant into {target}: {new_skill['name']}"
    
    elif decision["action"] == "merge_as_module":
        return f"📋 Recommend merging as module into {decision['target']}: {new_skill['name']}"
    
    return f"⚠️ Uncertain: {decision['action']} ({new_skill['name']}) — manual review needed"


def generate_report(results, days):
    """Generate a markdown report."""
    report = f"""# Skill Auto-Review Report

**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Scan window**: Last {days} days
**Skills reviewed**: {len(results)}

## Summary

| Action | Count |
|--------|-------|
"""
    action_counts = {}
    for r in results:
        action = r["decision"]["action"]
        action_counts[action] = action_counts.get(action, 0) + 1
    
    for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
        report += f"| {action} | {count} |\n"
    
    report += "\n## Details\n\n"
    
    for r in results:
        ns = r["new_skill"]
        d = r["decision"]
        report += f"### {ns['name']}\n"
        report += f"- **Action**: `{d['action']}`\n"
        report += f"- **Reason**: {d['reason']}\n"
        report += f"- **Confidence**: {d['confidence']:.0%}\n"
        if r["candidates"]:
            report += f"- **Top match**: {r['candidates'][0]['skill']['name']} (score: {r['candidates'][0]['score']:.2f})\n"
        report += "\n---\n\n"
    
    return report


def main():
    days = 7
    auto = False
    report_only = False
    
    args = sys.argv[1:]
    if "--days" in args:
        idx = args.index("--days")
        if idx + 1 < len(args):
            days = int(args[idx + 1])
    auto = "--auto" in args
    report_only = "--report-only" in args
    
    print(f"🔍 Skill Auto-Review Pipeline")
    print(f"  Scan window: last {days} days")
    print(f"  Auto-execute: {'yes' if auto else 'no'}")
    print(f"  Report-only: {'yes' if report_only else 'no'}")
    
    config = load_config()
    all_skills = scan_skills()
    recent = find_recent_skills(days)
    
    if not recent:
        print(f"\n✅ No skills modified in the last {days} days.")
        return
    
    print(f"\n📋 Found {len(recent)} recent skills:\n")
    
    results = []
    for skill_info in recent:
        result = review_skill_against_active(skill_info, all_skills, config)
        results.append(result)
        
        action = execute_auto_review(result, auto=auto)
        print(f"  {action}")
    
    # Generate report
    report = generate_report(results, days)
    report_path = REPORT_DIR / f"auto-review-{datetime.now().strftime('%Y%m%d-%H%M')}.md"
    
    with open(report_path, "w") as f:
        f.write(report)
    
    print(f"\n📄 Report saved: {report_path}")
    
    if not auto:
        print(f"\n  Run with --auto to execute safe decisions automatically")


if __name__ == "__main__":
    main()
