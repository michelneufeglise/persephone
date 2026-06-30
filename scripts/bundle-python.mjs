#!/usr/bin/env node
/**
 * Persephone — portable Python bundler
 *
 * Downloads a portable Python (astral-sh/python-build-standalone "install_only"
 * tarball) for the requested platform/arch(es), pip-installs
 * server/requirements.txt into it, and writes the result to:
 *
 *   build-resources/python-<arch>/        (macOS)
 *   build-resources/python-win-<arch>/    (Windows)
 *
 * electron-builder picks one of those up via the per-platform `extraResources`
 * mappings declared in package.json (build.mac.extraResources /
 * build.win.extraResources).
 *
 * Repeated runs are incremental — the archive and the extracted runtime are
 * cached and only re-extracted if missing.
 *
 * pip-installing native deps (torch, etc.) requires actually *running* the
 * target interpreter, so this script must be run on the matching host OS:
 * macOS bundles on macOS, Windows bundles on Windows.
 *
 * Run with:
 *   node scripts/bundle-python.mjs                       # both mac arches (default on macOS)
 *   node scripts/bundle-python.mjs --arch=arm64           # just arm64
 *   node scripts/bundle-python.mjs --arch=x64              # just intel
 *   node scripts/bundle-python.mjs --platform=win --arch=x64   # Windows (run on Windows)
 */

import { spawnSync } from 'node:child_process'
import { mkdir, rm, stat, writeFile, readFile, readdir } from 'node:fs/promises'
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

// python-build-standalone target triples, keyed by our own platform/arch names.
const PBS_TARGETS = {
  mac: {
    arm64: 'aarch64-apple-darwin',
    x64:   'x86_64-apple-darwin',
  },
  win: {
    x64:   'x86_64-pc-windows-msvc',
  },
}

function pbsTarget(platform, arch) {
  const t = PBS_TARGETS[platform]?.[arch]
  if (!t) throw new Error(`no python-build-standalone target for ${platform}/${arch}`)
  return t
}

const GH_API = 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest'

const C = {
  reset: '\x1b[0m', dim: '\x1b[2m', bold: '\x1b[1m',
  green: '\x1b[32m', yellow: '\x1b[33m', red: '\x1b[31m', cyan: '\x1b[36m',
}

const log = (msg) => process.stdout.write(`${msg}\n`)
const sub = (msg) => process.stdout.write(`  ${C.dim}${msg}${C.reset}\n`)

function fallbackUrl(release, version, platform, arch) {
  return `https://github.com/astral-sh/python-build-standalone/releases/download/${release}/cpython-${version}+${release}-${pbsTarget(platform, arch)}-install_only.tar.gz`
}

/**
 * Hit GitHub's releases API to discover the latest tag + the asset URLs
 * for each requested arch. Falls back to FALLBACK_* if anything goes wrong.
 * Also honours PBS_RELEASE / PBS_VERSION env vars for pinning.
 */
async function discoverRelease(platform, arches) {
  // Env override → use as-is (no API call)
  if (process.env.PBS_RELEASE && process.env.PBS_VERSION) {
    const r = process.env.PBS_RELEASE, v = process.env.PBS_VERSION
    log(`${C.dim}using env-pinned PBS release ${r} / cpython ${v}${C.reset}`)
    const urls = {}
    for (const a of arches) urls[a] = fallbackUrl(r, v, platform, a)
    return { release: r, version: v, urls }
  }

  try {
    const res = await fetch(GH_API, { headers: { 'User-Agent': 'persephone-bundler' } })
    if (!res.ok) throw new Error(`gh api ${res.status}`)
    const data = await res.json()
    const tag  = data.tag_name
    const find = (arch) => {
      const re = new RegExp(`cpython-3\\.11\\.\\d+\\+${tag}-${pbsTarget(platform, arch)}-install_only\\.tar\\.gz$`)
      const a = (data.assets || []).find((a) => re.test(a.name))
      return a ? a.browser_download_url : null
    }
    const urls = {}
    for (const a of arches) urls[a] = find(a)
    const missing = arches.filter(a => !urls[a])
    if (missing.length) throw new Error(`no install_only asset found for ${missing.join(', ')} in release`)
    const first = urls[arches[0]]
    const vMatch = first.match(/cpython-(\d+\.\d+\.\d+)\+/)
    const version = vMatch ? vMatch[1] : 'unknown'
    log(`${C.dim}discovered PBS release ${tag} / cpython ${version}${C.reset}`)
    return { release: tag, version, urls }
  } catch (err) {
    log(`${C.yellow}!${C.reset} GitHub API unreachable (${err.message}) — using pinned fallback ${FALLBACK_RELEASE}`)
    const urls = {}
    for (const a of arches) urls[a] = fallbackUrl(FALLBACK_RELEASE, FALLBACK_VERSION, platform, a)
    return { release: FALLBACK_RELEASE, version: FALLBACK_VERSION, urls }
  }
}

