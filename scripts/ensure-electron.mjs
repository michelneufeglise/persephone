#!/usr/bin/env node
/**
 * Verifies the `electron` npm package actually has its binary installed,
 * and repairs it on Windows if not.
 *
 * Electron's own postinstall (`node_modules/electron/install.js`) downloads
 * a zip via `@electron/get` and unpacks it with the `extract-zip` package.
 * On some Windows machines that extraction silently stops after the first
 * zip entry — no error, exit code 0, but `dist/electron.exe` never gets
 * written — so `electron/index.js` later throws "Electron failed to
 * install correctly" the moment anything tries to launch it. The cached
 * zip itself is fine; this re-extracts it with PowerShell's
 * Expand-Archive instead, which doesn't share that bug.
 *
 * Runs as the root `postinstall` (after `npm install`) and defensively
 * again before `electron:dev`, so already-broken installs self-heal too.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname    = path.dirname(fileURLToPath(import.meta.url))
const ROOT         = path.resolve(__dirname, '..')
const ELECTRON_DIR = path.join(ROOT, 'node_modules', 'electron')

function isInstalled() {
  const pathFile = path.join(ELECTRON_DIR, 'path.txt')
  if (!existsSync(pathFile)) return false
  const exe = readFileSync(pathFile, 'utf8').trim()
  return exe.length > 0 && existsSync(path.join(ELECTRON_DIR, 'dist', exe))
}

function runOfficialInstaller() {
  const r = spawnSync(process.execPath, [path.join(ELECTRON_DIR, 'install.js')], { stdio: 'inherit' })
  return r.status === 0
}

async function repairOnWindows() {
  console.log('[ensure-electron] electron.exe missing — repairing via PowerShell Expand-Archive…')

  const pkg = JSON.parse(readFileSync(path.join(ELECTRON_DIR, 'package.json'), 'utf8'))
  const checksumsPath = path.join(ELECTRON_DIR, 'checksums.json')

  const { downloadArtifact } = await import('@electron/get')
  const zipPath = await downloadArtifact({
    version:      pkg.version,
    artifactName: 'electron',
    platform:     'win32',
    arch:         process.env.npm_config_arch || process.arch,
    cacheRoot:    process.env.electron_config_cache,
    checksums:    existsSync(checksumsPath) ? JSON.parse(readFileSync(checksumsPath, 'utf8')) : undefined,
  })

  const distDir = path.join(ELECTRON_DIR, 'dist')
  mkdirSync(distDir, { recursive: true })

  const ps = spawnSync('powershell', [
    '-NoProfile', '-NonInteractive', '-Command',
    `Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${distDir}' -Force`,
  ], { stdio: 'inherit' })

  if (ps.status === 0) {
    writeFileSync(path.join(ELECTRON_DIR, 'path.txt'), 'electron.exe')
  }
}

async function main() {
  if (!existsSync(ELECTRON_DIR)) return
  if (isInstalled()) return

  if (process.platform === 'win32') {
    try {
      await repairOnWindows()
    } catch (err) {
      console.error('[ensure-electron] repair attempt failed:', err?.message ?? err)
    }
    if (!isInstalled()) {
      console.log('[ensure-electron] falling back to the standard installer…')
      runOfficialInstaller()
    }
  } else {
    runOfficialInstaller()
  }

  if (isInstalled()) {
    console.log('[ensure-electron] electron is installed ✓')
  } else {
    console.error(
      '[ensure-electron] electron still missing after repair. Try: delete node_modules/electron, ' +
      'then run npm install again. If it keeps failing, an antivirus/EDR product may be interfering ' +
      'with file extraction in node_modules — try excluding the project folder and the ' +
      '%LOCALAPPDATA%\\electron\\Cache folder from real-time scanning.',
    )
    process.exitCode = 1
  }
}

// Top-level await so callers doing `await import('./ensure-electron.mjs')`
// actually wait for the repair to finish before proceeding.
await main()
