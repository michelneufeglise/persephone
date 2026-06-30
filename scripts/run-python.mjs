#!/usr/bin/env node
/**
 * Cross-platform `python3` shim for npm scripts.
 * Windows installs typically expose `python`, not `python3`.
 */
import { spawn } from 'node:child_process'

const cmd  = process.platform === 'win32' ? 'python' : 'python3'
const args = process.argv.slice(2)

const proc = spawn(cmd, args, { stdio: 'inherit' })
proc.on('exit', code => process.exit(code ?? 1))
proc.on('error', err => {
  console.error(`[run-python] failed to launch '${cmd}': ${err.message}`)
  process.exit(1)
})
