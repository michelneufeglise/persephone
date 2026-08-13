#!/usr/bin/env node
/**
 * Verifies the `electron` npm package actually has its binary installed,
 * and repairs it if not.
 *
 * Electron's own postinstall (`node_modules/electron/install.js`) downloads
 * a zip via `@electron/get` and unpacks it with the `extract-zip` package.
 *
 * **Windows failure mode:** On some Windows machines extraction silently stops
 * after the first zip entry — no error, exit code 0, but `dist/electron.exe`
 * never gets written — so `electron/index.js` later throws "Electron failed to
 * install correctly" the moment anything tries to launch it. The cached zip
 * itself is fine; this re-extracts it with PowerShell's Expand-Archive,
 * which doesn't share that bug.
 *
 * **macOS/Linux failure mode:** Node v26+ breaks `extract-zip`/`yauzl`: the
 * inflate-raw read stream stalls mid-file with no error and no 'end' event,
 * leaving only a partial extraction. The cached zip is intact; if the current
 * Node is out of range, re-run the installer under a compatible Node version,
 * or re-extract with the system `ditto` (macOS) or `unzip` (Linux) as a fallback.
 *
 * Runs as the root `postinstall` (after `npm install`) and defensively
 * again before `electron:dev`, so already-broken installs self-heal too.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync, rmSync, renameSync, chmodSync, readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'

const __dirname    = path.dirname(fileURLToPath(import.meta.url))
const ROOT         = path.resolve(__dirname, '..')
const ELECTRON_DIR = path.join(ROOT, 'node_modules', 'electron')

function supportedNodeRange() {
  try {
    const pkg = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8'))
    return pkg.engines?.node || null
  } catch (_err) {
    return null
  }
}

function parseVersion(str) {
  const match = str.match(/^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?/)
  if (!match) return null
  return [parseInt(match[1], 10), parseInt(match[2] || 0, 10), parseInt(match[3] || 0, 10)]
}

function versionInRange(versionStr, range) {
  if (!range) return true
  const version = parseVersion(versionStr)
  if (!version) return false

  const clauses = range.trim().split(/\s+/)
  for (const clause of clauses) {
    const opMatch = clause.match(/^(>=|>|<=|<)(.+)$/)
    if (!opMatch) {
      continue
    }

    const op = opMatch[1]
    const clauseVersion = parseVersion(opMatch[2])
    if (!clauseVersion) continue

    const cmp = (a, b) => {
      if (a[0] !== b[0]) return a[0] - b[0]
      if (a[1] !== b[1]) return a[1] - b[1]
      return a[2] - b[2]
    }

    const c = cmp(version, clauseVersion)
    if (op === '>=' && !(c >= 0)) return false
    if (op === '>' && !(c > 0)) return false
    if (op === '<=' && !(c <= 0)) return false
    if (op === '<' && !(c < 0)) return false
  }

  return true
}

function nodeVersionOf(exePath) {
  try {
    const r = spawnSync(exePath, ['--version'], { encoding: 'utf8' })
    if (r.status === 0) return r.stdout.trim()
  } catch (_err) {
    // ignore
  }
  return null
}

function findCompatibleNode(range) {
  const candidates = []

  // Standard paths
  if (process.platform !== 'win32') {
    const stdPaths = ['/usr/local/bin/node', '/opt/homebrew/bin/node', '/usr/bin/node']
    for (const p of stdPaths) {
      if (p !== process.execPath && existsSync(p)) {
        candidates.push(p)
      }
    }

    // which -a node
    try {
      const r = spawnSync('which', ['-a', 'node'], { encoding: 'utf8' })
      if (r.status === 0) {
        const lines = r.stdout.trim().split('\n').filter(l => l.length > 0)
        for (const line of lines) {
          if (line !== process.execPath && !candidates.includes(line) && existsSync(line)) {
            candidates.push(line)
          }
        }
      }
    } catch (_err) {
      // ignore
    }
  } else {
    // On Windows, use `where node`
    try {
      const r = spawnSync('where', ['node'], { encoding: 'utf8' })
      if (r.status === 0) {
        const lines = r.stdout.trim().split('\n').filter(l => l.length > 0)
        for (const line of lines) {
          if (line !== process.execPath && !candidates.includes(line) && existsSync(line)) {
            candidates.push(line)
          }
        }
      }
    } catch (_err) {
      // ignore
    }
  }

  // Version manager directories
  const vmDirs = [
    { base: path.join(homedir(), '.nvm', 'versions', 'node'), pathFn: (e) => path.join(e, 'bin', 'node') },
    { base: path.join(homedir(), '.fnm', 'node-versions'), pathFn: (e) => [path.join(e, 'bin', 'node'), path.join(e, 'installation', 'bin', 'node')] },
    { base: path.join(homedir(), '.volta', 'tools', 'image', 'node'), pathFn: (e) => path.join(e, 'bin', 'node') },
  ]

  const vmCandidates = []
  for (const vm of vmDirs) {
    if (!existsSync(vm.base)) continue
    try {
      const entries = readdirSync(vm.base)
      for (const entry of entries) {
        const paths = Array.isArray(vm.pathFn(entry)) ? vm.pathFn(entry) : [vm.pathFn(entry)]
        for (const p of paths) {
          if (p !== process.execPath && !candidates.includes(p) && !vmCandidates.some(c => c.path === p) && existsSync(p)) {
            vmCandidates.push({ path: p, entry })
          }
        }
      }
    } catch (_err) {
      // ignore
    }
  }

  // Sort by descending version
  vmCandidates.sort((a, b) => {
    const va = parseVersion(a.entry)
    const vb = parseVersion(b.entry)
    if (!va || !vb) return 0
    if (va[0] !== vb[0]) return vb[0] - va[0]
    if (va[1] !== vb[1]) return vb[1] - va[1]
    return vb[2] - va[2]
  })

  for (const vm of vmCandidates) {
    candidates.push(vm.path)
  }

  // Dedup and find first in range
  const seen = new Set()
  for (const p of candidates) {
    if (seen.has(p)) continue
    seen.add(p)

    const ver = nodeVersionOf(p)
    if (ver && versionInRange(ver, range)) {
      console.log(`[ensure-electron] found compatible Node ${ver} at ${p}`)
      return p
    }
  }

  return null
}

function installCompatibleNode() {
  const range = '>=20.19.0 <25'

  // fnm
  try {
    const r = spawnSync('fnm', ['--version'], { encoding: 'utf8' })
    if (r.status === 0) {
      console.log('[ensure-electron] installing Node 24 via fnm…')
      spawnSync('fnm', ['install', '24'], { stdio: 'inherit' })
      const alt = findCompatibleNode(range)
      if (alt) return alt
    }
  } catch (_err) {
    // ignore
  }

  // volta
  try {
    const r = spawnSync('volta', ['--version'], { encoding: 'utf8' })
    if (r.status === 0) {
      console.log('[ensure-electron] installing Node 24 via volta…')
      spawnSync('volta', ['install', 'node@24'], { stdio: 'inherit' })
      const alt = findCompatibleNode(range)
      if (alt) return alt
    }
  } catch (_err) {
    // ignore
  }

  // nvm
  try {
    const nvmSh = path.join(homedir(), '.nvm', 'nvm.sh')
    if (existsSync(nvmSh)) {
      console.log('[ensure-electron] installing Node 24 via nvm…')
      const r = spawnSync('bash', ['-lc', '. "$HOME/.nvm/nvm.sh" && nvm install 24 >/dev/null && nvm which 24'], { encoding: 'utf8' })
      if (r.status === 0) {
        const lines = r.stdout.trim().split('\n').filter(l => l.length > 0)
        if (lines.length > 0) {
          const nodePath = lines[lines.length - 1]
          if (existsSync(nodePath)) {
            const ver = nodeVersionOf(nodePath)
            if (ver && versionInRange(ver, range)) {
              console.log(`[ensure-electron] found compatible Node ${ver} at ${nodePath}`)
              return nodePath
            }
          }
        }
      }
    }
  } catch (_err) {
    // ignore
  }

  return null
}

function isInstalled() {
  const pathFile = path.join(ELECTRON_DIR, 'path.txt')
  if (!existsSync(pathFile)) return false
  const exe = readFileSync(pathFile, 'utf8').trim()
  return exe.length > 0 && existsSync(path.join(ELECTRON_DIR, 'dist', exe))
}

function runOfficialInstaller(nodeExe = process.execPath) {
  const r = spawnSync(nodeExe, [path.join(ELECTRON_DIR, 'install.js')], { stdio: 'inherit' })
  return r.status === 0
}

function platformExecPath() {
  const platform = process.env.npm_config_platform || process.platform
  if (platform === 'darwin' || platform === 'mas') {
    return 'Electron.app/Contents/MacOS/Electron'
  }
  if (platform === 'linux' || platform === 'freebsd' || platform === 'openbsd') {
    return 'electron'
  }
  if (platform === 'win32') {
    return 'electron.exe'
  }
  return 'electron'
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

async function repairOnPosix() {
  console.log('[ensure-electron] electron binary missing or truncated — re-extracting with the system unzip…')

  const pkg = JSON.parse(readFileSync(path.join(ELECTRON_DIR, 'package.json'), 'utf8'))
  const checksumsPath = path.join(ELECTRON_DIR, 'checksums.json')

  const { downloadArtifact } = await import('@electron/get')
  const zipPath = await downloadArtifact({
    version:      pkg.version,
    artifactName: 'electron',
    platform:     process.env.npm_config_platform || process.platform,
    arch:         process.env.npm_config_arch || process.arch,
    cacheRoot:    process.env.electron_config_cache,
    checksums:    existsSync(checksumsPath) ? JSON.parse(readFileSync(checksumsPath, 'utf8')) : undefined,
  })

  const distDir = path.join(ELECTRON_DIR, 'dist')
  rmSync(distDir, { recursive: true, force: true })
  mkdirSync(distDir, { recursive: true })

  const platform = process.env.npm_config_platform || process.platform
  const tool = platform === 'darwin' ? 'ditto' : 'unzip'
  const args = platform === 'darwin'
    ? ['-x', '-k', zipPath, distDir]
    : ['-q', '-o', zipPath, '-d', distDir]

  const extractResult = spawnSync(tool, args, { stdio: 'inherit' })

  if (extractResult.error) {
    console.error(`[ensure-electron] ${tool} not found:`, extractResult.error.message)
    return
  }

  if (extractResult.status !== 0) {
    console.error(`[ensure-electron] extraction failed with status ${extractResult.status}`)
    return
  }

  // Move electron.d.ts if it exists
  const typeDefSource = path.join(distDir, 'electron.d.ts')
  if (existsSync(typeDefSource)) {
    const typeDefDest = path.join(ELECTRON_DIR, 'electron.d.ts')
    renameSync(typeDefSource, typeDefDest)
  }

  // Write path.txt
  writeFileSync(path.join(ELECTRON_DIR, 'path.txt'), platformExecPath())

  // chmod the binary
  try {
    chmodSync(path.join(distDir, platformExecPath()), 0o755)
  } catch (_err) {
    // ignore chmod errors
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
    if (!isInstalled()) {
      const range = supportedNodeRange()
      if (!versionInRange(process.version, range)) {
        console.log(`[ensure-electron] Node ${process.version} is outside ${range} — electron's installer truncates archives on this version`)
        let alt = findCompatibleNode(range)
        if (!alt) {
          alt = installCompatibleNode()
          if (alt) {
            if (!versionInRange(nodeVersionOf(alt), range)) {
              alt = null
            }
          }
        }
        if (alt) {
          console.log(`[ensure-electron] re-running installer under compatible Node at ${alt}`)
          runOfficialInstaller(alt)
        }
      }
    }

    if (!isInstalled()) {
      try {
        await repairOnPosix()
      } catch (err) {
        console.error('[ensure-electron] POSIX repair attempt failed:', err?.message ?? err)
      }
    }
  }

  if (isInstalled()) {
    console.log('[ensure-electron] electron is installed ✓')
  } else {
    if (process.platform === 'win32') {
      console.error(
        '[ensure-electron] electron still missing after repair. Try: delete node_modules/electron, ' +
        'then run npm install again. If it keeps failing, an antivirus/EDR product may be interfering ' +
        'with file extraction in node_modules — try excluding the project folder and the ' +
        '%LOCALAPPDATA%\\electron\\Cache folder from real-time scanning.',
      )
    } else {
      console.error(
        '[ensure-electron] electron still missing after repair. Node ' + process.version +
        ' may be the problem — `extract-zip` (used by electron\'s installer) is known to truncate ' +
        'archives on Node 26+; install with Node 22 or 24 LTS, or delete node_modules/electron ' +
        'and re-run npm install.',
      )
    }
    process.exitCode = 1
  }
}

// Top-level await so callers doing `await import('./ensure-electron.mjs')`
// actually wait for the repair to finish before proceeding.
await main()
