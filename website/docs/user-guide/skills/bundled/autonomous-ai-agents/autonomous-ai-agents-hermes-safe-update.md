---
title: "Hermes Safe Update"
sidebar_label: "Hermes Safe Update"
description: "Use when the user says they are about to update Hermes, update macOS, reboot a Mac host, finish a Hermes update, or finish a macOS update on a Hermes gateway..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Hermes Safe Update

Use when the user says they are about to update Hermes, update macOS, reboot a Mac host, finish a Hermes update, or finish a macOS update on a Hermes gateway host.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/autonomous-ai-agents/hermes-safe-update` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | macos, linux |
| Tags | `hermes`, `update`, `macos`, `gateway`, `launchd`, `safety`, `verification` |
| Related skills | [`hermes-agent`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent), [`claude-code`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-claude-code), [`codex`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-codex) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Hermes Safe Update

## Overview

This skill is the shared safe-update route for Hermes gateway hosts. It covers two related but distinct update classes:

| Route | Use for | Main risk |
|---|---|---|
| Hermes Safe Update | `hermes update`, Hermes source/profile/config changes, gateway restarts after a Hermes update | model-routing drift, local patch loss, skills/cron/memory drift, gateway boot regressions |
| Mac Host Update | macOS point updates, major macOS upgrades, Command Line Tools updates, any host update that requires a Mac reboot | gateway interruption, launchd drift, Python/venv/CLT breakage, network/certificate/permission changes |

The operating principle is the same for both routes:

```text
Nick is the operator.
Agents do preflight and postflight.
Agents do not run updates, reboots, gateway restarts, or credential changes unless Nick explicitly overrides the boundary.
```

## When to Use

Load this skill when the user says, in any clear natural language form, that they are:

- going to update Hermes;
- done updating Hermes;
- going to update macOS / the Mac / Command Line Tools;
- done updating macOS or rebooting a Hermes gateway host;
- asking whether it is safe to update a Hermes gateway host;
- asking for preflight or postflight verification around a Hermes or macOS update.

Do **not** require exact trigger wording. The route matching is semantic, not literal. If the user's intent is clear, act. Ask one short clarification only when the message could mean more than one phase.

## Semantic Trigger Map

The long explicit prompts are optional guardrails, not required incantations. Short messages are enough.

| Nick says | Agent should do |
|---|---|
| "I'm going to update macOS" | Mac Host Update preflight |
| "macOS update is done" | Mac Host Update postflight |
| "I'm going to update Hermes" | Hermes Safe Update pre-update readiness |
| "Hermes update is done" | Hermes Safe Update post-update reconciliation |

Equivalent phrases count. Examples: "I'm updating the Mac", "There's a Mac update; should I run it?", "Hermes update time", "The Mac reboot is complete", "I ran the Hermes update", "gateway update finished".

## Hard Operator Boundary

Nick is the operator. Agents may inspect, back up, prepare receipts, test, smoke, and report. Agents must not cross these lines without explicit Nick GO for that exact action.

### Agent may do

- Run read-only preflight and postflight checks.
- Create local Hermes backups and checksums.
- Create git patch bundles for uncommitted Hermes source changes.
- Capture sanitized receipts for config/model routing, skills, plugins, cron, memory, gateway process/log state, and OS/runtime state.
- Run focused tests, smokes, and ad-hoc verification.
- Inspect logs and process state.
- Prepare exact copy/paste commands or prompts for Nick.
- Tell Nick when the route is ready or blocked.

### Agent must not do

- Do not run `softwareupdate --install`.
- Do not click the macOS update UI.
- Do not reboot, shutdown, log out, stop, start, or restart the Mac.
- Do not run `hermes update`.
- Do not invoke `/update` from a gateway chat.
- Do not stop, start, or restart the Hermes gateway.
- Do not restart the Hermes gateway unless Nick explicitly GOes that restart.
- Do not rotate, revoke, delete, move, rewrite, print, or mutate credentials.
- Do not print or mutate credentials.
- Do not print or dump `.env`, `auth.json`, OAuth token values, API keys, cookies, passwords, connection strings, or secret-bearing config output.

Backups may contain secrets. Record backup paths and checksums only; never print archive contents.

## Route 1 — Hermes Safe Update

Use this route when Nick says he is going to update Hermes or has finished a Hermes update/restart.

### Hermes pre-update readiness

Goal: prove the current system is healthy and recoverable **before** Nick updates Hermes.

Minimum checks:

```bash
hermes profile list
hermes config path
pgrep -af 'hermes_cli.main|hermes.*gateway' || true
cd ~/.hermes/hermes-agent
git rev-parse --show-toplevel
git branch --show-current
git log -8 --oneline --decorate
git status --short
git diff --stat
git diff --check
hermes config check
```

If there are local source changes, create patch receipts before any update:

```bash
mkdir -p ~/Documents/lab-shared/hermes-update-receipts/patches
git diff > ~/Documents/lab-shared/hermes-update-receipts/patches/hermes-agent-pre-update-$(date +%Y%m%d-%H%M%S).patch
git diff --cached > ~/Documents/lab-shared/hermes-update-receipts/patches/hermes-agent-pre-update-staged-$(date +%Y%m%d-%H%M%S).patch
```

Create a local backup receipt:

```bash
mkdir -p ~/.hermes/backups/manual-update-readiness
hermes backup --quick --label pre-update-$(date +%Y%m%d-%H%M%S) \
  --output ~/.hermes/backups/manual-update-readiness/hermes-pre-update-$(date +%Y%m%d-%H%M%S).zip
