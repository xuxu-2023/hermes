---
name: importance-search
description: Importance-aware multi-source search. Ranks news by cross-outlet coverage, X/social by influential accounts, and YouTube by view velocity — instead of raw search order. Use when you need "what actually matters now" rather than a flat result list. Configurable per domain (AI, finance, general, ...). Runs on free Codex OAuth web search; no paid search API required.
---

# Importance Search

Most search returns results in *search order*. This skill ranks them by **importance signals**, so the agent surfaces what actually matters:

- **News** — ranked by cross-outlet coverage (how many major outlets carry the story) + recency. A story 5 outlets report is more important than one blog post.
- **X / social** — posts from *configured influential accounts* (e.g. for AI: @AnthropicAI, @OpenAI, @GoogleDeepMind) + recency, not random matches.
- **YouTube** — ranked by **view velocity** (views ÷ days since upload) from `yt-dlp` flat-search metadata — real popularity, not relevance order.

It then synthesizes a 2-line "why this matters today" insight over the top items.

## How it works

1. Codex OAuth (`auxiliary_client`) does web_search to discover important news + influential-account posts — free with a ChatGPT subscription, no per-call search API.
2. `yt-dlp` flat search returns YouTube `view_count` without triggering the bot-gate (full extraction is blocked on datacenter IPs; flat search is not).
3. A scoring pass ranks each source by its importance signal and returns the top N.
4. Optional deep fetch: if a deep-fetch engine is available (set `IMPORTANCE_FETCH_DIR` to an `insane-search`-style engine), the top news body is pulled past paywalls/WAFs to enrich the synthesis. Falls back to a plain HTTP fetch otherwise.

## Usage

```bash
python importance_search.py <domain>      # ai-tech | finance | general | <your own>
```

Outputs a ranked briefing: top news (coverage), X influencers, YouTube (views), and a concise insight.

## Configuration

Edit `search_domains.json` to add domains — each with `keywords`, `x_influencers`, and `youtube_queries`. Ships with `ai-tech`, `finance`, `general` so the same engine serves any field, not just tech.

## Requirements

- Codex CLI authenticated (ChatGPT subscription) — used via Hermes `agent.auxiliary_client`.
- `pip install yt-dlp`
- Optional: a deep-fetch engine for full-body article fetch (`IMPORTANCE_FETCH_DIR`).
