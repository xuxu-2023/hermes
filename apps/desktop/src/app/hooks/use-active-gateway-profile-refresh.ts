import { useEffect, useRef } from 'react'

interface UseActiveGatewayProfileRefreshOptions {
  activeGatewayProfile: string
  refreshActiveProfile: () => Promise<void>
  refreshCurrentModel: (force?: boolean) => Promise<void>
  refreshSessions: () => Promise<void>
}

/**
 * Keep profile-scoped desktop state in sync when the live gateway profile changes.
 *
 * The sidebar session list is filtered by the active gateway profile. Without
 * refreshing it here, startup/profile-swap flows can leave the desktop showing
 * the previous profile's empty session slice until some later user action
 * happens to trigger a refresh.
 */
export function useActiveGatewayProfileRefresh({
  activeGatewayProfile,
  refreshActiveProfile,
  refreshCurrentModel,
  refreshSessions
}: UseActiveGatewayProfileRefreshOptions) {
  const lastGatewayProfileRef = useRef(activeGatewayProfile)

  useEffect(() => {
    if (activeGatewayProfile === lastGatewayProfileRef.current) {
      return
    }

    lastGatewayProfileRef.current = activeGatewayProfile
    void refreshCurrentModel(true)
    void refreshActiveProfile()
    void refreshSessions().catch(() => undefined)
  }, [activeGatewayProfile, refreshActiveProfile, refreshCurrentModel, refreshSessions])
}
