#!/usr/bin/env python3
"""
Skill Usage Heat Tracker
Tracks which skills are actually loaded during sessions and generates
a heat report. Archives skills that haven't been used in N days.

Usage:
    python3 usage_tracker.py --scan         # Scan and update usage data
    python3 usage_tracker.py --report       # Generate usage report
    python3 usage_tracker.py --auto-archive # Archive skills unused for 90 days
    python3 usage_tracker.py --heat-map     # Generate heat map data for dashboard
"""

import os
import sys
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime
from collections import defaultdict

SKILLS_DIR = Path("/Users/bet/.hermes/profiles/coder/skills")
ARCHIVE_DIR = SKILLS_DIR / ".archive"
SKILLCURATOR_DIR = SKILLS_DIR / "skill-curator"
USAGE_DB = SKILLCURATOR_DIR / "references" / "usage_db.json"
SESSIONS_DIR = Path("/Users/bet/.hermes/profiles/coder/sessions")

def load_usage_db():
    """Load or create usage database."""
    if USAGE_DB.exists():
        with open(USAGE_DB) as f:
            return json.load(f)
    return {"skills": {}, "sessions_scanned": 0, "last_scan": None}

def save_usage_db(data):
    """Save usage database."""
    USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_DB, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

def scan_sessions_for_skills():
    """Scan session files to find which skills were loaded."""
    skill_usage = defaultdict(lambda: {"sessions": 0, "last_used": None, "total_refs": 0})
    sessions_scanned = 0
    
    if not SESSIONS_DIR.exists():
        return skill_usage, sessions_scanned
    
    # Get known skill names for matching
    known_skills = set()
    for entry in SKILLS_DIR.iterdir():
        if entry.name.startswith('.') or not entry.is_dir():
            continue
        if (entry / "SKILL.md").exists():
            known_skills.add(entry.name.lower())
    
    if not known_skills:
        return skill_usage, sessions_scanned
    
    # Scan .jsonl session files
    for session_file in sorted(SESSIONS_DIR.glob("*.jsonl")):
        try:
            mtime = session_file.stat().st_mtime
            found_any = False
            
            with open(session_file) as f:
                for i, line in enumerate(f):
                    if i > 30:  # Only scan first 30 lines (session start has skill list)
                        break
                    try:
                        data = json.loads(line)
                        content = data.get("content", "").lower()
                        
                        # Check for known skill names directly
                        for skill in known_skills:
                            if skill in content:
                                skill_usage[skill]["sessions"] += 1
                                skill_usage[skill]["total_refs"] += content.count(skill)
                                last = skill_usage[skill]["last_used"]
                                if last is None or mtime > last:
                                    skill_usage[skill]["last_used"] = mtime
                                found_any = True
                    except json.JSONDecodeError:
                        continue
            
            if found_any:
                sessions_scanned += 1
        except IOError:
            continue
    
    return skill_usage, sessions_scanned

def extract_skill_refs(content):
    """Extract skill names referenced in session content."""
    skill_names = set()
    
    import re
    
    # Pattern 1: - **skill-name**: description (from available_skills block)
    for match in re.finditer(r'- \*\*([a-z][a-z0-9-]{2,})\*\*:', content):
        skill_names.add(match.group(1))
    
    # Pattern 2: skill_view(name='skill-name') or skill_view(name="skill-name")
    for match in re.finditer(r"skill_view\(name=['\"]([a-z][a-z0-9-]{2,})['\"]", content):
        skill_names.add(match.group(1))
    
    # Pattern 3: skills_list mentions
    for match in re.finditer(r'"name":\s*"([a-z][a-z0-9-]{2,})"', content):
        skill_names.add(match.group(1))
    
    return skill_names

def update_usage_data(db):
    """Update usage data by scanning recent sessions."""
    new_usage, sessions_scanned = scan_sessions_for_skills()
    
    for skill_name, usage in new_usage.items():
        if skill_name not in db["skills"]:
            db["skills"][skill_name] = {
                "sessions": 0,
                "last_used": None,
                "total_refs": 0,
                "first_seen": datetime.now().isoformat(),
            }
        
        db["skills"][skill_name]["sessions"] = usage["sessions"]
        db["skills"][skill_name]["total_refs"] = usage["total_refs"]
        
        if usage["last_used"]:
            last_used = datetime.fromtimestamp(usage["last_used"]).isoformat()
            current_last = db["skills"][skill_name]["last_used"]
            if current_last is None or last_used > current_last:
                db["skills"][skill_name]["last_used"] = last_used
    
    db["sessions_scanned"] += sessions_scanned
    db["last_scan"] = datetime.now().isoformat()
    
    return db

