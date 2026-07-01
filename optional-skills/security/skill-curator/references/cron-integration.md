# Skill Curator Cron Integration

Two cron jobs automate skill hygiene. Both use `enabled_toolsets=['terminal', 'file']`.

## Job 1: Weekly Full Scan

```python
cronjob(
    action='create',
    schedule='0 10 * * 1',        # Monday 10:00
    name='skill-curator weekly scan',
    prompt='''Run skill-curator scan for skill hygiene:

1. Execute: `python3 ~/.hermes/profiles/coder/skills/skill-curator/scripts/consolidate.py --scan`
2. Execute: `python3 ~/.hermes/profiles/coder/skills/skill-curator/scripts/consolidate.py --health`
3. Report any NEW similar groups found that weren't previously consolidated
4. Report the count of active vs archived skills
5. If any new consolidation opportunities found, list them with member skills

Keep the report concise — just list new findings.''',
    enabled_toolsets=['terminal', 'file']
)
```

## Job 2: Bi-hourly Auto-Review

```python
cronjob(
    action='create',
    schedule='every 2h',
    name='skill-curator daily review',
    prompt='''Run the skill-curator auto-review pipeline:

1. Execute: `python3 ~/.hermes/profiles/coder/skills/skill-curator/scripts/auto_review.py --days 1 --report-only`
2. If there are any skills marked as "Recommend" (not "Already active" or "Skip duplicate"), report them with details
3. If all skills are "Already active" or "Skip duplicate", respond with exactly "[SILENT]" to suppress delivery

Only report if there are actual new findings that need attention.''',
    enabled_toolsets=['terminal', 'file']
)
```

## Pitfalls

- **`--days 1` for cron review**: Don't use `--days 7` in cron — it will re-report the same skills every run. Use 1 day to catch only truly new changes.
- **`[SILENT]` for no-op runs**: The auto-review cron MUST output `[SILENT]` when there's nothing to report, otherwise Feishu delivers empty noise.
- **Profile HOME resolution**: Cron runs under profile HOME (`~/.hermes/profiles/coder/home`). Always use absolute paths.
- **Suite self-match exclusion**: The `auto_review.py` script skips `category=merged` skills and suite source_skills to avoid false "duplicate of voice-suite" reports.
