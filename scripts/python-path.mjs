#!/usr/bin/env node
// Python interpreter discovery and resolution.
// Two exports:
// - resolvePython(): fast path resolution (env, venv, fallback)
// - discoverPython(): probes filesystem/PATH to find best Python for venv creation

import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')

const IS_WIN = process.platform === 'win32'

// Probe script to extract Python version, venv status, and executable path
const PROBE_SCRIPT = 'import sys; print("%d.%d.%d" % sys.version_info[:3]); print(sys.prefix != sys.base_prefix); print(sys.executable)'

// resolvePython() → string
// Returns the Python interpreter path that should be used to run the app.
// Fast, no probing. Checks (in order):
//  1. PERSEPHONE_PYTHON env var
//  2. Repo-local venv
//  3. VIRTUAL_ENV + bin/Scripts suffix
//  4. Fallback 'python3' (POSIX) or 'python' (Windows)
export function resolvePython() {
  // 1. Env var
  if (process.env.PERSEPHONE_PYTHON) {
    if (existsSync(process.env.PERSEPHONE_PYTHON)) {
      return process.env.PERSEPHONE_PYTHON
    }
  }

  // 2. Repo-local venv
  const venvInterp = IS_WIN
    ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(ROOT, '.venv', 'bin', 'python')
  if (existsSync(venvInterp)) {
    return venvInterp
  }

  // 3. VIRTUAL_ENV
  if (process.env.VIRTUAL_ENV) {
    const venvInterp = IS_WIN
      ? path.join(process.env.VIRTUAL_ENV, 'Scripts', 'python.exe')
      : path.join(process.env.VIRTUAL_ENV, 'bin', 'python')
    if (existsSync(venvInterp)) {
      return venvInterp
    }
  }

  // 4. Fallback
  return IS_WIN ? 'python' : 'python3'
}

// discoverPython() → { path, version } | null
// Probes the system for the best Python 3.10–3.13 installation suitable for venv creation.
// Returns the top candidate by (major, minor version preference, patch version), or null.
// Rejects venv interpreters (sys.prefix !== sys.base_prefix).
export function discoverPython() {
  const candidates = new Map() // path → { path, version, minor, patch }

  function probeCandidate(interpreterPath) {
    if (!interpreterPath || candidates.has(interpreterPath)) {
      return
    }

    try {
      const result = spawnSync(
        interpreterPath,
        ['-c', PROBE_SCRIPT],
        { encoding: 'utf8', timeout: 5000 },
      )

      if (result.error || result.status !== 0) {
        return
      }

      const lines = result.stdout.trim().split('\n')
      const version = lines[0]
      const isVenv = lines[1] === 'True'
      const executable = lines[2] || interpreterPath

      if (isVenv) {
        return // Skip venv interpreters
      }

      const [major, minor, patch] = version.split('.').map(Number)

      // Only accept 3.10–3.13
      if (major !== 3 || minor < 10 || minor > 13 || isNaN(patch)) {
        return
      }

      candidates.set(executable, { path: executable, version, minor, patch })
    } catch {
      // Silently skip unprobeable candidates
    }
  }

  function probeWindowsPyLauncher(minor) {
    try {
      const result = spawnSync(
        'py',
        [`-3.${minor}`, '-c', PROBE_SCRIPT],
        { encoding: 'utf8', timeout: 5000 },
      )

      if (result.error || result.status !== 0) {
        return
      }

      const lines = result.stdout.trim().split('\n')
      const version = lines[0]
      const isVenv = lines[1] === 'True'
      const executable = lines[2]

      if (isVenv || !executable) {
        return
      }

      const [major, minorParsed, patch] = version.split('.').map(Number)

      if (major !== 3 || minorParsed < 10 || minorParsed > 13 || isNaN(patch)) {
        return
      }

      candidates.set(executable, { path: executable, version, minor: minorParsed, patch })
    } catch {
      // Silently skip
    }
  }

  // 1. Env var
  if (process.env.PERSEPHONE_PYTHON) {
    probeCandidate(process.env.PERSEPHONE_PYTHON)
  }

  // 2. PATH names
  const pathNames = ['python3.11', 'python3.12', 'python3.13', 'python3.10', 'python3', 'python']
  for (const name of pathNames) {
    try {
      const cmd = IS_WIN ? `where ${name}` : `which ${name}`
      const result = spawnSync(IS_WIN ? 'cmd' : '/bin/sh', [IS_WIN ? '/c' : '-c', cmd], {
        encoding: 'utf8',
        timeout: 2000,
      })
      if (result.status === 0) {
        const resolved = result.stdout.trim().split('\n')[0]
        probeCandidate(resolved)
      }
    } catch {
      // Skip if which/where fails
    }
  }

  // 3. POSIX absolute directories
  if (!IS_WIN) {
    const absDirs = ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin']
    for (const dir of absDirs) {
      for (const name of ['python3.11', 'python3.12', 'python3.13', 'python3.10', 'python3']) {
        probeCandidate(path.join(dir, name))
      }
    }
  }

  // 4. pyenv (macOS / Linux)
  if (!IS_WIN) {
    try {
      const pyenvVersionsDir = path.join(homedir(), '.pyenv', 'versions')
      if (existsSync(pyenvVersionsDir)) {
        const versions = readdirSync(pyenvVersionsDir)
        for (const ver of versions) {
          probeCandidate(path.join(pyenvVersionsDir, ver, 'bin', 'python3'))
        }
      }
    } catch {
      // Silently skip pyenv
    }
  }

  // 5. macOS Framework builds
  if (process.platform === 'darwin') {
    try {
      const fwDir = '/Library/Frameworks/Python.framework/Versions'
      if (existsSync(fwDir)) {
        const versions = readdirSync(fwDir)
        for (const ver of versions) {
          probeCandidate(path.join(fwDir, ver, 'bin', 'python3'))
        }
      }
    } catch {
      // Silently skip
    }
  }

  // 6. Windows py launcher and AppData installs
  if (IS_WIN) {
    // Try py launcher for each minor
    for (const minor of [11, 12, 13, 10]) {
      probeWindowsPyLauncher(minor)
    }

    // Try AppData\Local\Programs\Python\*\python.exe
    try {
      const pythonDir = path.join(process.env.LOCALAPPDATA ?? '', 'Programs', 'Python')
      if (existsSync(pythonDir)) {
        const versions = readdirSync(pythonDir)
        for (const ver of versions) {
          probeCandidate(path.join(pythonDir, ver, 'python.exe'))
        }
      }
    } catch {
      // Silently skip
    }
  }

  // Comparator: prefer higher minorScore, then higher patch
  function isBetter(cand, best) {
    const candMinorScore = cand.minor === 11 ? 4 : cand.minor === 12 ? 3 : cand.minor === 13 ? 2 : 1
    const bestMinorScore = best.minor === 11 ? 4 : best.minor === 12 ? 3 : best.minor === 13 ? 2 : 1

    if (candMinorScore !== bestMinorScore) {
      return candMinorScore > bestMinorScore
    }

    return cand.patch > best.patch
  }

  // Find the best candidate
  let best = null
  for (const cand of candidates.values()) {
    if (!best || isBetter(cand, best)) {
      best = cand
    }
  }

  return best ? { path: best.path, version: best.version } : null
}
