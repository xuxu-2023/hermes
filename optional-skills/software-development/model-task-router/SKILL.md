---
name: model-task-router
description: Classify tasks and route to the best-fit model. Coding-heavy → terminal-spawned agent, orchestration → current model directly, mechanical → delegate_task. Configure your preferred coding and architecture models below.
version: 2.0.0
author: Sugumaran Balasubramaniyan (https://github.com/Sugumaran-Balasubramaniyan)
license: MIT
metadata:
  hermes:
    tags: [model-routing, task-classification, delegation, multi-model]
    related_skills: [subagent-driven-development]
---

# Model Task Router

Classify incoming user requests by task type and route to the appropriate model. No manual `/model` switches required.

## Why Task-Based Routing

Different models have different strengths. Benchmarks like DeepSWE and Terminal-Bench show meaningful divergence across task types — a model strong at tool orchestration may not be the best choice for multi-file code generation, and vice versa. Rather than switching models manually, this skill classifies tasks and dispatches automatically.

### Data Caveats

The benchmark data behind this skill has known limitations:

- **Effort tuning inconsistency**: Some models were benchmarked without the effort-level optimization applied to competitors. Scores for those models may understate capability.
- **Provider-level quirks**: OpenRouter privacy guardrails (data-training concerns) can block certain providers, causing benchmark failures unrelated to model capability.
- **Limited community replication**: Independent replications have produced varying results. The published numbers are directional, not definitive.

**Treat published scores as a starting point, not gospel.** If your experience with a model differs from the data, adjust the routing table below.

## Published Model Performance

DeepSWE (real-world coding, contamination-free) and Terminal-Bench (agentic CLI) scores as of June 2026:

### DeepSWE — Real-World Coding

| Model | SWE-bench Pro | DeepSWE | Notes |
|-------|:------------:|:-------:|-------|
| GPT-5.5 (xhigh) | 58.6% | 70% | Best-in-class |
| GPT-5.4 (xhigh) | 57.7% | 56% | Strong general coding |
| Claude Opus 4.7 | 64.3% | 54% | Competitive coding |
| Claude Sonnet 4.6 | — | 32% | Mid-tier |
| Gemini 3.5 Flash | — | 28% | Budget-aware option |
| GPT-5.4-Mini | — | 24% | Fast, cheap, delegated |
| Kimi K2.6 | — | 24% | Budget option |
| DeepSeek V4-Pro | 55.4% | ~8%* | See caveats above |

*\*V4-Pro was run at default effort (no tuning) and may have been affected by OpenRouter guardrail 404s. Score directionally suggests lower coding throughput, but magnitude is uncertain. Community replications report 5–8%. Awaiting re-run with proper effort tuning.*

### Terminal-Bench — Agentic CLI / Tool Orchestration

| Model | Score | $/1M Output |
|-------|:-----:|:-----------:|
| GPT-5.5 | 82.7% | $30.00 |
| DeepSeek V4-Pro | 67.9% | $0.87 |

Source: [DeepSWE leaderboard](https://deepswe.datacurve.ai/), [explainx.ai DeepSWE writeup](https://explainx.ai/blog/deepswe), [Terminal-Bench](https://kilo.ai/leaderboard)

## Routing Table

| Task Category | How | Why |
|--------------|-----|-----|
| **Code Generation** (implement, fix, refactor, PR, tests) | terminal-spawn `hermes chat` with coding model | Coding models have proven DeepSWE results |
| **Hard Architecture** (system design, complex debugging, security) | terminal-spawn `hermes chat` with architecture model | Highest reasoning capability |
| **Orchestration** (tools, shell, file ops, diagnostics) | Current model directly | Tool-calling efficiency |
| **Research** (web search, docs, analysis, planning) | Current model directly | Reasoning + web tools inline |
| **Mechanical** (grep, find, run tests, simple edits) | `delegate_task` (default subagent model) | Fast, cheap, delegated |

### Configuring Your Models

Set these in your `~/.hermes/config.yaml` or adapt based on what's available:

```yaml
# Coding model — used for implement/fix/refactor/PR tasks
coding_model: openrouter/openai/gpt-5.4

# Architecture model — used for system design, hard debugging, security
architecture_model: openrouter/openai/gpt-5.5

# Your current (orchestrator) model — handles tool calls, research, planning
# The skill uses whatever model is active; no override needed.
```

**If you don't configure these, the skill defaults to:**
- Coding: `openrouter/openai/gpt-5.4` (56% DeepSWE)
- Architecture: `openrouter/openai/gpt-5.5` (70% DeepSWE)
- Orchestration/Research: your current model (whatever is active)
- Mechanical: your configured `delegation.model` (or system default)

## Decision Tree

Priority order: Coding > Architecture > Mechanical > Research > Orchestration.
First match wins; ties broken left-to-right by priority order. User can override by prefixing their message with `[route: <category>]`.

```
User says: "..."
│
├─ [route: code] or [route: architecture] or [route: mechanical] or [route: direct]
│  → User override — use the specified route, skip keyword classification
│
├─ Keywords: "implement", "build", "create", "write code", "fix bug",
│           "refactor", "add feature", "PR", "patch"
│  AND task modifies or creates source files
│  → ROUTE: Coding → terminal-spawn hermes chat --model coding_model
│
├─ Keywords: "architecture", "design system", "how would you structure",
│           "security review", "complex debugging"
│  AND task is high-stakes, multi-system, or multi-file
│  → ROUTE: Architecture → terminal-spawn hermes chat --model architecture_model
│
├─ Keywords: "search for", "find all", "grep", "list files",
│           "run tests", "check if", "what's the content of"
│  AND task is purely mechanical (read-only or single command)
│  → ROUTE: Mechanical → delegate_task with default subagent model
│
├─ Keywords: "research", "summarize", "what is", "explain",
│           "look up", "search the web for"
│  → ROUTE: Research → handle directly with current model
│
└─ Everything else: tool calls, shell, navigation, diagnostics, config, planning
   → ROUTE: Orchestration → handle directly with current model
```

### User Override

Prefix any message with `[route: code]`, `[route: architecture]`, `[route: mechanical]`, or `[route: direct]` to override automatic classification. Example:

> [route: direct] Fix this typo in README.md

This forces direct handling even though "fix" would normally trigger a coding dispatch. Use for small one-liner fixes.

## Dispatch Mechanism

**Important:** `delegate_task` does not currently support per-task model override ([issue #18591](https://github.com/NousResearch/hermes-agent/issues/18591)). To dispatch a task to a specific model, use `terminal` to spawn a fresh agent with `hermes chat`. For mechanical tasks, `delegate_task` with the default subagent model is correct.

### For Coding Tasks → terminal-spawned agent

```bash
# Spawn a fresh agent with the coding model
hermes chat -q "<full task description with file paths and context>" \
  --model openrouter/openai/gpt-5.4 \
  --provider openrouter \
  --toolsets terminal,file,web
```

**IMPORTANT:** When dispatching a coding task:
1. Include ALL relevant context in the `-q` string: file paths, error messages, constraints, existing code snippets
2. Set a generous timeout: coding tasks can take 5-15 minutes
3. If the task is large, spawn in background and poll: `terminal(background=true, notify_on_complete=true)`
4. Verify the spawned agent's results before reporting to the user — agents can hallucinate completion
5. If the spawned agent fails, try once more with more specific instructions, then report the blocker

### For Architecture Tasks → terminal-spawned agent

```bash
hermes chat -q "<full architecture question with context>" \
  --model openrouter/openai/gpt-5.5 \
  --provider openrouter \
  --toolsets terminal,file,web
```

### For Mechanical Tasks → delegate_task (default subagent model)

```python
delegate_task(
    goal="<mechanical task>",
    context="""<what to search, where, what to report back>""",
    toolsets=["terminal", "file"]
)
```

Good for: searching codebase for patterns, running test suites, simple file modifications, finding and listing files.

### For Orchestration/Research → Handle Directly

Use your current model inline. Do NOT delegate or spawn. Handle tool calls directly.

See also: `subagent-driven-development` skill for the full delegation workflow (spec review → quality review → verify).

## Dispatch Checklist

When spawning a coding or architecture task via `hermes chat`:

1. **Pack all context**: The spawned agent has no memory of your conversation. Include file paths, error messages, constraints, and relevant code.
2. **Verify results**: Agents self-report "completed" when they haven't. Always read the modified file or run the test before reporting to the user.
3. **Retry once if needed**: If the spawned agent fails, try once more with more specific instructions, then report the blocker.
4. **Announce the routing**: Short message like "Routing to GPT-5.4 for implementation..." keeps the user informed.

## Anti-Patterns

- **Do NOT delegate orchestration.** The current model handles tool-calling; delegation adds overhead for no benefit.
- **Do NOT spawn agents for one-liner fixes.** Use `[route: direct]` for trivial edits.
- **Do NOT skip verification.** Spawned agents can hallucinate completion. Read files, run tests, confirm.
- **Do NOT spawn without context.** Pack every relevant detail into the `-q` string.
- **Do NOT use the architecture model for routine coding.** It's overkill; save it for hard problems.

## Pitfalls

- **`delegate_task` has no model parameter**: Per-task model override on `delegate_task` is not yet implemented ([issue #18591](https://github.com/NousResearch/hermes-agent/issues/18591)). Use `hermes chat -q --model ...` via `terminal()` for model-specific dispatch.
- **Spawned agents have no session memory**: When using `hermes chat`, the spawned process starts fresh. Pack ALL context into the `-q` string — file paths, error messages, constraints, relevant code.
- **Background processes need monitoring**: For long tasks, use `terminal(background=true, notify_on_complete=true)` and verify results with `process(action='wait')` or `process(action='poll')`.
- **Rate limits**: GPT-5.4 and GPT-5.5 share OpenAI rate limits via OpenRouter. If one dispatch fails, try the other or fall back to the current model.
- **Context budget**: Each spawned agent session adds cost and latency. Don't dispatch trivial tasks; use `[route: direct]` instead.

## Related Hermes Issues

- [#30652](https://github.com/NousResearch/hermes-agent/issues/30652) — Dynamic model routing based on task complexity (open, P3)
- [#16525](https://github.com/NousResearch/hermes-agent/issues/16525) — Expose model_switch as agent-callable tool (open, P3)
- [#18591](https://github.com/NousResearch/hermes-agent/issues/18591) — Per-task model override for delegate_task (open)

## References

- `references/deepswe-routing-data.md` — Full benchmark data, cost analysis, and caveats
