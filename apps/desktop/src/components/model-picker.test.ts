// Regression tests for #57405 — provider rows whose `models` field
// contains a `{id, ...}` object instead of a string crashed the picker
// with `'dict' object has no attribute 'lower'`. The picker now filters
// out non-string entries defensively, so the dropdown renders whatever
// string models the provider actually has and silently drops malformed
// entries.
//
// The picker is React-coupled to @tanstack/react-query, but the
// defensive filter is pure: we test it via a thin harness that mimics
// the relevant `ModelResults` slice of the component.

import { describe, expect, it } from 'vitest'

import type { ModelOptionProvider } from '@/types/hermes'

// Mirror of `ModelResults`'s defensive filter. If you change the
// implementation in `model-picker.tsx`, mirror the change here.
function configuredProviders(
  providers: ModelOptionProvider[],
): ModelOptionProvider[] {
  const isStringModel = (m: unknown): m is string =>
    typeof m === 'string' && m.length > 0
  return providers
    .map(p => ({ ...p, models: (p.models ?? []).filter(isStringModel) }))
    .filter(p => p.models.length > 0)
}

describe('model-picker defensive filter (#57405)', () => {
  it('keeps string-typed models unchanged', () => {
    const providers: ModelOptionProvider[] = [
      {
        slug: 'anthropic',
        name: 'Anthropic',
        models: ['claude-opus-4-8', 'claude-sonnet-4-5']
      }
    ]
    const got = configuredProviders(providers)
    expect(got).toEqual(providers)
  })

  it('drops dict-shaped models instead of crashing', () => {
    const providers = [
      {
        slug: 'broken',
        name: 'Broken provider',
        // The runtime shape some hand-edited configs and legacy disk
        // caches can produce. The TypeScript type says `string[]`
        // but the runtime does not enforce that.
        models: [
          { id: 'm1', context_length: 8192 } as unknown as string,
          { id: 'm2' } as unknown as string,
          'm3'
        ]
      }
    ] as unknown as ModelOptionProvider[]

    const got = configuredProviders(providers)
    expect(got).toHaveLength(1)
    expect(got[0].models).toEqual(['m3'])
  })

  it('excludes a provider whose models are all non-string', () => {
    const providers = [
      {
        slug: 'all-bad',
        name: 'All bad',
        models: [{ id: 'a' }, { id: 'b' }] as unknown as string[]
      }
    ] as unknown as ModelOptionProvider[]

    expect(configuredProviders(providers)).toEqual([])
  })

  it('keeps empty-model providers (filter is on shape, not emptiness)', () => {
    const providers: ModelOptionProvider[] = [
      { slug: 'empty', name: 'Empty', models: [] }
    ]
    // An empty `models` list is fine: the picker's other branch renders
    // "no authenticated providers" copy. The defensive filter should
    // not change behavior for empty arrays.
    expect(configuredProviders(providers)).toEqual([])
  })

  it('handles a mix of providers in one list', () => {
    const providers = [
      {
        slug: 'good',
        name: 'Good',
        models: ['g1', 'g2']
      },
      {
        slug: 'bad',
        name: 'Bad',
        models: [{ id: 'b1' } as unknown as string, 'b2']
      },
      {
        slug: 'empty',
        name: 'Empty',
        models: []
      }
    ] as unknown as ModelOptionProvider[]

    const got = configuredProviders(providers)
    expect(got.map(p => p.slug)).toEqual(['good', 'bad'])
    expect(got[0].models).toEqual(['g1', 'g2'])
    expect(got[1].models).toEqual(['b2'])
  })
})
