---
name: ponytail-review
description: >
  Use when reviewing a diff only for over-engineering. Finds what to delete or
  replace with existing code, stdlib, native features, or a smaller equivalent;
  returns one concrete line per finding.
version: 1.0.0
author: Hermes Agent (adapted from DietrichGebert/ponytail)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [code-review, simplification, yagni, dependencies, refactor]
    related_skills: [ponytail, simplify-code, requesting-code-review]
---

# Ponytail Review — Over-Engineering Diff Review

## Overview

Review a diff for unnecessary complexity. This is not a full correctness,
security, or performance review. It asks one narrow question: **what can safely
be deleted, reused, or made smaller?**

The best outcome of this review is a shorter diff. Each finding should name the
location, what to cut, and what replaces it.

This skill is adapted for Hermes from
[DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail), MIT
licensed.

## When to Use

Use this when the user says or implies:

- "ponytail-review", "review for over-engineering", "is this overbuilt?"
- "what can we delete?", "over-engineering-only review", "YAGNI review"
- a PR/diff added new dependencies, wrappers, abstractions, config, factories,
  custom parsers, custom validators, or duplicated helpers

Do **not** use this as the only pre-merge review for risky changes. Pair it with
`requesting-code-review` or normal review for correctness/security concerns.

## Input to Review

Prefer the smallest relevant diff source:

```bash
# Uncommitted work
git diff

# Staged work
git diff --staged

# Current branch against the default branch
git diff origin/main...HEAD

# A specific file
git diff -- path/to/file
```

If the diff is huge, split by subsystem rather than issuing vague repo-wide
claims.

## Tags

Use one tag per finding:

- `delete:` dead code, unused flexibility, speculative feature, unreachable
  branch, config nobody sets. Replacement: nothing.
- `reuse:` newly added code duplicates an existing helper, constant, component,
  type, or pattern in the codebase. Name the existing thing.
- `stdlib:` hand-rolled thing the standard library already ships. Name the
  function/module.
- `native:` code or dependency doing what the platform/framework/database/browser
  already does. Name the native feature.
- `yagni:` abstraction with one implementation, config with one value, factory
  with one product, layer with one caller, option nobody requested.
- `shrink:` same behavior, fewer lines. Show the smaller form.

## Output Format

One line per finding:

```text
<file>:L<line>: <tag> <what>. <replacement>.
```

Use a line range, hunk, or path-level pointer when exact line numbers are not
available, but still cite concrete evidence.

End with:

```text
net: roughly -<N> lines possible, -<M> deps possible.
```

If there is nothing meaningful to cut:

```text
No over-engineering findings in this scope. Run normal verification before shipping.
```

## Examples

Bad:

```text
This EmailValidator class might be more complex than necessary, have you
considered whether all these validation rules are needed?
```

Good:

```text
auth/email.py:L12-L38: stdlib: 27-line EmailValidator class for a signup form. Keep "@" check here; confirmation email is real validation.
ui/date.tsx:L4: native: date-picker dependency for one field. Use <input type="date">.
repo.py:L88: yagni: AbstractRepository with one implementation. Inline until the second implementation exists.
cache.py:L52-L71: delete: retry wrapper around a local pure function. Nothing replaces it.
utils.py:L30-L44: shrink: manual loop builds dict. dict(zip(keys, values)).
```

## Boundaries

Never flag these as bloat just because they add lines:

- trust-boundary validation
- auth, permission, and secret-handling checks
- error handling that prevents data loss or corrupt state
- migrations/backward compatibility for existing users/data
- accessibility basics
- one small smoke test or self-check for non-trivial logic
- observability required to operate production code

Correctness bugs, security holes, and performance regressions are out of scope
for the Ponytail score. If you notice one while inspecting complexity, report it
briefly and label it `normal-review:` so it is not hidden.

This skill lists findings only. Do not apply changes unless the user explicitly
asks you to simplify the diff after the review.

## Common Pitfalls

1. **Vague findings.** Every finding needs a file/line and a concrete
   replacement.
2. **Deleting tests.** A small behavior check is usually the ponytail minimum,
   not bloat.
3. **Ignoring existing code.** Search before claiming `reuse:`; name the helper.
4. **Treating style as simplification.** Do not nitpick formatting or naming.
5. **Using this as a security review.** It is intentionally narrow. Run normal
   verification separately before commit/push.

## Verification Checklist

- [ ] Reviewed a concrete diff, not vibes
- [ ] Searched for existing helpers before `reuse:` findings
- [ ] Kept findings to over-engineering/complexity only
- [ ] Preserved safety, correctness, accessibility, and tests
- [ ] Reported one line per finding with file/line and replacement
- [ ] Ended with possible net line/dependency reduction or `No over-engineering findings in this scope. Run normal verification before shipping.`

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
