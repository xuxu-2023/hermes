#!/usr/bin/env python3
"""
Redundant Implementation Validator
Tests that redundant implementations (same capability, different approach)
are still functional and up-to-date.

Usage:
    python3 redundant_validator.py --scan           # Scan all suites for redundant implementations
    python3 redundant_validator.py --test <suite>    # Test a specific suite's redundant implementations
    python3 redundant_validator.py --report          # Generate validation report
"""

import os
import sys
import json
import time
import importlib.util
import subprocess
from pathlib import Path
from datetime import datetime

SKILLS_DIR = Path(os.path.expanduser("~/.hermes/profiles/coder/skills"))
ARCHIVE_DIR = SKILLS_DIR / ".archive"
SKILLCURATOR_DIR = SKILLS_DIR / "skill-curator"
VALIDATION_LOG = SKILLCURATOR_DIR / "references" / "validation_log.json"

# Test definitions per capability type
TEST_TEMPLATES = {
    "voice": [
        {"type": "import_check", "module": "whisper", "desc": "Whisper import check"},
        {"type": "import_check", "module": "soundfile", "desc": "SoundFile import check"},
    ],
    "video": [
        {"type": "import_check", "module": "moviepy", "desc": "MoviePy import check"},
        {"type": "import_check", "module": "remotion", "desc": "Remotion check (npm)"},
    ],
    "rag": [
        {"type": "import_check", "module": "langchain", "desc": "LangChain import check"},
        {"type": "import_check", "module": "llama_index", "desc": "LlamaIndex import check"},
    ],
    "prompt": [
        {"type": "file_exists", "path": None, "desc": "SKILL.md exists"},
    ],
    "memory": [
        {"type": "import_check", "module": "sqlite3", "desc": "SQLite import check"},
    ],
    "cloudflare": [
        {"type": "import_check", "module": "playwright", "desc": "Playwright import check"},
    ],
    "kanban": [
        {"type": "command_check", "command": ["hermes", "kanban", "list"], "desc": "Hermes kanban CLI"},
    ],
    "huggingface": [
        {"type": "command_check", "command": ["hf", "--version"], "desc": "HuggingFace CLI"},
    ],
}

def load_validation_log():
    """Load or create validation log."""
    if VALIDATION_LOG.exists():
        with open(VALIDATION_LOG) as f:
            return json.load(f)
    return {"validations": [], "last_validation": None}

