import { Text } from '@hermes/ink'
import type { ReactNode } from 'react'

import type { UsageModelData } from '../gatewayTypes.js'
import type { Theme } from '../theme.js'

/** A numbered menu row with the ▸ cursor (mirrors ClarifyPrompt). */
export function MenuRow({ active, index, label, t }: { active: boolean; index: number; label: string; t: Theme }) {
  return (
    <Text>
      <Text bold={active} color={active ? t.color.label : t.color.muted} inverse={active}>
        {active ? '▸ ' : '  '}
        {index}. {label}
      </Text>
    </Text>
  )
}

/** Plain (non-numbered) action row with the ▸ cursor (confirm screens). */
export function ActionRow({ active, label, color, t }: { active: boolean; label: string; color?: string; t: Theme }) {
  return (
    <Text>
      <Text color={active ? t.color.accent : t.color.muted}>{active ? '▸ ' : '  '}</Text>
      <Text bold={active} color={active ? (color ?? t.color.text) : t.color.muted}>
        {label}
      </Text>
    </Text>
  )
}

export const BAR_CELLS = 10

/** ratio in [0,1] -> { bar: '█…░…', pct: 0-100 } using `cells` cells. */
export function barCells(ratio: number, cells: number = BAR_CELLS): { bar: string; pct: number } {
  const r = Math.max(0, Math.min(1, ratio))

  const filled = Math.round(r * cells)

  return { bar: '█'.repeat(filled) + '░'.repeat(cells - filled), pct: Math.round(r * 100) }
}

/**
 * Two-bar dollar usage view (decided with the user over a crammed three-segment
 * bar: at terminal widths a single fill glyph per full-resolution bar is the
 * only legible option). The plan bar is labeled with the plan name and shows
 * the allowance detail + % used; the top-up bar shows purchased dollars (no
 * denominator, renders full = balance, rolls over). Dollars only — never
 * "credits". Each row:
 *   `Plus    [██████░░░░]  $14.00 of $20.00 · 30% used`
 * Renders nothing for a free account (no bars to draw — caller shows upsell).
 */
export function UsageBars({ model, t }: { model: undefined | UsageModelData; t: Theme }) {
  if (!model || !model.available) {
    return null
  }

  const rows: ReactNode[] = []
  // Label the plan bar with the plan name (padded for column alignment with the
  // top-up row). Falls back to 'plan' when the name is absent.
  const planLabel = (model.plan_name || 'plan').padEnd(8).slice(0, 8)

  if (model.plan_bar) {
    const b = model.plan_bar
    const { bar } = barCells(b.fill_fraction)
    const pct = b.pct_used == null ? '' : ` · ${b.pct_used}% used`

    rows.push(
      <Text color={t.color.text} key="plan">
        {planLabel}
        <Text color={t.color.muted}>[</Text>
        <Text color={t.color.accent}>{bar}</Text>
        <Text color={t.color.muted}>]</Text>
        {`  ${b.remaining_display} left of ${b.total_display}${pct}`}
      </Text>
    )
  }

  if (model.topup_bar) {
    const b = model.topup_bar
    const { bar } = barCells(1)

    rows.push(
      <Text color={t.color.text} key="topup">
        {'top-up  '}
        <Text color={t.color.muted}>[</Text>
        <Text color={t.color.ok}>{bar}</Text>
        <Text color={t.color.muted}>]</Text>
        {`  ${b.remaining_display} · never expires`}
      </Text>
    )
  }

  if (rows.length === 0) {
    return null
  }

  return <>{rows}</>
}

/**
 * Plain-text version of the two-bar usage view, for text-only surfaces (the
 * /usage transcript panel). Returns one string per line: a plan bar, a top-up
 * bar, and a total-spendable summary, whichever apply. Dollars only.
 */
export function usageBarsText(model: undefined | UsageModelData): string[] {
  if (!model || !model.available) {
    return []
  }

  const lines: string[] = []
  const planLabel = (model.plan_name || 'plan').padEnd(8).slice(0, 8)

  if (model.plan_bar) {
    const b = model.plan_bar
    const { bar } = barCells(b.fill_fraction)
    const pct = b.pct_used == null ? '' : ` · ${b.pct_used}% used`

    lines.push(`${planLabel}[${bar}]  ${b.remaining_display} left of ${b.total_display}${pct}`)
  }

  if (model.topup_bar) {
    const b = model.topup_bar
    const { bar } = barCells(1)

    lines.push(`top-up  [${bar}]  ${b.remaining_display} · never expires`)
  }

  if (model.total_spendable_display && model.has_topup) {
    lines.push(`Total spendable: ${model.total_spendable_display}`)
  }

  return lines
}

export const footer = (extra: string, t: Theme) => <Text color={t.color.muted}>{extra}</Text>
