import { act, cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setVoicePlaybackState } from '@/store/voice-playback'

import { useVoiceConversation, type ConversationStatus } from './use-voice-conversation'

const recorder = vi.hoisted(() => ({
  cancel: vi.fn(),
  start: vi.fn(),
  stop: vi.fn()
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      notifications: {
        voice: {
          configureSpeechToText: 'Configure speech-to-text',
          couldNotStartSession: 'Could not start voice session',
          microphoneFailed: 'Microphone failed',
          playbackFailed: 'Playback failed',
          transcriptionFailed: 'Transcription failed',
          unavailable: 'Voice unavailable'
        }
      }
    }
  })
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

vi.mock('./use-mic-recorder', () => ({
  useMicRecorder: () => ({
    handle: recorder,
    level: 0
  })
}))

function playback(status: 'idle' | 'preparing' | 'speaking') {
  setVoicePlaybackState({
    audioElement: null,
    messageId: null,
    sequence: 0,
    source: status === 'idle' ? null : 'voice-conversation',
    status
  })
}

function Harness({
  busy = false,
  enabled,
  onStatus,
  onSubmit = () => undefined,
  onTranscribeAudio = async () => 'hello'
}: {
  busy?: boolean
  enabled: boolean
  onStatus?: (status: ConversationStatus) => void
  onSubmit?: (text: string) => boolean | Promise<boolean | void> | void
  onTranscribeAudio?: (audio: Blob) => Promise<string>
}) {
  const conversation = useVoiceConversation({
    busy,
    consumePendingResponse: vi.fn(),
    enabled,
    onSubmit,
    onTranscribeAudio,
    pendingResponse: () => null
  })

  onStatus?.(conversation.status)

  return <div data-status={conversation.status} />
}

describe('useVoiceConversation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    playback('idle')
    recorder.start.mockResolvedValue(undefined)
    recorder.stop.mockResolvedValue({
      audio: new Blob(['voice'], { type: 'audio/webm' }),
      durationMs: 1200,
      heardSpeech: true
    })
  })

  afterEach(() => {
    cleanup()
    playback('idle')
  })

  it('waits for active playback before starting the microphone', async () => {
    playback('speaking')

    const { rerender } = render(<Harness enabled={false} />)

    rerender(<Harness enabled />)

    await new Promise(resolve => window.setTimeout(resolve, 0))
    expect(recorder.start).not.toHaveBeenCalled()

    act(() => playback('idle'))

    await waitFor(() => expect(recorder.start).toHaveBeenCalledTimes(1))
  })

  it('does not enter the thinking loop when submit rejects the transcript', async () => {
    let onSilence: (() => void) | undefined
    recorder.start.mockImplementation(async options => {
      onSilence = options?.onSilence
    })
    const onSubmit = vi.fn(() => false)
    const statuses: ConversationStatus[] = []

    const { rerender } = render(<Harness enabled={false} onStatus={status => statuses.push(status)} />)

    rerender(<Harness enabled onStatus={status => statuses.push(status)} onSubmit={onSubmit} />)

    await waitFor(() => expect(recorder.start).toHaveBeenCalledTimes(1))

    await act(async () => {
      onSilence?.()
      await Promise.resolve()
    })

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('hello'))
    expect(statuses).not.toContain('thinking')
  })
})