function flagValue(name) {
  const arg = process.argv.find(a => a.startsWith(`--${name}=`))
  return arg ? arg.split('=')[1] : null
}

function parsePlatformArg() {
  const v = flagValue('platform') ?? (process.platform === 'win32' ? 'win' : 'mac')
  if (!(v in PBS_TARGETS)) {
    log(`${C.red}unknown platform '${v}' — expected mac or win${C.reset}`)
    process.exit(2)
  }
  return v
}

function parseArchArg(platform) {
  const arg = flagValue('arch')
  const valid = Object.keys(PBS_TARGETS[platform])
  if (!arg) return platform === 'mac' ? ['arm64', 'x64'] : valid
  if (!valid.includes(arg)) {
    log(`${C.red}unknown arch '${arg}' for ${platform} — expected ${valid.join(' or ')}${C.reset}`)
    process.exit(2)
  }
  return [arg]
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
async function bundleArch(platform, arch, info) {
  log(`\n${C.bold}${C.cyan}▸ ${platform}/${arch}${C.reset}`)
  await mkdir(CACHE, { recursive: true })
  await mkdir(OUT_ROOT, { recursive: true })

  const url      = info.urls[arch]
  const fileName = path.basename(new URL(url).pathname)
  const archive  = path.join(CACHE, fileName)
  const outDir   = path.join(OUT_ROOT, platform === 'win' ? `python-win-${arch}` : `python-${arch}`)
  const pyBin    = platform === 'win'
    ? path.join(outDir, 'python', 'python.exe')
    : path.join(outDir, 'python', 'bin', 'python3')

  // 1. Download archive (cached)
  if (!existsSync(archive)) {
    log(`  downloading python-${info.version} (${platform}/${arch})…`)
    await download(url, archive)
  } else {
    sub(`archive cached at ${path.relative(ROOT, archive)}`)
  }

  // 2. Extract — clean install
  log('  extracting…')
  if (existsSync(outDir)) await rm(outDir, { recursive: true, force: true })
  await mkdir(outDir, { recursive: true })
  run('tar', ['-xzf', archive, '-C', outDir])

  // Tarball layout: outDir/python/{bin,lib,include,…} (mac/linux) or
  // outDir/python/{python.exe,Lib,Scripts,…} (Windows)
  const inner = path.join(outDir, 'python')
  if (!existsSync(pyBin)) {
    throw new Error(`expected ${pyBin} after extraction — archive layout changed?`)
  }
  sub(`extracted to ${path.relative(ROOT, inner)}`)

  // 3. pip install requirements into the bundled interpreter. This only
  // works when the host OS can execute pyBin natively — i.e. mac bundles
  // must be built on macOS, Windows bundles on Windows.
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
      platform,
      arch,
      built_at: new Date().toISOString(),
    }, null, 2),
  )

  const sz = await dirSize(outDir)
  log(`${C.green}  ✓ ${platform}/${arch} bundle ready · ${(sz / 1e6).toFixed(0)}MB${C.reset}`)
}

async function dirSize(dir) {
  // `du -s` is unavailable on Windows — fall back to a JS-side walk there.
  if (process.platform !== 'win32') {
    const r = spawnSync('du', ['-sk', dir], { encoding: 'utf8' })
    if (r.status === 0) return Number(r.stdout.split(/\s+/)[0]) * 1024
  }
  return jsDirSize(dir)
}

async function jsDirSize(dir) {
  let total = 0
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) total += await jsDirSize(full)
    else if (entry.isFile()) total += (await stat(full)).size
  }
  return total
}

/* ─── main ─────────────────────────────────────────────────────── */
async function main() {
  log(`${C.bold}🐍 Persephone Python bundler${C.reset}`)

  if (!existsSync(REQS)) {
    log(`${C.red}server/requirements.txt not found at ${REQS}${C.reset}`)
    process.exit(2)
  }

  const platform = parsePlatformArg()
  const hostOk = platform === 'win' ? process.platform === 'win32' : process.platform === 'darwin'
  if (!hostOk) {
    log(`${C.red}refusing to bundle '${platform}' python from host platform '${process.platform}'${C.reset}`)
    log(`${C.dim}pip needs to execute the target interpreter, so this must run on a matching ${platform === 'win' ? 'Windows' : 'macOS'} machine.${C.reset}`)
    process.exit(2)
  }

  const arches = parseArchArg(platform)
  const info   = await discoverRelease(platform, arches)
  log(`${C.dim}python-build-standalone ${info.release} · cpython ${info.version}${C.reset}`)

  for (const a of arches) await bundleArch(platform, a, info)

  const pkgCmd = platform === 'win' ? 'npm run exe' : 'npm run dmg'
  log(`\n${C.bold}${C.green}done.${C.reset} run "${C.bold}${pkgCmd}${C.reset}" to package.`)
}

main().catch(err => {
  log(`\n${C.red}✗ ${err.message}${C.reset}`)
  process.exit(1)
})
