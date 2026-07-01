---
name: skill-curator
description: Scan skills for similarity clusters, propose merge plans, review new skills for integration, and maintain skill library hygiene.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [skill-management, optimization, consolidation, review, hygiene, maintenance]
    category: security
---

# Skill Curator

技能优化引擎 — 扫描技能库，识别相似/重叠技能，生成整合方案，审核新技能，维护技能健康度。

## When to Use

- "优化技能" / "整合技能" / "skill optimization" / "merge skills" / "consolidate skills"
- "skill audit" / "skill review" / "新技能审核"
- 技能数量增长后需要整理时
- 安装新技能后需要评估是否与已有技能重叠

## Prerequisites

- Python 3.10+
- No external dependencies (stdlib only)

## How to Run

```bash
# Scan for similar skills
python3 <skill-curator-path>/scripts/consolidate.py --scan

# Generate merge plan for a group
python3 <skill-curator-path>/scripts/consolidate.py --plan <group_name>

# Review a new skill for integration
python3 <skill-curator-path>/scripts/auto_review.py --days 7

# Auto-apply safe decisions (add_new + skip_duplicate)
python3 <skill-curator-path>/scripts/auto_review.py --days 7 --auto

# Skill health report
python3 <skill-curator-path>/scripts/consolidate.py --health
```

## Quick Reference

### consolidate.py

| Flag | Description |
|------|-------------|
| `--scan` | Scan for similar skill groups |
| `--plan <group>` | Generate merge plan for a group |
| `--execute <group>` | Execute merge (requires approval) |
| `--health` | Skill health report |
| `--rollback <suite>` | Rollback a merge |
| `--review <path>` | Review a new skill for integration |
| `--review <path> --auto` | Auto-apply best action |

### auto_review.py

| Flag | Description |
|------|-------------|
| `--days N` | Check skills modified in last N days (default: 7) |
| `--auto` | Auto-apply safe decisions |
| `--report-only` | Just generate report, no actions |

## Core Principles

1. **不删除原技能** — 整合后原技能保留在 `.archive/`，可随时恢复
2. **锁定保护** — `locked` 列表和 `locked_categories` 中的技能完全不被触碰
3. **子能力独立调用** — 整合后的技能支持单独调用其中某个子模块
4. **用户审核** — 所有整合方案必须经用户确认后才执行

## Cron Integration

For automated skill hygiene, add these cron jobs:

### Weekly Full Scan (Monday 10:00)
```
1. Run: consolidate.py --scan
2. Run: consolidate.py --health
3. Report NEW similar groups found
```

### Bi-hourly Auto-Review (every 2h)
```
1. Run: auto_review.py --days 1 --report-only
2. If skills marked "Recommend", report details
3. If all "Already active" or "Skip duplicate", output [SILENT]
```

See `references/cron-integration.md` for full cron job configurations.

## Pitfalls

- **Profile HOME resolution**: Cron runs under profile HOME. Always use absolute paths.
- **Suite self-match exclusion**: The auto_review script skips `category=merged` skills to avoid false reports.
- **`--days 1` for cron**: Don't use `--days 7` in cron — it will re-report the same skills. Use 1 day.
- **`[SILENT]` for no-op runs**: Auto-review cron MUST output `[SILENT]` when nothing to report.

## Verification

```bash
# Scan should complete without errors
python3 <path>/scripts/consolidate.py --scan

# Health report should show active/archived counts
python3 <path>/scripts/consolidate.py --health
```
