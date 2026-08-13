#!/usr/bin/env node
/**
 * Persephone bootstrapper: sets up a fresh repo (npm + Python venv).
 *
 *   npm run bootstrap -- [flags]
 *
 * Flags:
 *   --skip-npm       skip npm install
 *   --skip-python    skip Python venv setup
 *   --recreate       delete and rebuild .venv (requires re-creating venv)
 *   --help           print this message
 */

import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync, readFileSync, rmSync } from 'node:fs'
import { discoverPython } from './python-path.mjs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const IS_WIN = process.platform === 'win32'

const C = {
  reset: '\x1b[0m',
  dim: '\x1b[2m',
  bold: '\x1b[1m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  red: '\x1b[31m',
  cyan: '\x1b[36m',
}

// Parse flags
const args = process.argv.slice(2)
const skipNpm = args.includes('--skip-npm')
const skipPython = args.includes('--skip-python')
const recreate = args.includes('--recreate')
const showHelp = args.includes('--help') || args.includes('-h')

function log(msg) {
  process.stdout.write(msg + '\n')
}

function step(msg) {
  process.stdout.write(`${C.cyan}[install]${C.reset} ${msg}\n`)
}

function ok(msg) {
  process.stdout.write(`${C.green}✓${C.reset} ${msg}\n`)
}

function warn(msg) {
  process.stdout.write(`${C.yellow}!${C.reset} ${msg}\n`)
}

function fail(msg) {
  process.stdout.write(`${C.red}✗${C.reset} ${msg}\n`)
}

function help() {
  log(`
${C.bold}Persephone Bootstrap${C.reset}

Sets up a fresh Persephone repository: npm dependencies and Python venv.

${C.cyan}Usage:${C.reset}
  npm run bootstrap [options]

${C.cyan}Options:${C.reset}
  --skip-npm       Skip npm install
  --skip-python    Skip Python venv setup
  --recreate       Delete and rebuild .venv (Python venv)
  --help           Show this message

${C.cyan}Example:${C.reset}
  npm run bootstrap                    # Full setup
  npm run bootstrap -- --skip-npm      # Python only
  npm run bootstrap -- --recreate      # Rebuild venv
`)
  process.exit(0)
}

if (showHelp) {
  help()
}

async function main() {
  log('')
  step(`${C.bold}Bootstrapping Persephone${C.reset}`)
  log('')

  // 1. Check Node version
  {
    const nodeVer = process.version
    step(`Node ${nodeVer}`)

    // Try to parse engines.node from package.json
    try {
      const pkg = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8'))
      if (pkg.engines?.node) {
        const range = pkg.engines.node
        // Simple major version check
        const currentMajor = parseInt(nodeVer.slice(1))
        const rangeMatch = range.match(/(\d+)\.(\d+)\.(\d+)/)
        if (rangeMatch) {
          const minMajor = parseInt(rangeMatch[1])
          const maxMatch = range.match(/<\s*(\d+)/)
          if (maxMatch) {
            const maxMajor = parseInt(maxMatch[1])
            if (currentMajor < minMajor || currentMajor >= maxMajor) {
              warn(`Node version ${nodeVer} is outside range ${range}`)
              warn('Electron may misbehave; scripts/ensure-electron.mjs will attempt to work around this')
            }
          }
        }
      }
      ok(`Node ${nodeVer}`)
    } catch (e) {
      ok(`Node ${nodeVer}`)
    }
  }

  log('')

  // 2. npm install
  if (!skipNpm) {
    step('Running npm install…')
    const npmCmd = IS_WIN ? 'npm.cmd' : 'npm'
    const npmResult = spawnSync(npmCmd, ['install'], { stdio: 'inherit', cwd: ROOT })
    if (npmResult.status !== 0) {
      fail('npm install failed')
      process.exit(1)
    }
    ok('npm install complete')
  } else {
    step('Skipping npm install (--skip-npm)')
  }

  log('')

  // 3. Python venv setup
  if (!skipPython) {
    const venvDir = path.join(ROOT, '.venv')
    const venvPython = IS_WIN
      ? path.join(venvDir, 'Scripts', 'python.exe')
      : path.join(venvDir, 'bin', 'python')

    // 3a. Recreate?
    if (recreate) {
      step('Removing existing .venv (--recreate)…')
      try {
        rmSync(venvDir, { recursive: true, force: true })
        ok('.venv removed')
      } catch (e) {
        warn(`.venv remove failed: ${e.message}`)
      }
    }

    // 3b. Check or create venv
    if (existsSync(venvPython)) {
      ok(`.venv already exists at ${venvDir}`)
    } else {
      step('Discovering Python…')
      const discovered = discoverPython()
      if (!discovered) {
        fail('No Python 3.10–3.13 found')
        log(`${C.cyan}[install]${C.reset} Tried: python3.11, python3.12, python3.13, python3.10, python3, python`)
        log(`${C.cyan}[install]${C.reset} Searched: /opt/homebrew/bin, /usr/local/bin, /usr/bin (POSIX)`)
        log(`${C.cyan}[install]${C.reset}           ~/.pyenv/versions (pyenv), /Library/Frameworks/… (macOS)`)
        if (IS_WIN) {
          log(`${C.cyan}[install]${C.reset}           AppData\\Local\\Programs\\Python (Windows), py launcher`)
        }
        log('')
        log(`${C.yellow}!${C.reset} Install Python 3.11 from:`)
        log(`${C.yellow}!${C.reset}   macOS:  brew install python@3.11`)
        log(`${C.yellow}!${C.reset}   Linux:  apt install python3.11 python3.11-venv`)
        log(`${C.yellow}!${C.reset}   Windows: https://www.python.org/downloads/`)
        log(`${C.yellow}!${C.reset} Or set PERSEPHONE_PYTHON=/path/to/python`)
        process.exit(1)
      }

      ok(`Found Python ${discovered.version} at ${discovered.path}`)
      step(`Creating venv at ${venvDir}…`)

      const createResult = spawnSync(discovered.path, ['-m', 'venv', venvDir], { stdio: 'inherit' })
      if (createResult.status !== 0) {
        fail('venv creation failed')
        log(`${C.cyan}[install]${C.reset}`)
        if (!IS_WIN) {
          log(`${C.cyan}[install]${C.reset} On Debian/Ubuntu, run: apt install python3-venv`)
        }
        process.exit(1)
      }
      ok(`venv created at ${venvDir}`)
    }

    // 3c. Upgrade pip
    log('')
    step('Upgrading pip…')
    const pipUpgradeResult = spawnSync(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip'], { stdio: 'inherit' })
    if (pipUpgradeResult.status !== 0) {
      warn('pip upgrade failed (continuing anyway)')
    } else {
      ok('pip upgraded')
    }

    // 3d. Install requirements
    log('')
    const reqFile = path.join(ROOT, 'server', 'requirements.txt')
    if (!existsSync(reqFile)) {
      fail(`${reqFile} not found`)
      process.exit(1)
    }

    step(`Installing requirements from ${reqFile}…`)
    const pipInstallResult = spawnSync(venvPython, ['-m', 'pip', 'install', '-r', reqFile], { stdio: 'inherit' })
    if (pipInstallResult.status !== 0) {
      fail('pip install -r requirements.txt failed')
      process.exit(1)
    }
    ok('requirements installed')

    // 3e. Smoke test
    log('')
    step('Smoke-testing httpx import…')
    const smokeResult = spawnSync(
      venvPython,
      ['-c', 'import httpx, fastapi, uvicorn; print(httpx.__version__)'],
      { encoding: 'utf8', stdio: 'pipe' },
    )
    if (smokeResult.status !== 0) {
      fail('smoke test failed (httpx import error)')
      log(`${C.cyan}[install]${C.reset} stderr: ${smokeResult.stderr}`)
      process.exit(1)
    }
    const httpxVer = smokeResult.stdout.trim()
    ok(`httpx ${httpxVer} (fastapi, uvicorn) ✓`)
  } else {
    step('Skipping Python setup (--skip-python)')
  }

  log('')
  log(`${C.green}✓${C.reset} ${C.bold}Bootstrap complete!${C.reset}`)
  log('')
  log(`Next steps:`)
  log(`  ${C.cyan}npm run dev${C.reset}       Start FastAPI + Vite dev server`)
  log(`  ${C.cyan}npm run setup${C.reset}     Download Kokoro TTS + model assets (first run only)`)
  log('')
}

main().catch(err => {
  fail(`Unexpected error: ${err.message}`)
  process.exit(1)
})
