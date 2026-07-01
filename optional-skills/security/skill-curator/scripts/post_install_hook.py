#!/usr/bin/env python3
"""
Post-Install Hook for Skill-Curator
Automatically reviews newly installed skills.

Usage:
    # Manual trigger after installing a skill:
    python3 post_install_hook.py
    
    # Or integrate with hermes skills install:
    hermes skills install <path> && python3 post_install_hook.py
    
    # Cron: run every hour to check for recent installs
    python3 post_install_hook.py --auto-scan --minutes 60
"""

import os
import sys
import time
import json
import shutil
from pathlib import Path
from datetime import datetime

SKILLS_DIR = Path(os.path.expanduser("~/.hermes/profiles/coder/skills"))
ARCHIVE_DIR = SKILLS_DIR / ".archive"
SKILLCURATOR_DIR = SKILLS_DIR / "skill-curator"
INSTALL_LOG = SKILLCURATOR_DIR / "references" / "install_log.json"

# Add consolidate module to path
sys.path.insert(0, str(SKILLCURATOR_DIR / "scripts"))
from consolidate import (
    scan_skills, load_config, parse_skill, compute_similarity,
    detect_same_capability, detect_different_implementation,
    make_review_decision, HISTORY_FILE
)

def load_install_log():
    """Load or create install log."""
    if INSTALL_LOG.exists():
        with open(INSTALL_LOG) as f:
            return json.load(f)
    return {"installs": [], "reviews": []}

def save_install_log(data):
    """Save install log."""
    INSTALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(INSTALL_LOG, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

def find_recent_installs(minutes=60):
    """Find skills installed in the last N minutes."""
    cutoff = time.time() - (minutes * 60)
    recent = []
    log = load_install_log()
    logged_names = {i["name"] for i in log.get("installs", [])}
    
    for entry in sorted(SKILLS_DIR.iterdir()):
        if entry.name.startswith('.') or not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        
        # Skip if already reviewed
        if entry.name in logged_names:
            continue
        
        # Check modification time
        mtime = skill_md.stat().st_mtime
        if mtime >= cutoff:
            recent.append({
                "name": entry.name,
                "path": entry,
                "mtime": mtime,
                "modified": datetime.fromtimestamp(mtime).isoformat(),
            })
    
    return recent

def review_and_act(skill_info, auto=False):
    """Review a new skill and take action."""
    all_skills = scan_skills()
    config = load_config()
    new_skill = parse_skill(skill_info["path"] / "SKILL.md", skill_info["path"], "active")
    
    candidates = []
    for name, existing in all_skills.items():
        if existing["base"] == "archived" or name == new_skill["name"]:
            continue
        if existing.get("category") == "merged":
            continue
        
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
    
    # Log the review
    log = load_install_log()
    log["reviews"].append({
        "skill": new_skill["name"],
        "decision": decision["action"],
        "reason": decision["reason"],
        "confidence": decision["confidence"],
        "top_match": candidates[0]["skill"]["name"] if candidates else None,
        "top_score": candidates[0]["score"] if candidates else 0,
        "timestamp": datetime.now().isoformat(),
    })
    
    # Execute decision
    result = {"skill": new_skill["name"], "decision": decision["action"], "executed": False}
    
    if decision["action"] == "skip_duplicate" and decision["confidence"] >= 0.9:
        # High confidence duplicate → archive
        if auto:
            dest = ARCHIVE_DIR / new_skill["name"]
            if not dest.exists():
                shutil.move(str(skill_info["path"]), str(dest))
                result["executed"] = True
                result["action_detail"] = f"Archived as duplicate of {decision['target']}"
    
    elif decision["action"] == "merge_as_redundant" and decision["confidence"] >= 0.85:
        # Merge as redundant implementation
        target = decision["target"]
        target_dir = SKILLS_DIR / target
        if target_dir.exists() and auto:
            refs_dir = target_dir / "references"
            refs_dir.mkdir(exist_ok=True)
            impl_name = new_skill["name"].replace("-", "_")
            target_file = refs_dir / f"redundant_{impl_name}.md"
            
            src_md = skill_info["path"] / "SKILL.md"
            if src_md.exists() and not target_file.exists():
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
                
                # Archive original
                dest = ARCHIVE_DIR / new_skill["name"]
                if not dest.exists():
                    shutil.move(str(skill_info["path"]), str(dest))
                
                result["executed"] = True
                result["action_detail"] = f"Merged as redundant into {target}"
    
    elif decision["action"] == "add_new" and decision["confidence"] >= 0.8:
        # New capability → keep active
        result["executed"] = True
        result["action_detail"] = "New capability, kept active"
    
    save_install_log(log)
    return result

def main():
    auto = "--auto" in sys.argv
    auto_scan = "--auto-scan" in sys.argv
    minutes = 60
    
    if "--minutes" in sys.argv:
        idx = sys.argv.index("--minutes")
        if idx + 1 < len(sys.argv):
            minutes = int(sys.argv[idx + 1])
    
    if auto_scan:
        # Cron mode: scan for recent installs
        recent = find_recent_installs(minutes)
        if not recent:
            print("[SILENT]")
            return
        
        print(f"🔍 Found {len(recent)} new skill(s) installed in last {minutes} minutes:\n")
        for skill in recent:
            result = review_and_act(skill, auto=auto)
            status = "✅" if result["executed"] else "📋"
            print(f"  {status} {result['skill']}: {result['decision']} — {result.get('action_detail', 'manual review needed')}")
    else:
        # Manual mode: review a specific path
        if len(sys.argv) < 2 or sys.argv[1] in ("--auto", "--auto-scan", "--minutes"):
            print("Usage: post_install_hook.py <path_to_new_skill> [--auto]")
            print("       post_install_hook.py --auto-scan [--minutes N] [--auto]")
            sys.exit(1)
        
        skill_path = Path(sys.argv[1])
        if not skill_path.exists():
            print(f"❌ Path not found: {skill_path}")
            sys.exit(1)
        
        skill_info = {
            "name": skill_path.name,
            "path": skill_path,
            "mtime": skill_path.stat().st_mtime,
            "modified": datetime.fromtimestamp(skill_path.stat().st_mtime).isoformat(),
        }
        result = review_and_act(skill_info, auto=auto)
        print(f"\n{'✅' if result['executed'] else '📋'} {result['skill']}: {result['decision']}")
        print(f"   {result.get('action_detail', '')}")

if __name__ == "__main__":
    main()
