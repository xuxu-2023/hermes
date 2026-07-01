---
name: ponytail-audit
description: >
  Use when auditing a repository or subsystem for over-engineering. Produces a
  ranked, read-only list of dependencies, abstractions, wrappers, config, and
  custom code that can likely be deleted or simplified.
version: 1.0.0
author: Hermes Agent (adapted from DietrichGebert/ponytail)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [audit, code-review, simplification, yagni, dependencies, refactor]
    related_skills: [ponytail, ponytail-review, simplify-code]
---

# Ponytail Audit — Repo-Wide Over-Engineering Audit

## Overview

`ponytail-audit` is `ponytail-review` for a whole repository or subsystem. It
scans beyond the current diff and returns a ranked list of code that may be
safe to delete, reuse, or replace with simpler stdlib/native behavior.

This is a **read-only report**. It does not apply fixes unless the user asks for
a follow-up implementation.

This skill is adapted for Hermes from
[DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail), MIT
licensed.

## When to Use

Use this when the user asks:

- "ponytail-audit", "audit for over-engineering", "find bloat"
- "what can I delete from this repo?", "where are we overbuilt?"
- "check dependencies/abstractions/wrappers/config for YAGNI"
- to simplify a subsystem before a cleanup PR

Do **not** use this as a correctness/security audit. If you notice a correctness
or security issue, report it briefly as `normal-review:` and recommend a normal
review pass.

## Scope Selection

Pick the narrowest scope that matches the request:

```bash
# Whole repo summary
git status --short --branch

# Recent branch scope; replace origin/main with the detected default branch
git diff --stat origin/main...HEAD

# Search for common bloat classes
# Use search_files/read_file tools where available instead of dumping the tree.
```

Good starting points:

- dependency manifests (`package.json`, `pyproject.toml`, `requirements*.txt`,
  `Cargo.toml`, `go.mod`)
- directories named `utils`, `helpers`, `services`, `managers`, `adapters`,
  `providers`, `factories`, `interfaces`, `abstractions`
- config files and feature flags
- wrappers that only delegate
- files/components/classes with one caller
- hand-rolled parsing, validation, formatting, retry, debounce, cache, or date
  logic

## Tags

Use the same tags as `ponytail-review`:

- `delete:` dead code, unused flexibility, speculative feature, stale flag,
  unused config. Replacement: nothing.
- `reuse:` duplicate of existing helper, constant, component, type, or pattern.
  Name what to reuse.
- `stdlib:` hand-rolled thing the standard library already ships. Name the
  function/module.
- `native:` code/dependency doing what the platform/framework/database/browser
  already does. Name the native feature.
- `yagni:` abstraction with one implementation, config nobody sets, factory with
  one product, layer with one caller.
- `shrink:` same behavior, fewer lines. Show the shorter replacement.

## Output Format

Rank findings biggest cut first:

```text
1. <tag> <what to cut>. <replacement>. [path:line or path + symbol]
2. <tag> <what to cut>. <replacement>. [path:line or path + symbol]
...
net: roughly -<N> lines, -<M> deps possible.
```

If nothing meaningful is cuttable:

```text
No meaningful over-engineering findings in this scoped audit.
```

For uncertain findings, include `verify:` with the check needed before applying:

```text
3. yagni: FeatureFlagRegistry has one flag and one caller. Inline the constant. verify: search confirms no external plugin reads it. [src/flags.py]
```

## Boundaries

Do not recommend deleting or shrinking:

- trust-boundary validation
- auth, permission, and secret-handling checks
- error handling that prevents data loss or corrupt state
- migrations/backward compatibility for existing users/data
- accessibility basics
- one small smoke test or self-check for non-trivial logic
- production observability that operators rely on
- extension points with real external consumers, even if this repo has one
  implementation

If a candidate might be public API, plugin surface, CLI behavior, migration
path, or user data compatibility, mark it `verify:` instead of presenting it as
safe to cut.

## Common Pitfalls

1. **Auditing the whole repo when the user named a subsystem.** Scope first;
   broad audits become vague.
2. **Counting possible line savings as fact.** Estimate conservatively, use
   ranges when uncertain, and avoid fake precision.
3. **Mistaking narrow-waist extension points for YAGNI.** In Hermes especially,
   plugin/skill/tool boundaries may have external consumers.
4. **Flagging safety code.** Security, data safety, accessibility, and minimal
   tests are not bloat.
5. **Applying fixes during the audit.** Report first. Implementation is a
   separate request.

## Verification Checklist

- [ ] Confirmed branch/status before auditing a repo
- [ ] Scoped the audit to the user's requested repo/subsystem
- [ ] Searched for existing helpers and callers before findings
- [ ] Ranked findings by likely impact
- [ ] Marked uncertain public API/extension-surface cuts with `verify:`
- [ ] Preserved safety, correctness, accessibility, migration, and test code
- [ ] Returned a read-only report unless explicitly asked to modify files

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
