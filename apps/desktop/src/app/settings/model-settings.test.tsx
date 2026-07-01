import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Radix Select calls scrollIntoView on its items when the content opens; jsdom
// doesn't implement it (nor hasPointerCapture / releasePointerCapture), so stub
// them to let the dropdown open in tests.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getGlobalModelInfo = vi.fn()
const getGlobalModelOptions = vi.fn()
const getAuxiliaryModels = vi.fn()
const getMoaModels = vi.fn()
const saveMoaModels = vi.fn()
const setModelAssignment = vi.fn()
const getRecommendedDefaultModel = vi.fn()
const setEnvVar = vi.fn()
const getHermesConfigRecord = vi.fn()
const saveHermesConfig = vi.fn()
const startManualProviderOAuth = vi.fn()
const startManualLocalEndpoint = vi.fn()

vi.mock('@/hermes', () => ({
  getGlobalModelInfo: () => getGlobalModelInfo(),
  getGlobalModelOptions: () => getGlobalModelOptions(),
  getAuxiliaryModels: () => getAuxiliaryModels(),
  getMoaModels: () => getMoaModels(),
  saveMoaModels: (body: unknown) => saveMoaModels(body),
  setModelAssignment: (body: unknown) => setModelAssignment(body),
  getRecommendedDefaultModel: (slug: string) => getRecommendedDefaultModel(slug),
  setEnvVar: (key: string, value: string) => setEnvVar(key, value),
  getHermesConfigRecord: () => getHermesConfigRecord(),
  saveHermesConfig: (config: unknown) => saveHermesConfig(config)
}))

vi.mock('@/store/onboarding', () => ({
  startManualLocalEndpoint: () => startManualLocalEndpoint(),
  startManualProviderOAuth: (slug: string) => startManualProviderOAuth(slug)
}))

