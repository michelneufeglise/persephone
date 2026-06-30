#!/usr/bin/env node
/**
 * Persephone dev shell for Electron.
 *
 * Boots the FastAPI backend + Vite dev server (re-using scripts/dev.mjs's
 * port-cleaning logic), waits until Vite is ready on :5173, then launches
 * Electron pointed at the dev URL.
 */
import { spawn } from 'node:child_process'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')

const C = {
  reset: '\x1b[0m', dim: '\x1b[2m', bold: '\x1b[1m',
  cyan: '\x1b[36m', magenta: '\x1b[35m', green: '\x1b[32m', red: '\x1b[31m',
}

// Use HTTP through Node's DNS resolver so we accept whichever stack
// (IPv4 127.0.0.1 *or* IPv6 ::1) Vite is actually bound to.
function waitForUrl(url, timeoutMs = 30_000) {
  const started = Date.now()
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(url, { timeout: 1500 }, res => {
        res.resume()
        resolve()
      })
      req.on('error', () => {
        if (Date.now() - started > timeoutMs) {
          reject(new Error(`${url} did not respond within ${timeoutMs}ms`))
        } else {
          setTimeout(tick, 300)
        }
      })
      req.on('timeout', () => req.destroy())
    }
    tick()
  })
}

async function main() {
  console.log(`${C.bold}⚘ Persephone (electron dev)${C.reset}\n`)

  // Defensive self-heal: on some Windows machines electron's own postinstall
  // silently fails to extract its binary (see scripts/ensure-electron.mjs).
  // Cheap no-op if it's already installed correctly.
  await import('./ensure-electron.mjs')

  // Reuse the existing dev orchestrator — it handles port cleanup + spawning
  const dev = spawn(process.execPath, [path.join(ROOT, 'scripts', 'dev.mjs')], {
    stdio: 'inherit',
    env:   { ...process.env, FORCE_COLOR: '1' },
  })

  console.log(`${C.dim}waiting for Vite on :5173…${C.reset}`)
  try {
    await waitForUrl('http://localhost:5173/')
  } catch (err) {
    console.error(`${C.red}vite did not come up:${C.reset} ${err.message}`)
    dev.kill('SIGTERM')
    process.exit(1)
  }

  console.log(`${C.green}✓ Vite ready — launching Electron${C.reset}`)
  const electronPath = (await import('electron')).default
  const proc = spawn(electronPath, ['.'], {
    cwd: ROOT,
    stdio: 'inherit',
    env: { ...process.env, ELECTRON_ENABLE_LOGGING: '1' },
  })

  proc.on('exit', () => dev.kill('SIGTERM'))

  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      try { proc.kill(sig) } catch {}
      try { dev.kill(sig)  } catch {}
      setTimeout(() => process.exit(0), 200)
    })
  }
}

main().catch(err => {
  console.error(err)
  process.exit(1)
})
