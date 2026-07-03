'use strict'

// Windows 8.3 short-path helper used by resolveGitBinary.
//
// simple-git's customBinaryPlugin validates the binary path with a regex that
// does not allow spaces. Git-for-Windows is installed under "C:\Program Files"
// by default, so the resolved absolute path must be converted to its short
// form before being passed to simple-git.

const { execFileSync } = require('node:child_process')

/**
 * Convert a Windows path to its 8.3 short-path form.
 *
 * Uses cmd.exe's for-variable expansion (`%~sA`) so the result contains no
 * spaces and passes simple-git's isBadArgument regex. Falls back to the
 * original path if the lookup fails or returns the same path.
 *
 * @param {string} filePath
 * @returns {string}
 */
function toShortPath(filePath) {
  try {
    const result = execFileSync(
      'cmd.exe',
      ['/c', `for %A in ("${filePath}") do @echo %~sA`],
      { timeout: 5000, windowsHide: true, encoding: 'utf8' }
    )
      .toString()
      .trim()
    if (result && result !== filePath) return result
  } catch {
    // fall through
  }
  return filePath
}

module.exports = { toShortPath }
