'use strict'

const assert = require('node:assert')
const test = require('node:test')
const zlib = require('node:zlib')

const { __testing, extractThemes, readCentralDirectory } = require('./vscode-marketplace.cjs')

// Build a minimal zip so the test controls the bytes exactly — exercises the
// central-directory reader + theme extraction without a real zip dependency.
// Each entry may be `stored` (default) or `deflate`, letting us construct a
// deflated entry whose inflated size dwarfs its compressed size (a zip bomb).
function makeZip(entries) {
  const locals = []
  const centrals = []
  let offset = 0

  for (const { name, data, method = 'stored' } of entries) {
    const nameBuf = Buffer.from(name, 'utf8')
    const raw = Buffer.isBuffer(data) ? data : Buffer.from(data, 'utf8')
    const deflate = method === 'deflate'
    const body = deflate ? zlib.deflateRawSync(raw) : raw
    const methodCode = deflate ? 8 : 0

    const local = Buffer.alloc(30 + nameBuf.length)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(methodCode, 8) // method: stored | deflate
    local.writeUInt32LE(body.length, 18) // compressed size
    local.writeUInt32LE(raw.length, 22) // uncompressed size
    local.writeUInt16LE(nameBuf.length, 26)
    nameBuf.copy(local, 30)

    locals.push(local, body)

    const central = Buffer.alloc(46 + nameBuf.length)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE(methodCode, 10) // method: stored | deflate
    central.writeUInt32LE(body.length, 20)
    central.writeUInt32LE(raw.length, 24)
    central.writeUInt16LE(nameBuf.length, 28)
    central.writeUInt32LE(offset, 42) // local header offset
    nameBuf.copy(central, 46)

    centrals.push(central)
    offset += local.length + body.length
  }

  const centralStart = offset
  const centralBuf = Buffer.concat(centrals)

  const eocd = Buffer.alloc(22)
  eocd.writeUInt32LE(0x06054b50, 0)
  eocd.writeUInt16LE(entries.length, 8)
  eocd.writeUInt16LE(entries.length, 10)
  eocd.writeUInt32LE(centralBuf.length, 12)
  eocd.writeUInt32LE(centralStart, 16)

  return Buffer.concat([...locals, centralBuf, eocd])
}

test('readCentralDirectory finds every entry', () => {
  const zip = makeZip([
    { name: 'extension/package.json', data: '{}' },
    { name: 'extension/themes/x.json', data: '{}' }
  ])

  const records = readCentralDirectory(zip)
  assert.ok(records.has('extension/package.json'))
  assert.ok(records.has('extension/themes/x.json'))
})

test('extractThemes reads contributed color themes (resolving ./ paths)', () => {
  const pkg = JSON.stringify({
    name: 'theme-dracula',
    displayName: 'Dracula',
    contributes: {
      themes: [{ label: 'Dracula', uiTheme: 'vs-dark', path: './themes/dracula.json' }]
    }
  })
  const themeJson = JSON.stringify({ name: 'Dracula', type: 'dark', colors: { 'editor.background': '#282a36' } })

  const zip = makeZip([
    { name: 'extension/package.json', data: pkg },
    { name: 'extension/themes/dracula.json', data: themeJson }
  ])

  const themes = extractThemes(zip)
  assert.strictEqual(themes.length, 1)
  assert.strictEqual(themes[0].label, 'Dracula')
  assert.strictEqual(themes[0].uiTheme, 'vs-dark')
  assert.match(themes[0].contents, /editor\.background/)
})

test('extractThemes returns empty when the extension contributes no themes', () => {
  const zip = makeZip([{ name: 'extension/package.json', data: JSON.stringify({ name: 'x', contributes: {} }) }])
  assert.deepStrictEqual(extractThemes(zip), [])
})

test('extractThemes throws when the manifest is missing', () => {
  const zip = makeZip([{ name: 'extension/other.txt', data: 'hi' }])
  assert.throws(() => extractThemes(zip), /manifest missing/i)
})

test('extractThemes skips a decompression-bomb theme entry instead of inflating it', () => {
  // A small deflated entry whose inflated size (17 MB) exceeds MAX_ENTRY_BYTES
  // (16 MB). Without the inflate cap this allocates the full buffer (the bomb);
  // with the cap zlib throws ERR_BUFFER_TOO_LARGE and the per-theme try/catch
  // drops just this theme rather than failing the whole install.
  const bomb = Buffer.alloc(17 * 1024 * 1024, 0x41) // 17 MB of 'A'.
  const pkg = JSON.stringify({
    name: 'theme-bomb',
    displayName: 'Bomb',
    contributes: {
      themes: [
        { label: 'Bomb', uiTheme: 'vs-dark', path: './themes/bomb.json' },
        { label: 'Safe', uiTheme: 'vs-dark', path: './themes/safe.json' }
      ]
    }
  })
  const safeJson = JSON.stringify({ name: 'Safe', type: 'dark', colors: { 'editor.background': '#000000' } })

  const zip = makeZip([
    { name: 'extension/package.json', data: pkg, method: 'deflate' },
    { name: 'extension/themes/bomb.json', data: bomb, method: 'deflate' },
    { name: 'extension/themes/safe.json', data: safeJson, method: 'deflate' }
  ])

  const themes = extractThemes(zip)
  // The oversized entry is skipped; the legitimate sibling still extracts.
  assert.strictEqual(themes.length, 1)
  assert.strictEqual(themes[0].label, 'Safe')
  assert.ok(!themes.some(theme => theme.label === 'Bomb'))
})

