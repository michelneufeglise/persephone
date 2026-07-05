#!/usr/bin/env node
/**
 * Persephone control CLI.
 *
 *   persephone start [--electron|-e] [--api|--vite]
 *   persephone stop
 *   persephone restart [--electron|-e]
 *   persephone status
 *   persephone dmg
 *   persephone exe
 *   persephone help
 *
 * Cross-platform (macOS + Linux + Windows). Cleans FastAPI, Vite, Electron,
 * and any lingering MCP subprocesses that FastAPI may have orphaned.
 */

import { spawn, spawnSync, execSync } from 'node:child_process'
import net from 'node:net'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync } from 'node:fs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT      = path.resolve(__dirname, '..')

const IS_WIN = process.platform === 'win32'

// ── Config ────────────────────────────────────────────────────────────────────
const PORTS = {
  fastapi: 8000,
  vite:    5173,
}

// Process-name patterns to match lingering children (case-insensitive substring).
// Applied via `ps`/`tasklist`. Order = kill order.
const MATCH_PATTERNS = [
  { name: 'electron',      needle: 'Persephone.app/Contents/MacOS/Persephone', mac: true  },
  { name: 'electron-dev',  needle: 'node_modules/electron/dist/Electron',      mac: true  },
  { name: 'electron-win',  needle: 'electron.exe',                             win: true  },
  { name: 'vite',          needle: 'node_modules/vite/bin/vite.js' },
  { name: 'fastapi',       needle: 'server/main.py' },
  { name: 'mcp-git',       needle: 'server/mcp_persephone_git.py' },
  { name: 'mcp-uvx',       needle: 'uvx mcp-server-' },
  { name: 'mcp-npx-fs',    needle: 'server-filesystem' },
]

// ── ANSI colour palette ───────────────────────────────────────────────────────
const supportsColor = process.stdout.isTTY && !process.env.NO_COLOR
const C = supportsColor
  ? {
      reset: '\x1b[0m', dim: '\x1b[2m', bold: '\x1b[1m', italic: '\x1b[3m',
      red: '\x1b[38;5;204m', pink: '\x1b[38;5;213m',
      orange: '\x1b[38;5;215m', yellow: '\x1b[38;5;222m',
      green: '\x1b[38;5;120m', cyan: '\x1b[38;5;117m',
      violet: '\x1b[38;5;141m', grey: '\x1b[38;5;244m',
    }
  : Object.fromEntries('reset dim bold italic red pink orange yellow green cyan violet grey'.split(' ').map(k => [k, '']))

// ── Utility ────────────────────────────────────────────────────────────────────
const write = (s) => process.stdout.write(s)

const symbol = {
  ok:   `${C.green}✓${C.reset}`,
  bad:  `${C.red}✗${C.reset}`,
  wait: `${C.violet}⋯${C.reset}`,
  arr:  `${C.cyan}❯${C.reset}`,
  info: `${C.orange}◆${C.reset}`,
  warn: `${C.yellow}!${C.reset}`,
}

function banner(title, sub) {
  const bar = `${C.violet}${'─'.repeat(56)}${C.reset}`
  write(`\n${bar}\n`)
  write(`  ${C.bold}${C.pink}⚘  Persephone${C.reset}  ${C.dim}${title}${C.reset}\n`)
  if (sub) write(`  ${C.grey}${sub}${C.reset}\n`)
  write(`${bar}\n\n`)
}

function step(msg) { write(`  ${symbol.wait} ${msg}…`) }
function done(msg) { write(`\r  ${symbol.ok} ${msg}       \n`) }
function fail(msg) { write(`\r  ${symbol.bad} ${msg}       \n`) }
function line(sym, label, value) {
  write(`  ${sym}  ${label.padEnd(14)} ${C.grey}${value}${C.reset}\n`)
}

// ── Port + process helpers ────────────────────────────────────────────────────
function portFree(port) {
  return new Promise(resolve => {
    const srv = net.createServer()
    srv.once('error', () => resolve(false))
    srv.once('listening', () => srv.close(() => resolve(true)))
    srv.listen(port, '127.0.0.1')
  })
}

