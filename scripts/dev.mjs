#!/usr/bin/env node
/**
 * Persephone dev orchestrator.
 *
 * - Probes the FastAPI (8000) and Vite (5173) ports for availability.
 * - If a port is in use, locates the owning PID(s) and SIGTERMs them.
 * - Then launches both services concurrently with tagged, coloured output.
 * - Forwards SIGINT/SIGTERM to children so Ctrl+C cleanly shuts everything down.
 *
 * Cross-platform: uses Node's `net` for the probe; `lsof` on Unix, `netstat` on Windows.
 */

import { spawn, execSync, spawnSync } from 'node:child_process'
import net from 'node:net'

const API_PORT  = 8000
const VITE_PORT = 5173
const C = {
  reset: '\x1b[0m', dim: '\x1b[2m', bold: '\x1b[1m',
  cyan: '\x1b[36m', magenta: '\x1b[35m',
  green: '\x1b[32m', yellow: '\x1b[33m', red: '\x1b[31m',
}

function log(msg) {
  process.stdout.write(msg + '\n')
}

function isPortFree(port) {
  return new Promise(resolve => {
    const srv = net.createServer()
    srv.once('error', () => resolve(false))
    srv.once('listening', () => srv.close(() => resolve(true)))
    srv.listen(port, '127.0.0.1')
  })
}

function findPidsOnPort(port) {
  try {
    if (process.platform === 'win32') {
      const out = execSync(`netstat -ano | findstr :${port}`, { encoding: 'utf8' })
      const pids = new Set()
      for (const line of out.split(/\r?\n/)) {
        const m = line.match(/LISTENING\s+(\d+)/)
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

function killPid(pid) {
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/F', '/PID', String(pid)], { stdio: 'ignore' })
    } else {
      process.kill(Number(pid), 'SIGTERM')
    }
    return true
  } catch {
    return false
  }
}

async function ensurePortFree(port, label) {
  if (await isPortFree(port)) {
    log(`${C.green}✓${C.reset} port ${C.bold}${port}${C.reset} (${label}) is free`)
    return
  }

  const pids = findPidsOnPort(port)
  if (pids.length === 0) {
    log(`${C.yellow}!${C.reset} port ${port} (${label}) is occupied but no PID found (other host?)`)
    return
  }

  log(`${C.yellow}!${C.reset} port ${C.bold}${port}${C.reset} (${label}) busy — killing PID${pids.length > 1 ? 's' : ''}: ${pids.join(', ')}`)
  pids.forEach(killPid)

  // Wait briefly for OS to release the port
  for (let i = 0; i < 25; i++) {
    await new Promise(r => setTimeout(r, 120))
    if (await isPortFree(port)) {
      log(`${C.green}✓${C.reset} port ${C.bold}${port}${C.reset} (${label}) freed`)
      return
    }
  }

  log(`${C.red}✗${C.reset} could not free port ${port}`)
  process.exit(1)
}

function tagWriter(tag, color) {
  return chunk => {
    const lines = chunk.toString().replace(/\r/g, '').split('\n')
    for (const line of lines) {
      if (line === '') continue
      process.stdout.write(`${color}[${tag}]${C.reset} ${line}\n`)
    }
  }
}

async function main() {
  log('')
  log(`${C.bold}🌸 Persephone${C.reset} ${C.dim}— dev startup${C.reset}\n`)

  await ensurePortFree(API_PORT,  'FastAPI')
  await ensurePortFree(VITE_PORT, 'Vite')

  log(`\n${C.dim}Starting services…${C.reset}\n`)

  const pyCmd = process.platform === 'win32' ? 'python' : 'python3'
  const api = spawn(pyCmd, ['server/main.py'], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  })

  // Use `npx vite` so we don't require a global install. On Windows, npx is a .cmd
  const vite = spawn(
    process.platform === 'win32' ? 'npx.cmd' : 'npx',
    ['vite'],
    { stdio: ['ignore', 'pipe', 'pipe'] },
  )

  api.stdout.on('data', tagWriter('api',  C.cyan))
  api.stderr.on('data', tagWriter('api',  C.cyan))
  vite.stdout.on('data', tagWriter('vite', C.magenta))
  vite.stderr.on('data', tagWriter('vite', C.magenta))

  let shuttingDown = false
  function shutdown(signal) {
    if (shuttingDown) return
    shuttingDown = true
    log(`\n${C.dim}→ Received ${signal}. Stopping services…${C.reset}`)
    try { api.kill('SIGTERM') } catch {}
    try { vite.kill('SIGTERM') } catch {}
    setTimeout(() => process.exit(0), 800)
  }
  process.on('SIGINT',  () => shutdown('SIGINT'))
  process.on('SIGTERM', () => shutdown('SIGTERM'))

  api.on('exit', code => {
    if (shuttingDown) return
    log(`${C.red}[api]${C.reset} exited with code ${code}`)
    try { vite.kill('SIGTERM') } catch {}
    process.exit(code ?? 1)
  })
  vite.on('exit', code => {
    if (shuttingDown) return
    log(`${C.red}[vite]${C.reset} exited with code ${code}`)
    try { api.kill('SIGTERM') } catch {}
    process.exit(code ?? 1)
  })
}

main().catch(err => {
  console.error(err)
  process.exit(1)
})