shasum -a 256 ~/.hermes/backups/manual-update-readiness/<backup-file>
```

Sanitized receipt fields:

```text
model.provider
model.default
agent.reasoning_effort
agent.service_tier
fallback_providers provider/model/extra_body without credentials
auxiliary task provider/model/timeout/extra_body without credentials
MCP server names only
memory provider/status only
skills count/inventory summary
cron count/status summary
plugins count/status summary
gateway PID/start time/log paths
```

Focused verification targets, when available:

```bash
ulimit -n 4096
uv run pytest -o 'addopts=' -q tests/test_reasoning_effort_canonical.py
uv run pytest -o 'addopts=' -q tests/agent/test_auxiliary_client.py tests/agent/test_context_compressor.py
```

Compression timeout ad-hoc check:

```bash
venv/bin/python - <<'PY'
from unittest.mock import patch
from agent.context_compressor import ContextCompressor
with patch('agent.context_compressor._get_task_timeout', return_value=120.0), \
     patch('agent.context_compressor.estimate_messages_tokens_rough', return_value=20_000):
    timeout = ContextCompressor._summary_call_timeout('serialized prompt after pruning', source_tokens=195_874)
print(f'timeout={timeout:.3f}')
assert timeout > 390.0
PY
```

Clean pre-update signoff:

```text
READY FOR NICK UPDATE
```

After a successful Hermes pre-update readiness, include this copy/paste postflight prompt for Nick:

```text
Ares, Hermes update is done.
```

If blocked, respond:

```text
HERMES UPDATE PREFLIGHT BLOCKED — blockers: <list>
```

Nick then runs the Hermes update and any gateway restart himself.

### Hermes post-update reconciliation

Goal: prove the update did not break custom config, model routing, memory/brain, skills, cron, local patches, or the gateway runtime.

Minimum checks:

```bash
cd ~/.hermes/hermes-agent
git log -8 --oneline --decorate
git branch --show-current
git status --short
pgrep -af 'hermes_cli.main|hermes.*gateway' || true
hermes config check
hermes config path
git diff --check
```

Compare sanitized post-update state against the pre-update receipt. Confirm these survived:

```text
model.provider
model.default
agent.reasoning_effort
fallback_providers
auxiliary.compression
Gemini or other auxiliary task routing
MCP servers
skills/toolsets
memory provider
cron jobs
plugins
```

Run focused post-update gates and the compression timeout check again. If available and safe, run a live compression smoke without printing credentials. Expected clean shape:

```text
aborted False
summary_error None
network_failure False
```

Audit logs strictly after the post-update restart timestamp for fresh hits:

```text
Compression aborted
Connection error
Responses stream exceeded
all fallbacks exhausted
invalid reasoning
Traceback
```

Clean signoff:

```text
UPDATE VERIFIED
```

Partial or failed signoff:

```text
UPDATE PARTIALLY VERIFIED — explicit blockers: <list>. Do not rely on full parity until fixed.
UPDATE FAILED VERIFICATION — rollback/recovery recommended. Blockers: <list>. Last known-good backups/patches: <paths>.
```

## Route 2 — Mac Host Update

Use this route when Nick says he is going to update macOS / the Mac / Command Line Tools, or has finished a macOS update/reboot on a Hermes gateway host.

### Mac Host Update preflight

Goal: prove the Hermes host is healthy and recoverable **before** Nick updates macOS or reboots.

Read-only host/update state:

```bash
sw_vers
uname -a
softwareupdate --list
```

Record:

```text
current ProductVersion
current BuildVersion
Darwin kernel
available macOS update label/title/version
whether restart is required
Command Line Tools update if offered
```

Do not install the update.

Hermes gateway runtime state:

```bash
hermes config path
hermes gateway status
pgrep -af 'hermes_cli.main|hermes.*gateway' || true
```

Record:

```text
active config path
launchd plist path
launchd loaded yes/no
gateway PID
gateway command
gateway start time
stdout log path
stderr log path
```

Do not restart the gateway.

Hermes source/config state:

```bash
cd ~/.hermes/hermes-agent
git rev-parse --show-toplevel
git branch --show-current
git log -5 --oneline --decorate
git status --short
git diff --check
hermes config check
```

If local changes exist, create patch receipts:

```bash
mkdir -p ~/Documents/lab-shared/mac-host-update-receipts/patches
git diff > ~/Documents/lab-shared/mac-host-update-receipts/patches/hermes-agent-pre-macos-update-$(date +%Y%m%d-%H%M%S).patch
git diff --cached > ~/Documents/lab-shared/mac-host-update-receipts/patches/hermes-agent-pre-macos-update-staged-$(date +%Y%m%d-%H%M%S).patch
```

Create backup receipt:

```bash
mkdir -p ~/.hermes/backups/manual-macos-update-readiness
hermes backup --quick --label pre-macos-update-$(date +%Y%m%d-%H%M%S) \
  --output ~/.hermes/backups/manual-macos-update-readiness/hermes-pre-macos-update-$(date +%Y%m%d-%H%M%S).zip
