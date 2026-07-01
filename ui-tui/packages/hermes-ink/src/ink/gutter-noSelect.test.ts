import { EventEmitter } from 'events'

import React from 'react'
import { describe, expect, it } from 'vitest'

import Box from './components/Box.js'
import { NoSelect } from './components/NoSelect.js'
import Text from './components/Text.js'
import Ink from './ink.js'
import { cellAt, type Screen } from './screen.js'

class FakeTty extends EventEmitter {
  chunks: string[] = []
  columns = 40
  rows = 10
  isTTY = true

  write(chunk: string | Uint8Array, cb?: (err?: Error | null) => void): boolean {
    this.chunks.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8'))
    cb?.()

    return true
  }
}

function makeInk(cols = 40, rows = 10) {
  const stdout = new FakeTty()
  stdout.columns = cols
  stdout.rows = rows
  const stdin = new FakeTty()
  const stderr = new FakeTty()

  const ink = new Ink({
    exitOnCtrlC: false,
    patchConsole: false,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  return { ink, stdout, stdin, stderr }
}

type InkPrivate = { frontFrame: { screen: Screen } }
const screenOf = (ink: Ink): Screen => (ink as unknown as InkPrivate).frontFrame.screen

function gutterNoSelectOnRow(screen: Screen, row: number, gutterCols: number): boolean {
  for (let col = 0; col < gutterCols; col++) {
    if (screen.noSelect[row * screen.width + col] !== 1) {
      return false
    }
  }

  return true
}

function MessageRow({ alignItems, lines }: { alignItems?: 'flex-start' | 'stretch'; lines: string[] }) {
  const gutterWidth = 3
  const rowProps = alignItems ? { alignItems, flexDirection: 'row' as const } : {}

  return React.createElement(
    Box,
    { flexDirection: 'column' },
    React.createElement(
      Box,
      rowProps,
      React.createElement(
        NoSelect,
        { flexShrink: 0, fromLeftEdge: true, width: gutterWidth },
        React.createElement(Text, null, '> ')
      ),
      React.createElement(
        Box,
        { width: 30 },
        React.createElement(
          Box,
          { flexDirection: 'column' },
          ...lines.map((line, i) => React.createElement(Text, { key: i }, line))
        )
      )
    )
  )
}

describe('gutter noSelect stretch', () => {
  it('lays out gutter beside content on the same row with default Box props', () => {
    const { ink } = makeInk()
    const gutterWidth = 3

    ink.render(
      React.createElement(MessageRow, {
        lines: ['Hello']
      })
    )
    ink.onRender()

    const screen = screenOf(ink)
    expect(cellAt(screen, 0, 0)?.char).toBe('>')
    expect(cellAt(screen, gutterWidth, 0)?.char).toBe('H')

    ink.unmount()
  })

  it('marks from-left-edge gutter on every content row with default row layout', () => {
    const { ink } = makeInk()
    const gutterWidth = 3

    ink.render(
      React.createElement(MessageRow, {
        lines: ['Line one', 'Line two', 'Line three']
      })
    )
    ink.onRender()

    const screen = screenOf(ink)

    expect(gutterNoSelectOnRow(screen, 0, gutterWidth)).toBe(true)
    expect(gutterNoSelectOnRow(screen, 1, gutterWidth)).toBe(true)
    expect(gutterNoSelectOnRow(screen, 2, gutterWidth)).toBe(true)

    ink.unmount()
  })

  it('marks from-left-edge gutter on every content row with explicit stretch', () => {
    const { ink } = makeInk()
    const gutterWidth = 3

    ink.render(
      React.createElement(MessageRow, {
        alignItems: 'stretch',
        lines: ['Line one', 'Line two', 'Line three']
      })
    )
    ink.onRender()

    const screen = screenOf(ink)

    expect(gutterNoSelectOnRow(screen, 0, gutterWidth)).toBe(true)
    expect(gutterNoSelectOnRow(screen, 1, gutterWidth)).toBe(true)
    expect(gutterNoSelectOnRow(screen, 2, gutterWidth)).toBe(true)

    ink.unmount()
  })

  it('fails to fence continuation rows when the row disables cross-axis stretch', () => {
    const { ink } = makeInk()
    const gutterWidth = 3

    ink.render(
      React.createElement(MessageRow, {
        alignItems: 'flex-start',
        lines: ['Line one', 'Line two', 'Line three']
      })
    )
    ink.onRender()

    const screen = screenOf(ink)

    expect(gutterNoSelectOnRow(screen, 0, gutterWidth)).toBe(true)
    expect(gutterNoSelectOnRow(screen, 1, gutterWidth)).toBe(false)

    ink.unmount()
  })

  it('fences continuation rows when gutter uses height 100% in a stretch row', () => {
    const { ink } = makeInk()
    const gutterWidth = 3

    ink.render(
      React.createElement(
        Box,
        { flexDirection: 'column' },
        React.createElement(
          Box,
          { alignItems: 'stretch', flexDirection: 'row' },
          React.createElement(
            NoSelect,
            { flexShrink: 0, fromLeftEdge: true, height: '100%', width: gutterWidth },
            React.createElement(Text, null, '> ')
          ),
          React.createElement(
            Box,
            { width: 30 },
            React.createElement(
              Box,
              { flexDirection: 'column' },
              React.createElement(Text, null, 'Line one'),
              React.createElement(Text, null, 'Line two'),
              React.createElement(Text, null, 'Line three')
            )
          )
        )
      )
    )
    ink.onRender()

    const screen = screenOf(ink)

    expect(gutterNoSelectOnRow(screen, 0, gutterWidth)).toBe(true)
    expect(gutterNoSelectOnRow(screen, 1, gutterWidth)).toBe(true)
    expect(gutterNoSelectOnRow(screen, 2, gutterWidth)).toBe(true)

    ink.unmount()
  })

  it('extends gutter noSelect when content grows but the gutter node stays clean', () => {
    const { ink } = makeInk()
    const gutterWidth = 3

    ink.render(React.createElement(MessageRow, { alignItems: 'stretch', lines: ['Short'] }))
    ink.onRender()

    ink.render(
      React.createElement(MessageRow, {
        alignItems: 'stretch',
        lines: ['Line one', 'Line two', 'Line three']
      })
    )
    ink.onRender()

    const screen = screenOf(ink)

    expect(gutterNoSelectOnRow(screen, 0, gutterWidth)).toBe(true)
    expect(gutterNoSelectOnRow(screen, 1, gutterWidth)).toBe(true)
    expect(gutterNoSelectOnRow(screen, 2, gutterWidth)).toBe(true)

    ink.unmount()
  })
})
