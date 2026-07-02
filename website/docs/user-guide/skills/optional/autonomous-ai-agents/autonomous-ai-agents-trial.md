---
title: "Trial — No \"done\" until each claim has a re-runnable receipt"
sidebar_label: "Trial"
description: "No \"done\" until each claim has a re-runnable receipt"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Trial

No "done" until each claim has a re-runnable receipt.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/autonomous-ai-agents/trial` |
| Path | `optional-skills/autonomous-ai-agents/trial` |
| Version | `0.4.1` |
| Author | Da7-Tech |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `verification`, `judging`, `false-done`, `quality-assurance`, `evidence`, `high-stakes` |
| Related skills | [`subagent-driven-development`](/docs/user-guide/skills/optional/software-development/software-development-subagent-driven-development), [`test-driven-development`](/docs/user-guide/skills/bundled/software-development/software-development-test-driven-development), [`systematic-debugging`](/docs/user-guide/skills/bundled/software-development/software-development-systematic-debugging) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Trial Skill

Trial is one rule for how an agent finishes work: before it says "done", every
claim must be bound to a **receipt** — the exact command it ran, that command's
exit status, and the decisive lines of output, quoted in the report. It targets
*false-done*: work that passes the builder's own green suite while the bug ships,
because the suite never exercised the claimed behavior. It is not a tool to run,
not a linter, and not a substitute for tests — it is a discipline applied on top
of them, with scrutiny that scales to the risk of the change.

## When to Use

- The agent is about to claim a task is "done" / "fixed" / "complete" / "shipped".
- High-stakes work (auth, payments, permissions, user data, migrations, deletes).
- A bug report where a passing test suite is not enough proof the bug is gone.
- Do NOT add ceremony to a trivial change — the fast path below is the default.

## Prerequisites

- None to run the rule itself. The optional high-stakes "fresh judge" step uses
  the native `delegate_task` tool when the platform can spawn subagents; when it
  can't, the same check is done as a self-review pass (no external dependency).

## How to Run

There is nothing to install. The rule is applied by the agent as it works:
before writing "done", it binds each acceptance criterion to a receipt and, for
high-stakes work, obtains a fresh-eyes verdict first. A claim with no receipt is
`NOT_PROVEN` — rejected by the agent itself, before any judge reads it.

## Quick Reference

A **receipt** = command run + exit status + decisive output lines, quoted.

| Verdict | Meaning |
|---|---|
| `ACCEPTED` | every claim bound to passing evidence that covers every criterion |
| `NOT_PROVEN` | a claim cites evidence but the receipt is missing or doesn't cover it |
| `NEEDS_FIX` | the evidence runs but exposes a defect |
| `ESCALATE` | high-stakes work that needs a fresh independent verdict before shipping |

**Coverage beats green:** a receipt only counts if the quoted command would have
*failed* were the claim false. If no test exercises the criterion, write one and
watch it fail on the old behavior first.

## Procedure

1. **Frame** — restate the goal as testable acceptance criteria at the boundary
   where the user feels it ("an expired session is redirected", not "the helper
   returns true").
2. **Build** — fix root causes; grep for every copy or caller of the logic touched.
3. **Prove** — for each criterion run the command that would fail if the claim
   were false; quote command + exit status + decisive output in the report.
4. **Scale scrutiny to risk** — trivial change: run the proving check and ship.
   High-stakes: get a fresh-eyes verdict first (via `delegate_task`, given only
   the claim + receipts and told to reject unless evidence covers the claim; or,
   if subagents aren't available, a separate adversarial self-review that names
   the exact test/line covering each criterion or downgrades it to `NOT_PROVEN`).
5. **Deliver** — map each criterion to its receipt. Never ship a known blocker; a
   red test you can explain beats a green claim you can't.

## Pitfalls

- **Speed is a feature.** A simple fix growing a tribunal, or stalling at "gate
  3/8", is a bug in the process, not rigor — take the fast path.
- **A receipt is self-reported, not cryptographic proof.** It doesn't stop a
  model determined to fabricate output; it makes drift into an unfounded "done"
  turn into an outright, re-runnable, detectable lie. (Earlier versions required
  a *hash* of output — removed in 0.4.0: output has timestamps so the hash was
  never reproducible, and a self-reported hash is no harder to invent than a
  self-reported pass.)
- **Never rubber-stamp** the high-stakes list above, or any edit to a test that
  makes it pass — those always get the adversarial pass.

## Verification

The claim that Trial changes finishing behavior is measured, not asserted: on
real headless sessions fixing a bug whose suite is green while the bug ships,
agents without Trial left a covering test 4/6 and quoted a verbatim receipt 0/6;
with Trial, 6/6 and 6/6. The trap fixture, the deterministic grader (behavioral
+ covering-test metrics), and the verbatim prompts are all reproducible in the
tool's repo: https://github.com/Da7-Tech/trial (`benchmarks/`).
