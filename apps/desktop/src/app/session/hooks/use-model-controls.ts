import { type QueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'

import { getGlobalModelInfo } from '@/hermes'
import { useI18n } from '@/i18n'
import { persistString, storedString } from '@/lib/storage'
import { notifyError } from '@/store/notifications'
import { $activeSessionId, $currentModel, $currentProvider, setCurrentModel, setCurrentProvider } from '@/store/session'
import type { ModelOptionsResponse } from '@/types/hermes'

interface ModelSelection {
  model: string
  provider: string
}

const COMPOSER_DEFAULT_BASELINE_KEY = 'hermes.desktop.composer.default-baseline'

const LEGACY_DESKTOP_DEFAULTS: readonly ModelSelection[] = [
  { model: 'gpt-5.5', provider: 'openai-codex' },
  { model: 'openai/gpt-5.5', provider: 'openai-codex' }
]

function sameSelection(left: ModelSelection, right: ModelSelection): boolean {
  return left.model === right.model && left.provider === right.provider
}

function normalizedSelection(selection: ModelSelection): ModelSelection {
  return {
    model: selection.model.trim(),
    provider: selection.provider.trim()
  }
}

function readComposerDefaultBaseline(): ModelSelection | null {
  const raw = storedString(COMPOSER_DEFAULT_BASELINE_KEY)

  if (!raw) {
    return null
  }

  try {
    const parsed = JSON.parse(raw) as Partial<ModelSelection>
    const model = typeof parsed.model === 'string' ? parsed.model.trim() : ''
    const provider = typeof parsed.provider === 'string' ? parsed.provider.trim() : ''

    return model ? { model, provider } : null
  } catch {
    return null
  }
}

function writeComposerDefaultBaseline(selection: ModelSelection): void {
  persistString(COMPOSER_DEFAULT_BASELINE_KEY, JSON.stringify(normalizedSelection(selection)))
}

function isLegacyDesktopDefault(selection: ModelSelection): boolean {
  return LEGACY_DESKTOP_DEFAULTS.some(defaultSelection => sameSelection(selection, defaultSelection))
}

interface ModelControlsOptions {
  activeSessionId: string | null
  queryClient: QueryClient
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export function useModelControls({ activeSessionId, queryClient, requestGateway }: ModelControlsOptions) {
  const { t } = useI18n()
  const copy = t.desktop

  const updateModelOptionsCache = useCallback(
    (provider: string, model: string, includeGlobal: boolean) => {
      const patch = (prev: ModelOptionsResponse | undefined) => ({ ...(prev ?? {}), provider, model })

      queryClient.setQueryData<ModelOptionsResponse>(['model-options', activeSessionId || 'global'], patch)

      if (includeGlobal) {
        queryClient.setQueryData<ModelOptionsResponse>(['model-options', 'global'], patch)
      }
    },
    [activeSessionId, queryClient]
  )

  // Seed the composer's model state from the profile default. `force` reseeds
  // for a profile swap (the new profile has its own default). Routine refreshes
  // may also reseed when the composer still matches the last default we seeded
  // and the profile default has changed; once a user picks a different model,
  // that plain UI state survives boot / fresh draft / session-event refreshes.
  // A live session owns the footer, so skip entirely.
  const refreshCurrentModel = useCallback(async (force = false) => {
    try {
      if ($activeSessionId.get()) {
        return
      }

      const result = await getGlobalModelInfo()
      const nextDefault = normalizedSelection({
        model: typeof result.model === 'string' ? result.model : '',
        provider: typeof result.provider === 'string' ? result.provider : ''
      })

      if ($activeSessionId.get() || !nextDefault.model) {
        return
      }

      const current = normalizedSelection({
        model: $currentModel.get(),
        provider: $currentProvider.get()
      })
      const baseline = readComposerDefaultBaseline()
      const shouldReseed =
        force ||
        !current.model ||
        sameSelection(current, nextDefault) ||
        (baseline != null && sameSelection(current, baseline) && !sameSelection(baseline, nextDefault)) ||
        (baseline == null && isLegacyDesktopDefault(current) && !sameSelection(current, nextDefault))

      if (!shouldReseed) {
        return
      }

      setCurrentModel(nextDefault.model)
      setCurrentProvider(nextDefault.provider)
      writeComposerDefaultBaseline(nextDefault)
    } catch {
      // The delayed session.info event still updates this once the agent is ready.
    }
  }, [])

  // Returns whether the switch succeeded so callers can await it before applying
  // follow-up changes. The composer model is plain UI state: with no live
  // session it's just stored (and shipped on the next session.create); with one
  // it's scoped to that session via config.set. It NEVER writes the profile
  // default — that lives in Settings → Model — so picking a model here can't
  // silently mutate global config.
  const selectModel = useCallback(
    async (selection: ModelSelection): Promise<boolean> => {
      // Snapshot for rollback: the switch is applied optimistically, so a
      // failure must restore the prior model/provider (store + query cache)
      // rather than leave the UI showing a model the backend never selected.
      const prevModel = $currentModel.get()
      const prevProvider = $currentProvider.get()

      setCurrentModel(selection.model)
      setCurrentProvider(selection.provider)
      updateModelOptionsCache(selection.provider, selection.model, !activeSessionId)

      // No live session yet: the pick is pure UI state. session.create reads
      // $currentModel/$currentProvider and applies it as that session's override.
      if (!activeSessionId) {
        return true
      }

      try {
        await requestGateway('config.set', {
          session_id: activeSessionId,
          key: 'model',
          value: `${selection.model} --provider ${selection.provider}`
        })

        void queryClient.invalidateQueries({ queryKey: ['model-options', activeSessionId] })

        return true
      } catch (err) {
        setCurrentModel(prevModel)
        setCurrentProvider(prevProvider)
        updateModelOptionsCache(prevProvider, prevModel, !activeSessionId)
        notifyError(err, copy.modelSwitchFailed)

        return false
      }
    },
    [activeSessionId, copy.modelSwitchFailed, queryClient, requestGateway, updateModelOptionsCache]
  )

  return { refreshCurrentModel, selectModel, updateModelOptionsCache }
}