beforeEach(() => {
  getGlobalModelInfo.mockResolvedValue({ provider: 'nous', model: 'hermes-4' })
  getGlobalModelOptions.mockResolvedValue({
    providers: [
      {
        name: 'Nous',
        slug: 'nous',
        models: ['hermes-4', 'hermes-4-mini'],
        authenticated: true,
        capabilities: { 'hermes-4': { reasoning: true, fast: true } }
      },
      // An unconfigured api_key provider — surfaced by the full-universe payload.
      {
        name: 'DeepSeek',
        slug: 'deepseek',
        models: [],
        authenticated: false,
        auth_type: 'api_key',
        key_env: 'DEEPSEEK_API_KEY'
      }
    ]
  })
  getAuxiliaryModels.mockResolvedValue({
    main: { provider: 'nous', model: 'hermes-4' },
    tasks: [{ task: 'vision', provider: 'auto', model: '', base_url: '' }]
  })
  getMoaModels.mockResolvedValue(null)
  saveMoaModels.mockResolvedValue(null)
  setModelAssignment.mockResolvedValue({ provider: 'nous', model: 'hermes-4', gateway_tools: [] })
  getRecommendedDefaultModel.mockResolvedValue({ provider: 'deepseek', model: 'deepseek-chat', free_tier: null })
  setEnvVar.mockResolvedValue({ ok: true })
  getHermesConfigRecord.mockResolvedValue({ agent: { reasoning_effort: 'medium', service_tier: 'normal' } })
  saveHermesConfig.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderModelSettings() {
  const { ModelSettings } = await import('./model-settings')

  return render(<ModelSettings />)
}

describe('ModelSettings', () => {
  it('resolves the bare custom provider to the saved endpoint row that matches model.base_url', async () => {
    const { resolveModelSettingsProvider } = await import('./model-settings')

    expect(
      resolveModelSettingsProvider(
        [
          {
            name: 'Custom endpoint',
            slug: 'custom',
            models: [],
            authenticated: false,
            auth_type: 'api_key'
          },
          {
            name: '100.79.23.62:8317',
            slug: 'custom:100.79.23.62:8317',
            api_url: 'http://100.79.23.62:8317/v1/',
            models: ['gpt-5.4'],
            authenticated: true,
            is_user_defined: true
          }
        ],
        'custom',
        'http://100.79.23.62:8317/v1'
      )
    ).toBe('custom:100.79.23.62:8317')
  })

  it('keeps the bare custom provider when no saved endpoint matches model.base_url', async () => {
    const { resolveModelSettingsProvider } = await import('./model-settings')

    expect(
      resolveModelSettingsProvider(
        [
          {
            name: 'Other endpoint',
            slug: 'custom:other',
            api_url: 'http://127.0.0.1:8000/v1',
            models: ['llama'],
            is_user_defined: true
          }
        ],
        'custom',
        'http://100.79.23.62:8317/v1'
      )
    ).toBe('custom')
  })

  it('loads the current main model and lists the full provider universe', async () => {
    await renderModelSettings()

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    // Open the provider Select — every provider from the full payload should be
    // listed, including the unconfigured one with its "set up" hint.
    const triggers = await screen.findAllByRole('combobox')
    fireEvent.click(triggers[0])

    // "Nous" shows in both the trigger and the open list; the unconfigured
    // provider row is the unique signal of the full universe.
    expect((await screen.findAllByText('Nous')).length).toBeGreaterThan(0)
    expect(await screen.findByText(/DeepSeek/)).toBeTruthy()
  })

  it('shows the saved custom provider instead of the unconfigured custom setup row', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'custom', model: 'gpt-5.4' })
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Custom endpoint',
          slug: 'custom',
          models: [],
          authenticated: false,
          auth_type: 'api_key'
        },
        {
          name: '100.79.23.62:8317',
          slug: 'custom:100.79.23.62:8317',
          api_url: 'http://100.79.23.62:8317/v1',
          models: ['gpt-5.4', 'gpt-5.5'],
          authenticated: true,
          is_user_defined: true,
          capabilities: { 'gpt-5.4': { reasoning: true, fast: false } }
        }
      ]
    })
    getHermesConfigRecord.mockResolvedValueOnce({
      model: { provider: 'custom', default: 'gpt-5.4', base_url: 'http://100.79.23.62:8317/v1' },
      agent: { reasoning_effort: 'medium', service_tier: 'normal' }
    })

    await renderModelSettings()

    expect(await screen.findByText('100.79.23.62:8317')).toBeTruthy()
    expect(screen.getByText('gpt-5.4')).toBeTruthy()
    expect(screen.queryByText(/needs an API key/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Set up Custom endpoint/ })).toBeNull()
  })

  it('activates an unconfigured api_key provider inline by saving its key', async () => {
    await renderModelSettings()

    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    // Open the provider Select and pick the unconfigured provider.
    const triggers = screen.getAllByRole('combobox')
    fireEvent.click(triggers[0])
    const deepseekOption = await screen.findByText(/DeepSeek/)
    fireEvent.click(deepseekOption)

    // The inline key input appears for an api_key provider that needs setup.
    const keyInput = await screen.findByPlaceholderText(/Paste DEEPSEEK_API_KEY/)
    fireEvent.change(keyInput, { target: { value: 'sk-test-123' } })

    const activate = await screen.findByRole('button', { name: /Activate/ })
    fireEvent.click(activate)

    await waitFor(() => expect(setEnvVar).toHaveBeenCalledWith('DEEPSEEK_API_KEY', 'sk-test-123'))
  })

  it('writes the profile default speed (service_tier) when the fast switch is toggled', async () => {
    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    const fastSwitch = await screen.findByRole('switch')
    fireEvent.click(fastSwitch)

    await waitFor(() =>
      expect(saveHermesConfig).toHaveBeenCalledWith(
        expect.objectContaining({ agent: expect.objectContaining({ service_tier: 'fast' }) })
      )
    )
  })

  it('hides the reasoning/speed defaults when the main model reports no capabilities', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4'],
          authenticated: true,
          capabilities: { 'hermes-4': { reasoning: false, fast: false } }
        }
      ]
    })

    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('renders the auxiliary task rows', async () => {
    await renderModelSettings()

    expect(await screen.findByText('Vision')).toBeTruthy()
    expect(screen.getAllByText('auto · use main model').length).toBeGreaterThan(0)
  })

  it('assigns an auxiliary task to the main model via setModelAssignment', async () => {
    await renderModelSettings()

    // One "Set to main" button per task slot; the first is Vision.
    const setToMainButtons = await screen.findAllByRole('button', { name: 'Set to main' })
    fireEvent.click(setToMainButtons[0])

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'hermes-4',
        provider: 'nous',
        scope: 'auxiliary',
        task: 'vision'
      })
    )
  })

  it('warns when a main switch leaves auxiliary tasks pinned to another provider', async () => {
    setModelAssignment.mockResolvedValueOnce({
      provider: 'openrouter',
      model: 'anthropic/claude-opus-4.7',
      gateway_tools: [],
      stale_aux: [{ task: 'compression', provider: 'nous', model: 'hermes-4' }]
    })

    await renderModelSettings()
    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())

    const applyButton = await screen.findByRole('button', { name: 'Apply' })
    fireEvent.click(applyButton)

    // The switch-time notice names the pinned provider and offers a reset.
    expect(await screen.findByText(/still run on/)).toBeTruthy()
    expect(screen.getByText('nous')).toBeTruthy()
  })

  it('shows a persistent banner when a loaded aux slot mismatches the main provider', async () => {
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'nous', model: 'hermes-4' },
      tasks: [{ task: 'curator', provider: 'openrouter', model: 'anthropic/claude-opus-4.7', base_url: '' }]
    })

    await renderModelSettings()

    // Banner present on load, no switch required.
    expect(await screen.findByText(/still run on/)).toBeTruthy()
  })
})