def generate_report(db, days_threshold=30):
    """Generate usage report."""
    cutoff = time.time() - (days_threshold * 86400)
    cutoff_iso = datetime.fromtimestamp(cutoff).isoformat()
    
    all_skills = set()
    for entry in sorted(SKILLS_DIR.iterdir()):
        if entry.name.startswith('.') or not entry.is_dir():
            continue
        if (entry / "SKILL.md").exists():
            all_skills.add(entry.name)
    
    used = []
    unused = []
    rarely_used = []
    
    for skill_name in sorted(all_skills):
        data = db["skills"].get(skill_name, {})
        last_used = data.get("last_used")
        sessions = data.get("sessions", 0)
        
        if last_used is None:
            unused.append({"name": skill_name, "sessions": 0, "last_used": "Never"})
        elif last_used < cutoff_iso:
            rarely_used.append({
                "name": skill_name,
                "sessions": sessions,
                "last_used": last_used[:10],
            })
        else:
            used.append({
                "name": skill_name,
                "sessions": sessions,
                "last_used": last_used[:10],
            })
    
    report = {
        "generated": datetime.now().isoformat(),
        "total_skills": len(all_skills),
        "used_recently": len(used),
        "rarely_used": len(rarely_used),
        "never_used": len(unused),
        "used": used[:20],  # Top 20
        "rarely_used": rarely_used,
        "never_used": unused,
    }
    
    return report

def auto_archive(db, days_threshold=90):
    """Archive skills that haven't been used in N days."""
    cutoff = time.time() - (days_threshold * 86400)
    cutoff_iso = datetime.fromtimestamp(cutoff).isoformat()
    
    archived = []
    for skill_name, data in db["skills"].items():
        last_used = data.get("last_used")
        if last_used and last_used < cutoff_iso:
            src = SKILLS_DIR / skill_name
            dest = ARCHIVE_DIR / skill_name
            if src.exists() and not dest.exists():
                import shutil
                shutil.move(str(src), str(dest))
                archived.append(skill_name)
    
    return archived

def generate_heat_map(db):
    """Generate heat map data for dashboard."""
    heat_data = []
    
    for skill_name, data in sorted(db["skills"].items()):
        sessions = data.get("sessions", 0)
        last_used = data.get("last_used", "Never")
        total_refs = data.get("total_refs", 0)
        
        # Determine heat level
        if sessions >= 10:
            heat = "🔴 Hot"
        elif sessions >= 3:
            heat = "🟡 Warm"
        elif sessions >= 1:
            heat = "🟢 Cold"
        else:
            heat = "⚪ Unused"
        
        heat_data.append({
            "skill": skill_name,
            "sessions": sessions,
            "refs": total_refs,
            "last_used": last_used[:10] if last_used and last_used != "Never" else "Never",
            "heat": heat,
        })
    
    return sorted(heat_data, key=lambda x: -x["sessions"])

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    action = sys.argv[1]
    db = load_usage_db()
    
    if action == "--scan":
        print("🔍 Scanning sessions for skill usage...")
        db = update_usage_data(db)
        save_usage_db(db)
        print(f"✅ Scanned {db['sessions_scanned']} sessions total")
        print(f"   Tracking {len(db['skills'])} unique skills")
    
    elif action == "--report":
        days = 30
        if "--days" in sys.argv:
            idx = sys.argv.index("--days")
            if idx + 1 < len(sys.argv):
                days = int(sys.argv[idx + 1])
        
        report = generate_report(db, days)
        print(f"\n📊 Skill Usage Report (last {days} days)")
        print(f"  Total skills: {report['total_skills']}")
        print(f"  Used recently: {report['used_recently']}")
        print(f"  Rarely used: {report['rarely_used']}")
        print(f"  Never used: {report['never_used']}")
        
        if report["rarely_used"]:
            print(f"\n⚠️ Rarely used skills (>{days} days):")
            for s in report["rarely_used"][:10]:
                print(f"  - {s['name']} (last: {s['last_used']}, sessions: {s['sessions']})")
        
        if report["never_used"]:
            print(f"\n❌ Never used skills:")
            for s in report["never_used"][:10]:
                print(f"  - {s['name']}")
    
    elif action == "--auto-archive":
        days = 90
        if "--days" in sys.argv:
            idx = sys.argv.index("--days")
            if idx + 1 < len(sys.argv):
                days = int(sys.argv[idx + 1])
        
        archived = auto_archive(db, days)
        if archived:
            print(f"📦 Archived {len(archived)} skills (unused for {days} days):")
            for s in archived:
                print(f"  - {s}")
        else:
            print(f"✅ No skills to archive (all used within {days} days)")
    
    elif action == "--heat-map":
        heat_data = generate_heat_map(db)
        print(json.dumps(heat_data, indent=2, ensure_ascii=False))
    
    else:
        print(f"❌ Unknown action: {action}")
        print(__doc__)

if __name__ == "__main__":
    main()