shasum -a 256 ~/.hermes/backups/manual-macos-update-readiness/<backup-file>
```

Focused verification targets are the same reasoning/compression gates as the Hermes update route when they exist and are practical:

```bash
ulimit -n 4096
uv run pytest -o 'addopts=' -q tests/test_reasoning_effort_canonical.py
uv run pytest -o 'addopts=' -q tests/agent/test_auxiliary_client.py tests/agent/test_context_compressor.py
```

Also run the compression timeout ad-hoc check from the Hermes route when that code path exists.

Recent log audit should include macOS/host-specific signatures:

```text
Compression aborted
Connection error
Responses stream exceeded
all fallbacks exhausted
invalid reasoning
Traceback
launchd
crash
permission denied
certificate verify failed
xcrun
```

Clean preflight signoff:

```text
READY FOR NICK MAC UPDATE
```

Include concise receipts:

```text
Current macOS:
Available update:
Gateway PID/status:
Backup path/checksum:
Hermes HEAD:
Model routing:
Focused tests/ad-hoc checks:
Recent log audit:
```

After a successful Mac preflight, include this copy/paste postflight prompt for Nick:

```text
Ares, macOS update is done.
```

If blocked, respond:

```text
MAC UPDATE PREFLIGHT BLOCKED — blockers: <list>
```

Nick then installs the macOS update and reboots himself.

### Mac Host Update postflight

Goal: prove the macOS update/reboot did not break the Hermes gateway/runtime.

Confirm OS and reboot state:

```bash
sw_vers
uname -a
uptime
whoami
```

Confirm gateway/launchd state:

```bash
hermes gateway status
pgrep -af 'hermes_cli.main|hermes.*gateway' || true
```

Do not restart automatically. If stopped, tell Nick the exact command he can run, for example:

```bash
hermes gateway start
```

Confirm Hermes config/source survived:

```bash
hermes config check
hermes config path
cd ~/.hermes/hermes-agent
git log -1 --oneline --decorate
git status --short
git diff --check
```

Compare sanitized config receipt against preflight. Run focused postflight tests/smokes and the compression timeout ad-hoc check again when available.

Audit logs strictly after the reboot/update timestamp for fresh hits:

```text
Compression aborted
Connection error
Responses stream exceeded
all fallbacks exhausted
invalid reasoning
Traceback
launchd
crash
permission denied
certificate verify failed
xcrun
```

Clean signoff:

```text
MAC UPDATE VERIFIED
```

Partial or failed signoff:

```text
MAC UPDATE PARTIALLY VERIFIED — blockers: <list>
MAC UPDATE FAILED VERIFICATION — rollback/recovery recommended. Blockers: <list>. Last known-good backup/patch receipts: <paths>.
```

## Verification Language Discipline

Use precise labels:

- "focused verification" for targeted tests/smokes that cover the changed or high-risk paths;
- "ad-hoc verification" for one-off scripts, doc/system contract checks, grep checks, or live probes that are not part of a canonical test suite;
- "canonical suite passed" only when a repository-defined test/lint/build suite actually ran and passed.

Do not claim a canonical suite passed unless it actually ran and passed. Do not report a full repository suite as green unless the full suite actually ran and passed.

## Reporting Template

For any preflight/postflight report, lead with the verdict, then receipts.

```text
VERDICT: READY FOR NICK UPDATE | READY FOR NICK MAC UPDATE | UPDATE VERIFIED | MAC UPDATE VERIFIED | BLOCKED/PARTIAL/FAILED