test('extractThemes skips an oversized stored theme entry instead of reading it', () => {
  // The stored path has no zlib cap, so the explicit `data.length > maxBytes`
  // check is the only guard. A 17 MB stored entry (> MAX_ENTRY_BYTES of 16 MB)
  // must be skipped by the per-theme try/catch, leaving the legitimate sibling.
  const oversized = Buffer.alloc(17 * 1024 * 1024, 0x41) // 17 MB of 'A'.
  const pkg = JSON.stringify({
    name: 'theme-stored-bomb',
    displayName: 'StoredBomb',
    contributes: {
      themes: [
        { label: 'StoredBomb', uiTheme: 'vs-dark', path: './themes/big.json' },
        { label: 'Safe', uiTheme: 'vs-dark', path: './themes/safe.json' }
      ]
    }
  })
  const safeJson = JSON.stringify({ name: 'Safe', type: 'dark', colors: { 'editor.background': '#000000' } })

  const zip = makeZip([
    { name: 'extension/package.json', data: pkg },
    { name: 'extension/themes/big.json', data: oversized },
    { name: 'extension/themes/safe.json', data: safeJson }
  ])

  const themes = extractThemes(zip)
  assert.strictEqual(themes.length, 1)
  assert.strictEqual(themes[0].label, 'Safe')
  assert.ok(!themes.some(theme => theme.label === 'StoredBomb'))
})

test('extractThemes fails closed when the stored package.json manifest is oversized', () => {
  // The manifest is parsed outside the per-theme try/catch, so an oversized
  // stored manifest must throw (fail closed) rather than be read unbounded.
  const oversized = Buffer.alloc(17 * 1024 * 1024, 0x7b) // 17 MB of '{'.

  const zip = makeZip([{ name: 'extension/package.json', data: oversized }])

  assert.throws(() => extractThemes(zip), /exceeds the extraction size limit/i)
})

test('extractThemes rejects an unsupported zip compression method', () => {
  // Only stored (0) and deflate (8) are allowed; an unknown method must throw
  // instead of being misread as raw deflate. The manifest is parsed outside the
  // per-theme try/catch, so patching its central-directory method surfaces the
  // rejection through the public entry point.
  const zip = makeZip([{ name: 'extension/package.json', data: '{}' }])

  // Patch the method byte in both the local (offset 8) and central headers. The
  // central-directory method lives at `centralStart + 10`; locate it via the
  // EOCD's central-directory offset field rather than hardcoding a position.
  const eocd = zip.length - 22
  const centralStart = zip.readUInt32LE(eocd + 16)
  zip.writeUInt16LE(99, 8) // local header method.
  zip.writeUInt16LE(99, centralStart + 10) // central header method.

  assert.throws(() => extractThemes(zip), /unsupported zip compression method/i)
})

test('extractThemes still reads a normal deflated theme under the inflate cap', () => {
  // Regression guard: the inflate cap must not break legitimate deflated themes.
  const pkg = JSON.stringify({
    name: 'theme-dracula',
    displayName: 'Dracula',
    contributes: {
      themes: [{ label: 'Dracula', uiTheme: 'vs-dark', path: './themes/dracula.json' }]
    }
  })
  const themeJson = JSON.stringify({ name: 'Dracula', type: 'dark', colors: { 'editor.background': '#282a36' } })

  const zip = makeZip([
    { name: 'extension/package.json', data: pkg, method: 'deflate' },
    { name: 'extension/themes/dracula.json', data: themeJson, method: 'deflate' }
  ])

  const themes = extractThemes(zip)
  assert.strictEqual(themes.length, 1)
  assert.strictEqual(themes[0].label, 'Dracula')
  assert.match(themes[0].contents, /editor\.background/)
})

test('looksLikeIconTheme filters icon/product-icon packs out of theme search', () => {
  const { looksLikeIconTheme } = __testing

  // Tagged contribution points are the strongest signal.
  assert.strictEqual(looksLikeIconTheme({ tags: ['theme', 'icon-theme'] }), true)
  assert.strictEqual(looksLikeIconTheme({ tags: ['product-icon-theme'] }), true)

  // Name/description fallback for packs that don't tag themselves.
  assert.strictEqual(looksLikeIconTheme({ displayName: 'Material Icon Theme' }), true)
  assert.strictEqual(looksLikeIconTheme({ shortDescription: 'A pack of file icons.' }), true)

  // Real color themes survive.
  assert.strictEqual(looksLikeIconTheme({ displayName: 'Dracula Official', tags: ['theme', 'color-theme'] }), false)
  assert.strictEqual(looksLikeIconTheme({ displayName: 'One Dark Pro' }), false)
})
