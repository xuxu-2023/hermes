# DeepSWE Routing Data — Reference

> **Updated 2026-06-11**: Full model table with published and community-verified data. All solve-rate claims from DeepSWE issue #21 were retracted by the issue author (2026-06-08) after discovering a test-setup error. The cost-correction analysis in that issue remains valid and is included below. Treat DeepSWE scores as directional — effort tuning, provider routing, and limited replication all introduce uncertainty.

## The Evidence

DeepSWE ([deepswe.datacurve.ai](https://deepswe.datacurve.ai)) is a contamination-free, real-complexity coding benchmark. Tasks are written from scratch, require ~5.5x more code than SWE-bench Pro, and span 91 repositories across 5 languages.

## Full DeepSWE Results (Published + Community)

| Model | SWE-bench Pro | DeepSWE | Delta | Notes |
|-------|:------------:|:-------:|:-----:|-------|
| GPT-5.5 (xhigh) | 58.6% | **70%** | +11 | Best-in-class |
| GPT-5.4 (xhigh) | 57.7% | **56%** | −2 | Strong general coding |
| Claude Opus 4.7 | 64.3% | **54%** | −10 | Competitive coding |
| Claude Sonnet 4.6 | — | 32% | — | Mid-tier |
| Gemini 3.5 Flash | — | 28% | — | Budget-aware |
| GPT-5.4-Mini | — | 24% | — | Fast, cheap, delegated |
| Kimi K2.6 | — | 24% | — | Budget option |
| DeepSeek V4-Pro | 55.4% | ~8%* | −47 | See caveats below |

### V4-Pro Caveats

The ~8% DeepSWE score for V4-Pro comes with significant methodological concerns documented in [DeepSWE issue #21](https://github.com/datacurve-ai/deep-swe/issues/21):

1. **No effort tuning**: V4-Pro was run at `reasoning_effort: null` while all other models had tuned effort levels (xhigh, max, medium). Thinking mode was left on but unoptimized.
2. **OpenRouter guardrail 404s**: OpenRouter blocks DeepSeek by default (data-training privacy concern). API calls may have returned 404 errors rather than model failures.
3. **Limited replication**: Community member `ivanfioravanti` ran an independent test via direct DeepSeek API and got ~5.3% (similar direction). No independent confirmation with proper effort tuning exists yet.
4. **Solve-rate claims from issue #21 retracted**: The issue author retracted their own solve-rate analysis after discovering a test-setup error.

**Bottom line**: V4-Pro likely underperforms GPT-5.4/5.5 on coding tasks, but the magnitude of the gap is uncertain pending a re-run with proper effort tuning. Configure your routing accordingly.

## Cost Analysis (from DeepSWE Issue #21 — Valid)

DeepSeek V4-Pro pricing includes cache-hit rates at $0.0036/M (99.2% off cache-miss). DeepSWE's reported costs billed all tokens at the miss rate ($0.435/M). In real-world agent runs, ~78% of tokens are cache hits.

### Corrected Cost per Task (Cache-Adjusted)

| Model | DeepSWE | Reported Cost | Corrected Cost | Factor |
|-------|:-------:|:------------:|:--------------:|:------:|
| GPT-5.5 | 70% | $6.61/task | — | — |
| GPT-5.4 | 56% | $4.38/task | — | — |
| GPT-5.4-Mini | 24% | $2.08/task | — | — |
| DeepSeek V4-Pro | 8% | $4.22/task | ~$0.30/task | ~14× inflation |

Source: Reproducible analysis script in issue #21 by `agentecobuilder`. Verified independently.

**Key finding**: V4-Pro's per-task cost was inflated ~14× by ignoring cache-hit pricing. The real cost is very low (~$0.30/task). However, cost-per-task ignores solve rate — if a model requires many retries, the effective cost-per-solve may be higher despite low per-token pricing. Since the solve rate is uncertain (see caveats), cost-per-solve calculations are withheld pending a proper re-benchmark.

### Additional Cost Considerations

- **OpenRouter 5% fee** for high-usage accounts adds to all OpenRouter-routed models
- **Reasoning tokens**: Thinking-mode models generate hidden reasoning tokens that are billed but not shown in output token counts
- **Provider pricing volatility**: DeepSeek and other providers adjust pricing frequently; verify current rates

## Terminal-Bench (Agentic CLI)

| Model | Score | $/1M Output |
|-------|:-----:|:-----------:|
| GPT-5.5 | 82.7% | $30.00 |
| DeepSeek V4-Pro | 67.9% | $0.87 |

V4-Pro is competitive at tool orchestration and CLI tasks — a different skill set from code generation. This supports the routing strategy: coding → specialized coding models, orchestration → efficient orchestrator.

## Routing Principles

1. **Route based on task type, not model brand loyalty.** Different models excel at different things.
2. **Treat published benchmarks as directional.** Real-world experience with your actual tasks is the best calibration.
3. **Configure coding/architecture models explicitly.** Don't rely on the orchestrator model for code generation if there's a stronger option.
4. **Verify subagent results.** No benchmark score guarantees a correct implementation. Always read the output.

## Sources

- DeepSWE Leaderboard: https://deepswe.datacurve.ai/
- DeepSWE Issue #21 (cost correction, caveats): https://github.com/datacurve-ai/deep-swe/issues/21
- explainx.ai DeepSWE analysis: https://explainx.ai/blog/deepswe
- Terminal-Bench / Kilo Leaderboard: https://kilo.ai/leaderboard
