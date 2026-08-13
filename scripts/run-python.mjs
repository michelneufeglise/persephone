#!/usr/bin/env node
/**
 * Cross-platform Python shim for npm scripts.
 * Resolves interpreter via resolvePython() to use venv if available.
 */
import { spawn } from 'node:child_process'
import { resolvePython } from './python-path.mjs'

const cmd  = resolvePython()
const args = process.argv.slice(2)

const proc = spawn(cmd, args, { stdio: 'inherit' })
proc.on('exit', code => process.exit(code ?? 1))
proc.on('error', err => {
  console.error(`[run-python] failed to launch '${cmd}': ${err.message}`)
  console.error(`[run-python] run 'npm run bootstrap' to set up the Python venv`)
  process.exit(1)
})
