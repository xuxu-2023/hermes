import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useActiveGatewayProfileRefresh } from './use-active-gateway-profile-refresh'

describe('useActiveGatewayProfileRefresh', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('does nothing on the initial render', () => {
    const refreshCurrentModel = vi.fn(async () => undefined)
    const refreshActiveProfile = vi.fn(async () => undefined)
    const refreshSessions = vi.fn(async () => undefined)

    renderHook(({ profile }) =>
      useActiveGatewayProfileRefresh({
        activeGatewayProfile: profile,
        refreshActiveProfile,
        refreshCurrentModel,
        refreshSessions
      }),
      { initialProps: { profile: 'default' } }
    )

    expect(refreshCurrentModel).not.toHaveBeenCalled()
    expect(refreshActiveProfile).not.toHaveBeenCalled()
    expect(refreshSessions).not.toHaveBeenCalled()
  })

  it('refreshes model, active profile, and sessions when the gateway profile changes', async () => {
    const refreshCurrentModel = vi.fn(async () => undefined)
    const refreshActiveProfile = vi.fn(async () => undefined)
    const refreshSessions = vi.fn(async () => undefined)

    const { rerender } = renderHook(
      ({ profile }) =>
        useActiveGatewayProfileRefresh({
          activeGatewayProfile: profile,
          refreshActiveProfile,
          refreshCurrentModel,
          refreshSessions
        }),
      { initialProps: { profile: 'default' } }
    )

    rerender({ profile: 'orchestrator' })

    await waitFor(() => {
      expect(refreshCurrentModel).toHaveBeenCalledWith(true)
      expect(refreshActiveProfile).toHaveBeenCalledTimes(1)
      expect(refreshSessions).toHaveBeenCalledTimes(1)
    })
  })

  it('does not re-refresh when rerendered with the same profile', async () => {
    const refreshCurrentModel = vi.fn(async () => undefined)
    const refreshActiveProfile = vi.fn(async () => undefined)
    const refreshSessions = vi.fn(async () => undefined)

    const { rerender } = renderHook(
      ({ profile }) =>
        useActiveGatewayProfileRefresh({
          activeGatewayProfile: profile,
          refreshActiveProfile,
          refreshCurrentModel,
          refreshSessions
        }),
      { initialProps: { profile: 'default' } }
    )

    rerender({ profile: 'default' })
    rerender({ profile: 'default' })

    await waitFor(() => {
      expect(refreshCurrentModel).not.toHaveBeenCalled()
      expect(refreshActiveProfile).not.toHaveBeenCalled()
      expect(refreshSessions).not.toHaveBeenCalled()
    })
  })
})