def save_validation_log(data):
    """Save validation log."""
    VALIDATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(VALIDATION_LOG, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

def find_suites_with_redundant():
    """Find suites that have redundant implementations."""
    results = []
    for entry in sorted(SKILLS_DIR.iterdir()):
        if entry.name.startswith('.') or not entry.is_dir():
            continue
        modules_file = entry / "modules.json"
        if modules_file.exists():
            try:
                with open(modules_file) as f:
                    data = json.load(f)
                redundant = data.get("redundant_implementations", [])
                if redundant:
                    results.append({
                        "suite": entry.name,
                        "modules": data.get("modules", {}),
                        "redundant": redundant,
                    })
            except (json.JSONDecodeError, KeyError):
                pass
    return results

def run_import_check(module_name):
    """Check if a Python module can be imported."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0, result.stderr.strip() if result.returncode != 0 else ""
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)

def run_command_check(command):
    """Check if a CLI command works."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0, result.stderr.strip() if result.returncode != 0 else ""
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except FileNotFoundError:
        return False, f"Command not found: {command[0]}"
    except Exception as e:
        return False, str(e)

def run_file_exists_check(path):
    """Check if a file exists."""
    if path and Path(path).exists():
        return True, ""
    return False, f"File not found: {path}"

def run_test(test):
    """Run a single test."""
    test_type = test.get("type")
    
    if test_type == "import_check":
        return run_import_check(test.get("module", ""))
    elif test_type == "command_check":
        return run_command_check(test.get("command", []))
    elif test_type == "file_exists":
        return run_file_exists_check(test.get("path"))
    
    return False, f"Unknown test type: {test_type}"

def validate_suite(suite_info):
    """Validate a suite's redundant implementations."""
    suite_name = suite_info["suite"]
    redundant = suite_info["redundant"]
    
    results = {
        "suite": suite_name,
        "timestamp": datetime.now().isoformat(),
        "redundant_tests": [],
        "overall_status": "PASS",
    }
    
    # Determine test category based on suite name
    category = None
    for key in TEST_TEMPLATES:
        if key in suite_name.lower():
            category = key
            break
    
    tests = TEST_TEMPLATES.get(category, [])
    
    # Test each redundant implementation
    for impl in redundant:
        impl_name = impl.get("name", "unknown")
        impl_results = {
            "name": impl_name,
            "tests": [],
            "status": "PASS",
        }
        
        # Run generic tests
        for test in tests:
            passed, error = run_test(test)
            test_result = {
                "test": test.get("desc", test.get("type", "unknown")),
                "passed": passed,
                "error": error if not passed else "",
            }
            impl_results["tests"].append(test_result)
            
            if not passed:
                impl_results["status"] = "FAIL"
                results["overall_status"] = "FAIL"
        
        # Check if redundant file exists
        impl_file = SKILLS_DIR / suite_name / impl.get("file", "")
        if impl_file.exists():
            test_result = {
                "test": f"Redundant file exists: {impl.get('file', '')}",
                "passed": True,
                "error": "",
            }
        else:
            test_result = {
                "test": f"Redundant file exists: {impl.get('file', '')}",
                "passed": False,
                "error": f"File not found: {impl_file}",
            }
            impl_results["status"] = "FAIL"
            results["overall_status"] = "FAIL"
        
        impl_results["tests"].append(test_result)
        results["redundant_tests"].append(impl_results)
    
    return results

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    action = sys.argv[1]
    suites = find_suites_with_redundant()
    
    if action == "--scan":
        print(f"🔍 Scanning {len(suites)} suites for redundant implementations...")
        if not suites:
            print("  No suites with redundant implementations found.")
            return
        for s in suites:
            print(f"  - {s['suite']}: {len(s['redundant'])} redundant implementations")
    
    elif action == "--test":
        if len(sys.argv) < 3:
            print("Usage: redundant_validator.py --test <suite_name>")
            sys.exit(1)
        
        suite_name = sys.argv[2]
        target = None
        for s in suites:
            if s["suite"] == suite_name:
                target = s
                break
        
        if not target:
            print(f"❌ Suite '{suite_name}' not found or has no redundant implementations")
            sys.exit(1)
        
        print(f"🧪 Testing {suite_name} redundant implementations...")
        result = validate_suite(target)
        
        for impl_result in result["redundant_tests"]:
            status = "✅" if impl_result["status"] == "PASS" else "❌"
            print(f"  {status} {impl_result['name']}")
            for test in impl_result["tests"]:
                icon = "✅" if test["passed"] else "❌"
                print(f"    {icon} {test['test']}")
                if test["error"]:
                    print(f"       Error: {test['error']}")
        
        print(f"\nOverall: {'✅ PASS' if result['overall_status'] == 'PASS' else '❌ FAIL'}")
        
        # Log result
        log = load_validation_log()
        log["validations"].append(result)
        log["last_validation"] = datetime.now().isoformat()
        save_validation_log(log)
    
    elif action == "--report":
        log = load_validation_log()
        if not log["validations"]:
            print("📋 No validation history found. Run --test first.")
            return
        
        print(f"📊 Validation Report")
        print(f"  Total validations: {len(log['validations'])}")
        print(f"  Last validation: {log.get('last_validation', 'Never')}")
        
        # Latest results
        latest = log["validations"][-1]
        print(f"\n  Latest ({latest['suite']}): {latest['overall_status']}")
        for impl in latest["redundant_tests"]:
            status = "✅" if impl["status"] == "PASS" else "❌"
            print(f"    {status} {impl['name']}: {len(impl['tests'])} tests")
    
    else:
        print(f"❌ Unknown action: {action}")
        print(__doc__)

if __name__ == "__main__":
    main()