Receipts:
- Host / Hermes version:
- Gateway PID/status:
- Backup path/checksum:
- Patch receipt path or none:
- Sanitized routing/config summary:
- Focused verification:
- Ad-hoc verification:
- Recent log audit:

Nick action needed:
- <exact update/reboot/restart action Nick performs, if any>

Copy/paste postflight prompt:
```text
&lt;Ares/Atlas, ... update is done.>
```
```

Never include credential values in reports.

## Common Pitfalls

1. **Exact-phrase trap.** Nick does not need to paste the long prompt. Short semantic triggers must route to the correct phase.

2. **Agent-run update trap.** Readiness is not permission for the agent to update. Nick still runs macOS updates, Hermes updates, reboots, and gateway restarts.

3. **Gateway restart recursion.** The gateway is the channel carrying the conversation. Do not restart it merely because code changed; surface whether a restart is needed and wait for Nick's GO.

4. **Credential receipt leak.** Sanitized receipts record keys, providers, models, counts, hashes, and paths. They never record credential values.

5. **Overclaiming verification.** A doc grep or temp script is ad-hoc verification, not canonical suite green.

6. **Mac route is not a Hermes update.** A macOS update can break launchd, CLT, Python, certificates, or permissions even if Hermes code did not change. Use the Mac Host Update route for host/reboot risk.

## Verification Checklist

- [ ] Correct route selected from semantic trigger.
- [ ] Operator boundary repeated in the response.
- [ ] Preflight created backups/patch receipts where needed.
- [ ] Sanitized config/routing/gateway receipt captured without secrets.
- [ ] Focused tests/smokes run or blockers reported.
- [ ] Ad-hoc checks labeled as ad-hoc.
- [ ] Preflight response includes the matching copy/paste postflight prompt.
- [ ] Postflight compares against preflight receipt.
- [ ] No gateway restart/update/reboot/credential mutation happened without explicit Nick GO.
