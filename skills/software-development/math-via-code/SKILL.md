---
name: math-via-code
description: "Use when computing sums, percentages, ratios, projections, financial models, unit conversions, or any multi-step arithmetic. Offloads all math to code execution."
version: 2.0.0
author: Tom Mulkins
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [math, accuracy, arithmetic, computation, financial-modeling, code-execution]
    related_skills: [ocr-and-documents, google-workspace]
---

# Math Via Code

## Overview

LLM arithmetic accuracy degrades sharply past ~150k context tokens and is unreliable even below that. This skill enforces a hard rule: all math — no matter how "simple" — goes through `execute_code` or a Python script. The only exception is a single operation on two numbers (e.g. `5 * 3`).

The payoff is zero arithmetic errors on financial models, SDE calculations, projections, rollups, and any multi-step computation. Code is always right; in-head math is not.

## When to Use

- Any computation involving 3+ numbers, even "simple" addition
- Financial analysis: SDE, P&L, margins, royalties, annualizations
- Growth rates, ratios, percentages, weighted averages
- Unit conversions, projections, multi-facility rollups
- Reading data from files or APIs and computing derived values
- Whenever the user provides numbers and expects a computed result

**Don't use for:** Trivial single-operation arithmetic on two numbers (`5 * 3`, `100 - 20`). Everything else → code.

## Procedure

### 1. Determine inputs

See **Data Sources** below for source-specific guidance. The universal rule: capture numbers as variables, never re-type from memory.

### 2. Write the calculation

Use `execute_code` for simple in-memory math. Use `terminal` with the project's venv Python when you need installed packages (openpyxl, pandas, etc.).

```python
# Example: SDE from raw inputs
revenue = 327158
direct_costs = 192193
gp = revenue - direct_costs

overhead_items = [960, 1351.92, 9000, 1440, 1137.24]
overhead_royalties = round(revenue * 0.07, 2)
total_overhead = sum(overhead_items) + overhead_royalties

owner_salary = 4500 * 12
owner_payroll_tax = 370 * 12
owner_auto = 364 * 12
owner_comp = owner_salary + owner_payroll_tax + owner_auto

net_profit = gp - total_overhead - owner_comp
sde = net_profit + owner_comp
```

### 3. Run it

- `execute_code` for pure-Python math (no external packages needed).
- `terminal` when you need external packages (pandas, openpyxl, etc.) — use your project's Python environment.

### 4. Verify (mandatory)

Every calculation must include verification. Two approaches:

**Self-check assertions** (add to the script):
```python
assert abs(gp + direct_costs - revenue) < 0.01, "GP + DOC should equal revenue"
assert abs(sde - (net_profit + owner_comp)) < 0.01, "SDE = net + add-back"
assert abs(royalties - round(revenue * 0.07, 2)) < 0.01, "Royalties = 7%"
```

**Cross-check against source** (when data comes from a document, workbook, or API):
```python
assert abs(computed_sde - source_sde) < 0.01, \
    "SDE mismatch: computed={}, source={}".format(computed_sde, source_sde)
```

### 5. Report

Present results with enough context to verify:
- Show inputs
- Show computed outputs
- Show assertion results (pass/fail)
- Flag if computed values differ from source

## Common Calculations

| Calculation | Formula |
|-------------|---------|
| Gross margin | `(revenue - cogs) / revenue * 100` |
| SDE | `net_profit + owner_salary + payroll_tax + auto + other_addbacks` |
| Annualized | `ytd_total / months_elapsed * 12` |
| Growth rate | `(current - prior) / prior * 100` |
| Royalties | `revenue * royalty_rate` |
| Multi-facility rollup | `sum(facility[i] for i in facilities)` |
| Weighted average | `sum(val * weight) / sum(weights)` |

## Data Sources

The source of the numbers doesn't change the rule — compute in code, verify against source.

**Numbers in conversation:** Capture directly as variables. Don't re-type from memory.

**From a file (Excel, CSV, PDF):** Read programmatically. Don't copy-paste partial numbers and do the rest in your head.

**From a web page or API:** Extract values into variables, then compute.

### Excel / .xlsx specifics

1. Use your project's Python environment (not the sandbox) when you need openpyxl or pandas installed.
2. Always `data_only=True` to get computed values, not formulas:
   ```python
   wb = openpyxl.load_workbook(path, data_only=True)
   ```
3. Strip whitespace from labels — Excel cell values often have leading spaces:
   ```python
   label = cell.value.strip()  # NOT cell.value
   ```
4. Write scripts to `/tmp/` and run the file rather than passing complex f-strings through subprocess.
5. Verify facility sums match totals — sum individual facilities and compare to the "All Customers" row.

## Common Pitfalls

1. **Sandbox Python missing packages.** Use your project's Python environment via `terminal` when you need external packages like pandas or openpyxl.
2. **Excel labels have leading/trailing spaces.** Always `.strip()` label strings before comparison.
3. **`data_only=False` returns formulas not values.** Pass `data_only=True` to `load_workbook`.
4. **f-strings with escaped quotes break in subprocess.** Write script to `/tmp/` file, run the file.
5. **Rounding too early.** Keep full precision during computation; round only for display.
6. **Transcribing numbers by hand from a source.** Read the source programmatically — hand transcription introduces errors.
7. **Using made-up data to "test."** Always use real data from the source.
8. **Doing "quick" mental math on 3+ numbers.** Don't. Write code. It takes 10 seconds and prevents wrong answers.

## Verification Checklist

- [ ] All arithmetic executed via `execute_code` or a Python script — nothing computed in-head
- [ ] Inputs captured as variables, not re-typed from memory
- [ ] Self-check assertions pass (or cross-check against source values)
- [ ] Rounded for display only; full precision used during computation
- [ ] If data came from a document/workbook/API, computed result compared back to source
