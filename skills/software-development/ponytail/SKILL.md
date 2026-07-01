---
name: ponytail
description: >
  Use when the user explicitly asks for Ponytail, YAGNI, minimal implementation,
  or the shortest safe path. Applies a lazy-senior-dev ladder: reuse existing
  code, prefer stdlib/native features, and avoid speculative abstractions.
version: 1.0.0
author: Hermes Agent (adapted from DietrichGebert/ponytail)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [coding, simplification, yagni, minimalism, refactor]
    related_skills: [simplify-code, requesting-code-review, test-driven-development]
---

# Ponytail — Minimal Code That Works

## Overview

Ponytail is a coding discipline for avoiding unnecessary code. It channels the
lazy senior developer who has seen every over-engineered codebase and knows the
best code is the code never written.

Lazy means efficient, not careless. This skill shortens the solution only after
you understand the problem and the code path it touches. It never removes
security, correctness, accessibility, data-loss protection, or explicit user
requirements.

This skill is adapted for Hermes from
[DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail), MIT
licensed.

## When to Use

Use this when the user explicitly asks for one of these modes:

- "ponytail", "lazy mode", "YAGNI", "minimal solution", "simplest safe
  solution", "do less", or "shortest safe path"
- an implementation that should avoid new dependencies, framework layers,
  wrappers, factories, config, or speculative abstractions
- a quick check for whether existing code, stdlib, or native platform features
  already cover the request

Do **not** auto-run this for every implementation task. Load it when the user
asks for minimalism/YAGNI or the requested change visibly risks bloat.

Do **not** use this to skip investigation. Read the task and nearby code first.
A tiny diff in the wrong place is not lazy; it is a second bug.

## The Ladder

Stop at the first rung that actually works:

1. **Does this need to exist at all?** Speculative need = skip it and say why.
2. **Already in this codebase?** Reuse the existing helper, util, type, pattern,
   config, or component. Look before writing a new one.
3. **Stdlib does it?** Prefer built-in language/library support.
4. **Native platform feature covers it?** Browser, CSS, database constraints,
   shell, OS, or framework defaults beat custom code.
5. **Already-installed dependency solves it?** Use it. Do not add a dependency
   for something a few clear lines can do.
6. **Can it be one line?** One line.
7. **Only then:** write the minimum code that works.

The ladder is a reflex, not a research project. If two rungs work, take the
higher one and move on.

## Bug Fix Boundary

For bugs, defer to `systematic-debugging` and `test-driven-development` for the
full investigate-first and test-first workflow. Ponytail adds only this bias:
once the root cause is known, prefer one fix in the shared path over repeated
symptom patches in each caller.

## Rules

- No unrequested abstractions: no interface with one implementation, no factory
  for one product, no config for a value nobody sets.
- No scaffolding "for later". Later can scaffold for itself.
- Deletion over addition when behavior stays correct.
- Boring over clever. Clever is what someone decodes at 3am.
- Fewest files possible. Shortest working diff wins only after the real flow is
  understood.
- Prefer explicit local code over a new dependency unless the dependency is
  already present or the domain is genuinely hard.
- Two stdlib/native options, same size? Take the one that handles edge cases
  correctly.
- If the user explicitly insists on the fuller version after you name the
  simpler path, build the requested version without re-arguing.

## Output Style

For implementation tasks:

1. Make the smallest safe change.
2. Run the relevant narrow verification.
3. In the response, prefer a few short bullets: what changed, what was
   intentionally skipped, and when to add the skipped complexity.

Pattern:

```text
changed: <smallest working fix>
skipped: <dependency/abstraction/config not added>
add when: <specific trigger that makes the complexity real>
```

Do not suppress explanations the user explicitly asks for. Reports,
walkthroughs, PR bodies, and verification notes are not prose debt.

## Intensity

| Level | Use when | Behavior |
|-------|----------|----------|
| **lite** | User wants a normal implementation but may appreciate a simpler alternative. | Build what's asked; name the lazier alternative in one line. |
| **full** | Default when this skill is loaded. | Enforce the ladder: existing code → stdlib → native → installed deps → one line → minimum. |
| **ultra** | User explicitly asks for aggressive deletion/YAGNI. | Challenge requirements before adding code; prefer deletion and one-liners. |

Unless the user states a level, use **full** for the current task only. Hermes
skills are task-scoped; do not claim Ponytail is globally enabled across future
sessions.

## When Not to Be Lazy

Never simplify away:

- trust-boundary input validation
- authentication, authorization, secret handling, or other security controls
- error handling that prevents data loss or corrupt state
- accessibility basics in user-facing UI
- durable observability needed to operate production systems
- migrations or compatibility paths that protect existing users/data
- tests/checks for non-trivial logic, money paths, security paths, parsers, or
  concurrency
- user requirements that are explicit after you state the simpler alternative

Hardware and physical systems are also not idealized software. Keep calibration
knobs, tolerances, and safety margins when real sensors, clocks, motors, or
human environments are involved.

## Minimal Verification

Lazy code without a check is unfinished. For non-trivial logic, leave the
smallest runnable check that proves the behavior:

- one focused unit test if the repo already has a test suite
- an existing narrow test command for the touched subsystem
- a tiny `assert`/self-check only when the project has no test harness

Trivial one-liners may not need new tests, but still run the relevant existing
lint/type/build check when available.

## Common Pitfalls

1. **Skipping comprehension.** The ladder starts after reading the relevant
   code path. Do not use "small diff" as an excuse to patch the wrong place.
2. **Cutting safety.** Validation, auth, data-loss prevention, accessibility,
   and real tests are not bloat.
3. **Adding new dependencies casually.** Prefer stdlib/native/existing helpers.
4. **Inventing abstractions for future variants.** Wait for the second concrete
   implementation before extracting an interface or factory.
5. **Over-explaining the minimal choice.** Name what was skipped and the trigger
   for adding it; avoid a design essay unless asked.

## Verification Checklist

- [ ] Checked whether the code needs to exist at all
- [ ] Searched for existing helpers/patterns before adding new code
- [ ] Used stdlib/native/platform support where it fits
- [ ] Avoided new dependencies and speculative abstractions
- [ ] Preserved security, correctness, accessibility, and data safety
- [ ] Added or ran the smallest relevant verification
- [ ] Reported skipped complexity with a concrete future trigger

## Attribution

Adapted from [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)
under the MIT License.

Upstream notice preserved from the original project:

```text
MIT License

Copyright (c) 2026 DietrichGebert

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
