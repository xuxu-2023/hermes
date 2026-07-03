'use strict'

// Real-behavior tests for the Windows 8.3 short-path helper.
//
// These exercise the exported toShortPath() function by stubbing
// node:child_process, so they run on any OS and verify the actual logic
// rather than checking source text.

const test = require('node:test')
const assert = require('node:assert/strict')

// Patch child_process before requiring the module under test.
const childProcess = require('node:child_process')

const MODULE_PATH = './windows-short-path.cjs'

function loadModule() {
  const key = require.resolve(MODULE_PATH)
  delete require.cache[key]
  return require(MODULE_PATH)
}

function withExecFileSyncResult(value, fn) {
  const original = childProcess.execFileSync
  childProcess.execFileSync = (...args) => {
    // Return the stub value; if it's an Error, throw it to mimic execFileSync.
    if (value instanceof Error) throw value
    return value
  }
  try {
    return fn()
  } finally {
    childProcess.execFileSync = original
  }
}

test('toShortPath returns the short path when cmd expansion succeeds', () => {
  const original = 'C:\\Program Files\\Git\\cmd\\git.exe'
  const short = 'C:\\PROGRA~1\\Git\\cmd\\git.exe'

  const result = withExecFileSyncResult(short, () => loadModule().toShortPath(original))

  assert.equal(result, short)
})

test('toShortPath falls back to the original path when cmd expansion throws', () => {
  const original = 'C:\\Program Files\\Git\\cmd\\git.exe'

  const result = withExecFileSyncResult(new Error('cmd not found'), () => loadModule().toShortPath(original))

  assert.equal(result, original)
})

test('toShortPath falls back to the original path when expansion returns the same path', () => {
  const original = 'C:\\some\\path\\git.exe'

  const result = withExecFileSyncResult(original, () => loadModule().toShortPath(original))

  assert.equal(result, original)
})

test('toShortPath falls back to the original path when expansion returns empty', () => {
  const original = 'C:\\Program Files\\Git\\cmd\\git.exe'

  const result = withExecFileSyncResult('', () => loadModule().toShortPath(original))

  assert.equal(result, original)
})

test('toShortPath invokes cmd.exe with the correct for-variable short-path expansion', () => {
  const original = 'C:\\Program Files\\Git\\cmd\\git.exe'
  let capturedArgs

  const originalExecFileSync = childProcess.execFileSync
  childProcess.execFileSync = (file, args, options) => {
    capturedArgs = { file, args, options }
    return 'C:\\PROGRA~1\\Git\\cmd\\git.exe'
  }
  try {
    loadModule().toShortPath(original)
  } finally {
    childProcess.execFileSync = originalExecFileSync
  }

  assert.equal(capturedArgs.file, 'cmd.exe')
  assert.deepEqual(capturedArgs.args, ['/c', `for %A in ("${original}") do @echo %~sA`])
  assert.equal(capturedArgs.options.timeout, 5000)
  assert.equal(capturedArgs.options.windowsHide, true)
  assert.equal(capturedArgs.options.encoding, 'utf8')
})