function pidsOnPort(port) {
  try {
    if (IS_WIN) {
      const out = execSync(`netstat -ano | findstr :${port}`, { encoding: 'utf8' })
      const pids = new Set()
      for (const l of out.split(/\r?\n/)) {
        const m = l.match(/LISTENING\s+(\d+)/)
        if (m) pids.add(m[1])
      }
      return [...pids]
    }
    const out = execSync(`lsof -ti tcp:${port} -sTCP:LISTEN`, { encoding: 'utf8' })
    return out.trim().split('\n').filter(Boolean)
  } catch {
    return []
  }
}

function pidsByPattern(needle) {
  try {
    if (IS_WIN) {
      const out = execSync(
        `wmic process where "CommandLine like '%${needle.replace(/'/g, "''")}%'" get ProcessId /value`,
        { encoding: 'utf8' },
      )
      return [...out.matchAll(/ProcessId=(\d+)/g)].map(m => m[1])
    }
    const out = execSync(`ps -Ao pid=,command= | grep -F ${JSON.stringify(needle)} | grep -v grep`, { encoding: 'utf8' })
    const self = String(process.pid)
    return out.trim().split('\n').map(l => l.trim().split(/\s+/)[0]).filter(pid => pid && pid !== self)
  } catch {
    return []
  }
}

function killPid(pid, signal = 'SIGTERM') {
  try {
    if (IS_WIN) {
      spawnSync('taskkill', ['/F', '/T', '/PID', String(pid)], { stdio: 'ignore' })
    } else {
      process.kill(Number(pid), signal)
    }
    return true
  } catch {
    return false
  }
}

async function waitPortFree(port, timeoutMs = 4000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    if (await portFree(port)) return true
    await sleep(150)
  }
  return false
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

// ── Actions ────────────────────────────────────────────────────────────────────
async function stopAll({ quiet = false } = {}) {
  if (!quiet) banner('stop', 'terminate every Persephone process')

  let killed = 0
  const record = new Set()  // pids we've already touched

  // 1. Port-bound listeners first (FastAPI, Vite)
  for (const [label, port] of Object.entries(PORTS)) {
    const pids = pidsOnPort(port)
    if (!pids.length) {
      line(symbol.ok, `port :${port}`, `${label} already free`)
      continue
    }
    for (const pid of pids) {
      if (record.has(pid)) continue
      record.add(pid)
      const ok = killPid(pid, 'SIGTERM')
      if (ok) killed++
      line(ok ? symbol.ok : symbol.warn, `port :${port}`, `${label} pid ${pid} → ${ok ? 'SIGTERM' : 'skip'}`)
    }
  }

  // 2. Named children (Electron, MCP subprocesses, orphaned FastAPI, Vite bound to IPv6)
  for (const pat of MATCH_PATTERNS) {
    if (pat.mac && IS_WIN) continue
    if (pat.win && !IS_WIN) continue
    const pids = pidsByPattern(pat.needle)
    for (const pid of pids) {
      if (record.has(pid)) continue
      record.add(pid)
      const ok = killPid(pid, 'SIGTERM')
      if (ok) killed++
      line(ok ? symbol.ok : symbol.warn, pat.name, `pid ${pid} → ${ok ? 'SIGTERM' : 'skip'}`)
    }
  }

  // 3. Give SIGTERM 800ms, then SIGKILL anything still on the ports
  await sleep(800)
  for (const [label, port] of Object.entries(PORTS)) {
    const stubborn = pidsOnPort(port)
    for (const pid of stubborn) {
      killPid(pid, 'SIGKILL')
      killed++
      line(symbol.warn, `port :${port}`, `${label} pid ${pid} → SIGKILL`)
    }
  }

  // 4. Final report + port confirmation
  const stateLines = []
  for (const [label, port] of Object.entries(PORTS)) {
    const free = await portFree(port)
    stateLines.push(`${label}:${port} ${free ? `${C.green}free${C.reset}` : `${C.red}busy${C.reset}`}`)
  }
  if (!quiet) {
    write(`\n  ${symbol.arr} ${killed} process${killed === 1 ? '' : 'es'} terminated\n`)
    write(`  ${symbol.arr} ${stateLines.join('   ')}\n\n`)
  }
  return killed
}

