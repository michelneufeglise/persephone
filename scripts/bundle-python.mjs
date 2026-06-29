#!/usr/bin/env node
/**
 * Persephone — portable Python bundler
 *
 * Downloads a portable Python (astral-sh/python-build-standalone "install_only"
 * tarball) for the requested arch(es), pip-installs server/requirements.txt
 * into it, and writes the result to:
 *
 *   build-resources/python-<arch>/
 *
 * electron-builder picks one of those up per --arch via `extraResources`
 * mappings declared in package.json:
 *
 *   "extraResources": [
 *     { "from": "build-resources/python-${arch}", "to": "python" },
 *     { "from": "server",                          "to": "server" }
 *   ]
 *
 * Repeated runs are incremental — the archive and the extracted runtime are
 * cached and only re-extracted if missing.
 *
 * Run with:
 *   node scripts/bundle-python.mjs               # both arches (default)
 *   node scripts/bundle-python.mjs --arch=arm64  # just arm64
 *   node scripts/bundle-python.mjs --arch=x64    # just intel
 */

import { spawnSync } from 'node:child_process'
import { mkdir, rm, stat, writeFile, readFile } from 'node:fs/promises'
import { existsSync, createWriteStream } from 'node:fs'
import { pipeline } from 'node:stream/promises'
import path from 'node:path'
import https from 'node:https'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT      = path.resolve(__dirname, '..')
const CACHE     = path.join(ROOT, '.python-cache')
const OUT_ROOT  = path.join(ROOT, 'build-resources')
const REQS      = path.join(ROOT, 'server', 'requirements.txt')

// Known-good fallback (used only if the GitHub API can't be reached).
// At runtime we query the API for the *actual* latest release so we don't
// have to chase the constantly-shifting tag/version combos manually.
const FALLBACK_RELEASE = '20260623'
const FALLBACK_VERSION = '3.11.15'

const ARCH_TO_PBS = {
  arm64: 'aarch64-apple-darwin',
  x64:   'x86_64-apple-darwin',
}

const GH_API = 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest'

const C = {
  reset: '\x1b[0m', dim: '\x1b[2m', bold: '\x1b[1m',
  green: '\x1b[32m', yellow: '\x1b[33m', red: '\x1b[31m', cyan: '\x1b[36m',
}

const log = (msg) => process.stdout.write(`${msg}\n`)
const sub = (msg) => process.stdout.write(`  ${C.dim}${msg}${C.reset}\n`)

function fallbackUrl(release, version, arch) {
  return `https://github.com/astral-sh/python-build-standalone/releases/download/${release}/cpython-${version}+${release}-${ARCH_TO_PBS[arch]}-install_only.tar.gz`
}

/**
 * Hit GitHub's releases API to discover the latest tag + the asset URLs
 * for both arches. Falls back to FALLBACK_* if anything goes wrong.
 * Also honours PBS_RELEASE / PBS_VERSION env vars for pinning.
 */
async function discoverRelease() {
  // Env override → use as-is (no API call)
  if (process.env.PBS_RELEASE && process.env.PBS_VERSION) {
    const r = process.env.PBS_RELEASE, v = process.env.PBS_VERSION
    log(`${C.dim}using env-pinned PBS release ${r} / cpython ${v}${C.reset}`)
    return {
      release: r, version: v,
      urls: { arm64: fallbackUrl(r, v, 'arm64'), x64: fallbackUrl(r, v, 'x64') },
    }
  }

  try {
    const res = await fetch(GH_API, { headers: { 'User-Agent': 'persephone-bundler' } })
    if (!res.ok) throw new Error(`gh api ${res.status}`)
    const data = await res.json()
    const tag  = data.tag_name
    const find = (arch) => {
      const re = new RegExp(`cpython-3\\.11\\.\\d+\\+${tag}-${ARCH_TO_PBS[arch]}-install_only\\.tar\\.gz$`)
      const a = (data.assets || []).find((a) => re.test(a.name))
      return a ? a.browser_download_url : null
    }
    const arm64 = find('arm64')
    const x64   = find('x64')
    if (!arm64 || !x64) throw new Error('no install_only asset found in release')
    const vMatch = arm64.match(/cpython-(\d+\.\d+\.\d+)\+/)
    const version = vMatch ? vMatch[1] : 'unknown'
    log(`${C.dim}discovered PBS release ${tag} / cpython ${version}${C.reset}`)
    return { release: tag, version, urls: { arm64, x64 } }
  } catch (err) {
    log(`${C.yellow}!${C.reset} GitHub API unreachable (${err.message}) — using pinned fallback ${FALLBACK_RELEASE}`)
    return {
      release: FALLBACK_RELEASE,
      version: FALLBACK_VERSION,
      urls: {
        arm64: fallbackUrl(FALLBACK_RELEASE, FALLBACK_VERSION, 'arm64'),
        x64:   fallbackUrl(FALLBACK_RELEASE, FALLBACK_VERSION, 'x64'),
      },
    }
  }
}

