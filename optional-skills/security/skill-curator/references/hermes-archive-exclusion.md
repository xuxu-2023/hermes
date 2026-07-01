# Hermes Archive Exclusion Mechanism

## How .archive/ Works

Hermes `agent/skill_utils.py` line 27 defines `EXCLUDED_SKILL_DIRS`:

```python
EXCLUDED_SKILL_DIRS = frozenset((
    ".git", ".github", ".hub", ".archive", ".venv", "venv",
    "node_modules", "site-packages", "__pycache__", ".tox",
    ".nox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
))
```

The `is_excluded_skill_path()` function (line 47) checks every SKILL.md path against this set.
The `iter_skill_index_files()` function (line 668) prunes excluded dirs during os.walk:
```python
dirs[:] = [d for d in dirs if d not in EXCLUDED_SKILL_DIRS]
```

## Implications

- Skills in `skills/.archive/` are **never loaded** into the system prompt
- They don't appear in `skills_list` or `<available_skills>`
- Zero token cost — completely invisible to the agent
- Files remain intact — `mv` back to restore
- This is the **preferred archival mechanism** over `.disabled` suffix

## Verified On

- Hermes Agent v0.14.0+, macOS
- Profile path: `~/.hermes/profiles/<profile>/skills/.archive/`
- Global path: `~/.hermes/skills/.archive/`
