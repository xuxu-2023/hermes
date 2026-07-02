---
name: graph-advocate
description: Route plain-English onchain data questions to the right Graph Protocol service. Returns live data from 15,500+ subgraphs across 20+ chains, the Token API (EVM/Solana/TON), Polymarket/Hyperliquid trader intelligence, and a Polymarket-Limitless cross-venue prediction-market spread JOIN. Free routing; paid endpoints settle in USDC on Base via x402.
version: 2.7.0
author: Paul Barba (PaulieB14), Graph Advocate
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Blockchain, TheGraph, Subgraph, DeFi, OnchainData, x402, A2A, ERC-8004, Polymarket, Limitless, Hyperliquid, Aave, Uniswap]
    related_skills: [hyperliquid, evm]
---

# Graph Advocate Skill

Routing agent for **The Graph Protocol**. Send a plain-English question about
any blockchain (Ethereum, Base, Arbitrum, Polygon, Solana, TON, BNB, etc.) and
get back the right service plus a ready-to-run query — no manual subgraph
hunting, no MCP install, no API key.

Live service: `https://graphadvocate.com`. ERC-8004 agent #734 (Arbitrum),
#41034 (Base). ENS: `graphadvocate.eth`. Already on ClawHub, Agentverse, CDP
Bazaar, x402scan, Agentic Market, and 8004scan.

---

## When to Use

- User asks "which subgraph for protocol X on chain Y" or wants the right
  GraphQL query against a Graph subgraph
- User wants live token balances / swaps / holders / NFTs across EVM, Solana,
  or TON (routes to The Graph's Token API)
- User wants Polymarket or Hyperliquid trader intelligence (skill score, PnL,
  ghost-fill risk, vault evaluator)
- User wants cross-venue prediction-market spread (Polymarket vs Limitless,
  or Polymarket vs Kalshi) with arbitrage direction
- User wants natural-language Q&A over the x402 Base settlements warehouse
  (132M+ payments, May 2025 → Jun 2026)
- User wants to discover ERC-8004 agents on Base by capability

---

## Prerequisites

Stdlib only — no external packages, no API key needed for free-tier routing.
Optional `curl` (any modern client works). Outbound HTTPS to
`graphadvocate.com` required.

For paid endpoints, the calling agent needs an x402-compatible HTTP client
with a funded wallet on Base (USDC). Per-call settlement; no subscription.

---

## How to Run

Invoke through the `terminal` tool.

### Free routing (3 queries/sender/day, then $0.01 USDC via x402)

```bash
curl -sX POST https://graphadvocate.com/route \
  -H 'Content-Type: application/json' \
  -d '{"request":"USDC holders top 20 on Ethereum","sender":"0xYourAgentAddress"}'
```

Returns:

```json
{
  "recommendation": "token-api",
  "reason": "Token API exposes ERC-20 holder data directly...",
  "confidence": "high",
  "query_ready": { "tool": "...", "args": {...} },
  "execution_result": { "source": "...", "data": {...} },
  "cache_for_seconds": 300
}
```

### Plain-English chat (free for handshakes/intros)

```bash
curl -sX POST https://graphadvocate.com/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Find me Uniswap V3 subgraphs across all chains"}'
```

### Quota check (no charge)

```bash
curl -s "https://graphadvocate.com/quota?sender=0xYourAgentAddress"
```

---

## Paid Endpoints (x402, USDC on Base)

Every paid endpoint returns a 402 challenge with an `output_example` field in
the body so you can preview the payload shape before signing. Pricing:

| Endpoint | Price | Returns |
|---|---:|---|
| `POST /polymarket/pnl-quick` | $0.02 | Skill score + classification for a Polymarket wallet |
| `POST /polymarket/pnl` | $0.05 | Full PnL: scores + per-position records |
| `POST /polymarket/screen` | $0.05 | Top wagerers on a market with ghost-fill risk |
| `POST /polymarket/risk` | $0.02 | Wallet-type detection + ghost-fill risk classification |
| `POST /hyperliquid/score` | $0.02 | Hyperliquid perps trader skill score |
| `POST /hyperliquid/pnl` | $0.05 | Per-coin PnL + open positions + recent activity |
| `POST /hyperliquid/screen` | $0.05 | Top N traders of a coin (N capped at 10) |
| `POST /hyperliquid/vault` | $0.10 | Vault evaluator: leader skill + depositor concentration |
| `POST /hyperliquid/risk` | $0.02 | Liquidation rate + funding burn + outflow flag |
| `POST /kalshi/consensus-trend` | $0.05 | Kalshi consensus slope + acceleration |
| `POST /kalshi-polymarket/spread` | $0.05 | Kalshi-Polymarket cross-source spread |
| `POST /kalshi/sports-live-edge` | $0.05 | Live sports mispricing detector |
| `POST /predmarket/spread` | $0.05 | **Polymarket-Limitless cross-venue spread** with arbitrage direction |
| `POST /ask` | $0.05 | Natural-language Q&A over 132M+ x402 Base settlements |
| `POST /onchain-x402/address` | $0.01 | Decentralized x402 address lookup via subgraph |

---

## Spend Controls (Required Before Autonomous Use)

1. **Dedicated low-balance wallet.** Fund only what you're willing to spend.
2. **Per-call approval.** Configure your x402 client to surface 402 challenges
   before signing. Every paid call's 402 body now includes `output_example`
   so you can decide based on the actual payload shape.
3. **Hard caps.** Set `maxAmountPerCall` and `maxTotalSpend` in your runtime,
   or wrap calls in a counter that breaks after N invocations.

---

## Quick Reference

```bash
# Free
POST /route        # plain-English -> routed query + live data
POST /chat         # web-chat-style interaction
GET  /quota        # remaining free-tier today

# Discovery (no-charge)
GET /.well-known/agent-card.json   # A2A agent card
GET /agents/capabilities.json      # machine-readable capability list
GET /llms.txt                      # LLM-friendly discovery file
GET /SKILL.md                      # this skill manifest (live source)

# Paid (x402 on Base, USDC) — see table above
```

---

## Identity & Provenance

- ERC-8004: Agent #734 (Arbitrum), #41034 (Base)
- ENS: `graphadvocate.eth`
- Source: https://github.com/PaulieB14/graph-advocate
- Docs: https://docs.graphadvocate.com
- ClawHub: https://clawhub.ai/paulieb14/graph-advocate

---

## Security & Privacy

- **Instruction-only skill** — no code is downloaded or executed locally.
- **No credentials required** for free-tier routing.
- **No local file access** — reads nothing from your filesystem.
- **Stateless** — no session data persists between requests.
- Plain-English queries are sent to `graphadvocate.com` over HTTPS. Do not
  send private keys, seed phrases, or sensitive internal strategy details.

Paid mode is **opt-in** — paid endpoints only settle when your runtime is
configured to accept x402 payment challenges. Without that configuration,
paid endpoints return `402 Payment Required` and the call stops there.

The live SKILL.md is also served at `https://graphadvocate.com/SKILL.md` —
this file mirrors that source.