function parseArchArg() {
  const arg = process.argv.find(a => a.startsWith('--arch='))
  if (!arg) return ['arm64', 'x64']
  const v = arg.split('=')[1]
  if (!(v in ARCH_TO_PBS)) {
    log(`${C.red}unknown arch '${v}' — expected arm64 or x64${C.reset}`)
    process.exit(2)
  }
  return [v]
}

/* ─── download with progress ───────────────────────────────────── */
function download(url, dest) {
  return new Promise((resolve, reject) => {
    const file = createWriteStream(dest)
    const get = (loc) => {
      https.get(loc, res => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          res.resume()
          return get(res.headers.location)
        }
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode} for ${loc}`))
          return
        }
        const total = Number(res.headers['content-length'] ?? 0)
        let seen = 0
        let lastPct = -1
        res.on('data', chunk => {
          seen += chunk.length
          if (total > 0) {
            const pct = Math.floor((seen / total) * 100)
            if (pct >= lastPct + 5) {
              lastPct = pct
              process.stdout.write(`\r  ${C.dim}downloaded ${pct}% (${(seen / 1e6).toFixed(1)}MB)${C.reset}`)
            }
          }
        })
        pipeline(res, file).then(() => {
          process.stdout.write('\n')
          resolve()
        }, reject)
      }).on('error', reject)
    }
    get(url)
  })
}

/* ─── shell helpers ────────────────────────────────────────────── */
function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: 'inherit', ...opts })
  if (r.status !== 0) {
    throw new Error(`${cmd} ${args.join(' ')} → exit ${r.status}`)
  }
}

/* ─── per-arch bundle ──────────────────────────────────────────── */
async function bundleArch(arch, info) {
  log(`\n${C.bold}${C.cyan}▸ ${arch}${C.reset}`)
  await mkdir(CACHE, { recursive: true })
  await mkdir(OUT_ROOT, { recursive: true })

  const url      = info.urls[arch]
  const fileName = path.basename(new URL(url).pathname)
  const archive  = path.join(CACHE, fileName)
  const outDir   = path.join(OUT_ROOT, `python-${arch}`)
  const pyBin    = path.join(outDir, 'python', 'bin', 'python3')

  // 1. Download archive (cached)
  if (!existsSync(archive)) {
    log(`  downloading python-${info.version} (${arch})…`)
    await download(url, archive)
  } else {
    sub(`archive cached at ${path.relative(ROOT, archive)}`)
  }

  // 2. Extract — clean install
  log('  extracting…')
  if (existsSync(outDir)) await rm(outDir, { recursive: true, force: true })
  await mkdir(outDir, { recursive: true })
  run('tar', ['-xzf', archive, '-C', outDir])

  // Tarball layout: outDir/python/{bin,lib,include,…}
  const inner = path.join(outDir, 'python')
  if (!existsSync(pyBin)) {
    throw new Error(`expected ${pyBin} after extraction — archive layout changed?`)
  }
  sub(`extracted to ${path.relative(ROOT, inner)}`)

  // 3. pip install requirements into the bundled interpreter
  log('  pip install -r server/requirements.txt (this may take a minute)…')
  run(pyBin, ['-m', 'pip', 'install', '--upgrade', 'pip', 'wheel'])
  run(pyBin, [
    '-m', 'pip', 'install',
    '--no-input',
    '--no-warn-script-location',
    '-r', REQS,
  ])

  // 4. Stamp it so future runs can skip
  await writeFile(
    path.join(outDir, 'BUNDLE_INFO.json'),
    JSON.stringify({
      python:   info.version,
      release:  info.release,
      arch,
      built_at: new Date().toISOString(),
    }, null, 2),
  )

  const sz = await dirSize(outDir)
  log(`${C.green}  ✓ ${arch} bundle ready · ${(sz / 1e6).toFixed(0)}MB${C.reset}`)
}

async function dirSize(dir) {
  // cheap implementation — `du -s` is more reliable here than walking JS-side
  const r = spawnSync('du', ['-sk', dir], { encoding: 'utf8' })
  if (r.status !== 0) return 0
  return Number(r.stdout.split(/\s+/)[0]) * 1024
}

/* ─── main ─────────────────────────────────────────────────────── */
async function main() {
  log(`${C.bold}🐍 Persephone Python bundler${C.reset}`)

  if (!existsSync(REQS)) {
    log(`${C.red}server/requirements.txt not found at ${REQS}${C.reset}`)
    process.exit(2)
  }

  const info = await discoverRelease()
  log(`${C.dim}python-build-standalone ${info.release} · cpython ${info.version}${C.reset}`)

  const arches = parseArchArg()
  for (const a of arches) await bundleArch(a, info)

  log(`\n${C.bold}${C.green}done.${C.reset} run "${C.bold}npm run dmg${C.reset}" to package.`)
}

main().catch(err => {
  log(`\n${C.red}✗ ${err.message}${C.reset}`)
  process.exit(1)
})