async function startAll({ withElectron, apiOnly, viteOnly }) {
  banner(
    withElectron ? 'start · electron' : 'start · dev',
    apiOnly  ? 'FastAPI only'
    : viteOnly ? 'Vite only'
    : withElectron ? 'FastAPI + Vite + Electron shell'
    : 'FastAPI + Vite',
  )

  // Ensure ports are clear before we spawn anything.
  step('checking ports')
  const before = { fastapi: pidsOnPort(PORTS.fastapi), vite: pidsOnPort(PORTS.vite) }
  const anyBusy = before.fastapi.length || before.vite.length
  if (anyBusy) {
    fail('checking ports (busy — running stop first)')
    await stopAll({ quiet: true })
  } else {
    done('checking ports')
  }

  // Pick the orchestrator to launch
  const script = withElectron
    ? path.join(ROOT, 'scripts', 'electron-dev.mjs')
    : path.join(ROOT, 'scripts', 'dev.mjs')

  if (!existsSync(script)) {
    fail(`cannot find ${script}`)
    process.exit(1)
  }

  // For --api / --vite only, we drop into a bespoke sub-launch.
  if (apiOnly)  return launchApiOnly()
  if (viteOnly) return launchViteOnly()

  line(symbol.info, 'launching',   path.relative(ROOT, script))
  line(symbol.info, 'fastapi url', `http://127.0.0.1:${PORTS.fastapi}`)
  line(symbol.info, 'vite url',    `http://localhost:${PORTS.vite}`)
  if (withElectron) line(symbol.info, 'electron', 'auto-opens once vite is ready')
  write('\n')

  const child = spawn(process.execPath, [script], {
    cwd:   ROOT,
    stdio: 'inherit',
    env:   process.env,
  })
  proxySignals(child)
  child.on('exit', code => process.exit(code ?? 0))
}

function launchApiOnly() {
  line(symbol.info, 'launching', 'FastAPI only (scripts/run-python.mjs server/main.py)')
  write('\n')
  const child = spawn(process.execPath, [path.join(ROOT, 'scripts', 'run-python.mjs'), path.join(ROOT, 'server', 'main.py')], {
    cwd: ROOT, stdio: 'inherit', env: process.env,
  })
  proxySignals(child)
  child.on('exit', code => process.exit(code ?? 0))
}

function launchViteOnly() {
  line(symbol.info, 'launching', 'Vite only (npx vite)')
  write('\n')
  const child = spawn(IS_WIN ? 'npx.cmd' : 'npx', ['vite'], {
    cwd: ROOT, stdio: 'inherit', env: process.env,
  })
  proxySignals(child)
  child.on('exit', code => process.exit(code ?? 0))
}

async function restartAll(opts) {
  banner('restart', opts.withElectron ? 'stop → start with Electron' : 'stop → start')
  await stopAll({ quiet: true })
  write(`  ${symbol.ok} everything down\n\n`)
  await sleep(300)
  await startAll(opts)
}

async function statusAll() {
  banner('status', 'runtime snapshot')
  for (const [label, port] of Object.entries(PORTS)) {
    const pids = pidsOnPort(port)
    const sym  = pids.length ? symbol.ok : symbol.info
    const val  = pids.length ? `${C.green}up${C.reset}   pid ${pids.join(', ')}` : `${C.grey}idle${C.reset}`
    line(sym, `${label}:${port}`, val)
  }
  for (const pat of MATCH_PATTERNS) {
    if (pat.mac && IS_WIN) continue
    if (pat.win && !IS_WIN) continue
    const pids = pidsByPattern(pat.needle)
    if (!pids.length) continue
    line(symbol.ok, pat.name, `pid ${pids.join(', ')}`)
  }

  // FastAPI liveness probe
  await new Promise(res => {
    const req = http.get(`http://127.0.0.1:${PORTS.fastapi}/api/models`, { timeout: 1000 }, r => {
      r.resume()
      line(symbol.ok, 'fastapi probe', `HTTP ${r.statusCode}`)
      res()
    })
    req.on('error', () => { line(symbol.info, 'fastapi probe', 'no response'); res() })
    req.on('timeout', () => { req.destroy(); line(symbol.info, 'fastapi probe', 'timeout'); res() })
  })
  write('\n')
}

async function buildDmg() {
  banner('build · dmg', 'macOS installer for distribution')
  await stopAll({ quiet: true })
  write(`  ${symbol.ok} services stopped\n\n`)
  runNpm(['run', 'dmg'])
}

async function buildExe() {
  banner('build · exe', 'Windows installer for distribution')
  await stopAll({ quiet: true })
  write(`  ${symbol.ok} services stopped\n\n`)
  runNpm(['run', 'exe'])
}

function runNpm(args) {
  const child = spawn(IS_WIN ? 'npm.cmd' : 'npm', args, {
    cwd: ROOT, stdio: 'inherit', env: process.env,
  })
  proxySignals(child)
  child.on('exit', code => process.exit(code ?? 0))
}

// ── Signal forwarding ─────────────────────────────────────────────────────────
function proxySignals(child) {
  const forward = (sig) => () => { try { child.kill(sig) } catch {} }
  process.on('SIGINT',  forward('SIGINT'))
  process.on('SIGTERM', forward('SIGTERM'))
  process.on('SIGHUP',  forward('SIGHUP'))
}

// ── Help ──────────────────────────────────────────────────────────────────────
function help() {
  banner('help', 'quick-reference')
  const cmd = (name, desc) => write(`  ${C.pink}${name.padEnd(28)}${C.reset} ${C.grey}${desc}${C.reset}\n`)
  cmd('persephone start',                    'FastAPI + Vite (dev)')
  cmd('persephone start --electron',         'FastAPI + Vite + Electron shell')
  cmd('persephone start --api',              'FastAPI only')
  cmd('persephone start --vite',             'Vite only')
  cmd('persephone stop',                     'kill FastAPI, Vite, Electron + orphan MCPs')
  cmd('persephone restart [--electron]',     'stop → start')
  cmd('persephone status',                   'snapshot of ports + processes')
  cmd('persephone dmg',                      'stop everything → build macOS .dmg')
  cmd('persephone exe',                      'stop everything → build Windows .exe')
  cmd('persephone help',                     'this screen')
  write('\n')
  write(`  ${C.dim}Same commands are available as npm scripts: ${C.reset}`)
  write(`${C.cyan}npm start${C.reset} · ${C.cyan}npm stop${C.reset} · ${C.cyan}npm restart${C.reset} · ${C.cyan}npm run status${C.reset}\n\n`)
}

// ── Entry ─────────────────────────────────────────────────────────────────────
const args = process.argv.slice(2)
const cmd  = args.shift() ?? 'help'
const has  = (f, s) => args.includes(f) || (s && args.includes(s))

const opts = {
  withElectron: has('--electron', '-e'),
  apiOnly:      has('--api'),
  viteOnly:     has('--vite'),
}

async function main() {
  try {
    switch (cmd) {
      case 'start':   await startAll(opts);   break
      case 'stop':    await stopAll();        break
      case 'restart': await restartAll(opts); break
      case 'status':  await statusAll();      break
      case 'dmg':     await buildDmg();       break
      case 'exe':     await buildExe();       break
      case 'help':
      case '--help':
      case '-h':
        help()
        break
      default:
        write(`\n  ${symbol.bad} unknown command: ${C.red}${cmd}${C.reset}\n`)
        help()
        process.exit(1)
    }
  } catch (exc) {
    write(`\n  ${symbol.bad} ${C.red}${exc?.message ?? exc}${C.reset}\n\n`)
    process.exit(1)
  }
}

main()
