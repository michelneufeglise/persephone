/**
 * Persephone — Electron main process
 *
 *  ┌──────────┐ spawn  ┌────────────────────┐
 *  │ Electron │ ─────▶ │  bundled Python    │
 *  │  main    │        │  uvicorn :PORT     │
 *  └──────────┘        └────────────────────┘
 *       │                       │
 *       │       loadURL         │
 *       └──── http://127.0.0.1 ◀┘
 *
 * Production builds ship a portable Python (resources/python/) prebuilt by
 * scripts/bundle-python.mjs. Dev builds use the system python3.
 */

'use strict'

const { app, BrowserWindow, Menu, shell, dialog, nativeImage } = require('electron')
const { spawn }    = require('node:child_process')
const path         = require('node:path')
const fs           = require('node:fs')
const http         = require('node:http')
const net          = require('node:net')

const isDev    = !app.isPackaged
const APP_NAME = 'Persephone'
const HOST     = '127.0.0.1'
const ICON_PATH = path.join(__dirname, '..', 'build', 'icon.png')

let mainWindow      = null
let pythonProc      = null
let chosenPort      = 0
let pythonStartedAt = 0
/** Ring buffer of recent Python stderr lines for surfacing in the failure dialog. */
const pyLogTail     = []
const PY_LOG_TAIL_MAX = 80
let pyLogPath       = null   // set in startPython

/* ───────────────────────────────────────────────────────────────── */
/* Single instance lock                                              */
/* ───────────────────────────────────────────────────────────────── */
if (!app.requestSingleInstanceLock()) {
  app.quit()
  process.exit(0)
}
app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
  }
})

/* ───────────────────────────────────────────────────────────────── */
/* Port + Python lifecycle                                           */
/* ───────────────────────────────────────────────────────────────── */
function findFreePort(start = 8765) {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', () => {
      // Try the next port
      findFreePort(start + 1).then(resolve, reject)
    })
    srv.listen(start, HOST, () => {
      const port = srv.address().port
      srv.close(() => resolve(port))
    })
  })
}

function resourcesPath() {
  return isDev
    ? path.join(__dirname, '..')
    : process.resourcesPath
}

function bundledPythonPath() {
  // resources/python/bin/python3 inside the .app bundle
  return path.join(resourcesPath(), 'python', 'bin', 'python3')
}

function resolvePythonCmd() {
  if (isDev) return { cmd: 'python3', args: [] }
  const bundled = bundledPythonPath()
  if (fs.existsSync(bundled)) return { cmd: bundled, args: [] }
  // Fallback to system python so we can still ship if the bundler skipped
  return { cmd: 'python3', args: [] }
}

function serverScriptPath() {
  return isDev
    ? path.join(__dirname, '..', 'server', 'main.py')
    : path.join(resourcesPath(), 'server', 'main.py')
}

function userDataPath() {
  return app.getPath('userData')
}

function startPython(port) {
  const { cmd, args } = resolvePythonCmd()
  const script = serverScriptPath()

  if (!fs.existsSync(script)) {
    throw new Error(`server entry not found: ${script}`)
  }

  const env = {
    ...process.env,
    PORT:                    String(port),
    PERSEPHONE_DATA_DIR:     userDataPath(),
    PERSEPHONE_PROD:         isDev ? '' : '1',
    PYTHONUNBUFFERED:        '1',
    PYTHONDONTWRITEBYTECODE: '1',
    // Point huggingface/torch caches into the per-user data dir so they
    // survive app deletes? Keep default for now; revisit if disk pressure.
  }

  // When using the bundled python, set up its dyld path so torch + snac
  // can find their bundled .dylibs.
  if (cmd.startsWith(path.join(resourcesPath(), 'python'))) {
    const libDir = path.join(resourcesPath(), 'python', 'lib')
    env.DYLD_LIBRARY_PATH = `${libDir}:${env.DYLD_LIBRARY_PATH ?? ''}`
  }

  // Persist a rolling log file the user can find via Help → Open data directory
  pyLogPath = path.join(userDataPath(), 'persephone-backend.log')
  let logFd
  try { logFd = fs.openSync(pyLogPath, 'w') } catch (e) { logFd = null }

  pythonStartedAt = Date.now()
  const proc = spawn(cmd, [...args, script], {
    cwd:   path.dirname(script),
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  const captureLine = (stream, prefix) => (data) => {
    const text = data.toString()
    if (logFd != null) {
      try { fs.writeSync(logFd, text) } catch {}
    }
    stream.write(`[py] ${text}`)
    // Split incoming buffer into lines and push into the tail.
    for (const ln of text.split('\n')) {
      if (!ln) continue
      pyLogTail.push(`${prefix}${ln}`)
      if (pyLogTail.length > PY_LOG_TAIL_MAX) pyLogTail.shift()
    }
  }
  proc.stdout.on('data', captureLine(process.stdout, ''))
  proc.stderr.on('data', captureLine(process.stderr, ''))

  proc.on('exit', (code, signal) => {
    console.log(`[py] exited code=${code} signal=${signal}`)
    pythonProc = null
    if (logFd != null) { try { fs.closeSync(logFd) } catch {} }
    if (!mainWindow || mainWindow.isDestroyed()) {
      showStartupFailure(code, signal)
      app.quit()
    }
  })

  return proc
}

function showStartupFailure(code, signal) {
  const tail = pyLogTail.length
    ? pyLogTail.slice(-30).join('\n')
    : '(no Python output captured — interpreter never started)'
  const logHint = pyLogPath ? `\n\nFull log: ${pyLogPath}` : ''
  dialog.showErrorBox(
    'Persephone backend failed to start',
    `The local server exited unexpectedly (code ${code}, signal ${signal}).\n\n` +
    `─── last Python output ───\n${tail}${logHint}`,
  )
}

function waitForServer(port, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    const started = Date.now()
    const tick = () => {
      const req = http.get({ host: HOST, port, path: '/api/models', timeout: 1500 }, res => {
        // Any 2xx/4xx/5xx means the server is alive enough to take requests
        res.resume()
        resolve()
      })
      req.on('error', () => {
        if (Date.now() - started > timeoutMs) {
          reject(new Error(`server did not become ready on :${port} within ${timeoutMs}ms`))
        } else {
          setTimeout(tick, 350)
        }
      })
      req.on('timeout', () => req.destroy())
    }
    tick()
  })
}

/* ───────────────────────────────────────────────────────────────── */
/* Window                                                            */
/* ───────────────────────────────────────────────────────────────── */
async function createWindow() {
  const icon = fs.existsSync(ICON_PATH) ? nativeImage.createFromPath(ICON_PATH) : undefined

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 920,
    minHeight: 620,
    title: APP_NAME,
    icon,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 14, y: 14 },
    backgroundColor: '#06040c', // matches Underworld theme bg
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  })

  mainWindow.once('ready-to-show', () => mainWindow.show())

  // Open external links in the user's default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  // Dev: load Vite directly so HMR works. Prod: load the FastAPI-served
  // dist via the Python backend's static mount.
  if (isDev) {
    await mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    await mainWindow.loadURL(`http://${HOST}:${chosenPort}`)
  }
}

/* ───────────────────────────────────────────────────────────────── */
/* App lifecycle                                                     */
/* ───────────────────────────────────────────────────────────────── */
app.whenReady().then(async () => {
  // Pretty app name in macOS menu bar etc.
  if (process.platform === 'darwin') app.setName(APP_NAME)
  setupMenu()

  try {
    if (isDev) {
      // dev: scripts/electron-dev.mjs already runs vite + FastAPI via dev.mjs;
      // don't spawn a duplicate Python here.
      await createWindow()
    } else {
      chosenPort = await findFreePort(8765)
      pythonProc = startPython(chosenPort)
      await waitForServer(chosenPort, 60_000)
      console.log(`[main] backend ready on :${chosenPort} in ${Date.now() - pythonStartedAt}ms`)
      await createWindow()
    }
  } catch (err) {
    console.error('[main] startup failed:', err)
    const tail = pyLogTail.length
      ? `\n\n─── last Python output ───\n${pyLogTail.slice(-30).join('\n')}`
      : ''
    const logHint = pyLogPath ? `\n\nFull log: ${pyLogPath}` : ''
    dialog.showErrorBox(
      'Persephone could not start',
      String(err?.message ?? err) +
      `\n\nIf this is your first launch, the bundled Python may need a moment to extract.${tail}${logHint}`,
    )
    app.quit()
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', async () => {
  if (BrowserWindow.getAllWindows().length === 0) await createWindow()
})

app.on('before-quit', () => {
  if (pythonProc && !pythonProc.killed) {
    try { pythonProc.kill('SIGTERM') } catch {}
    setTimeout(() => {
      if (pythonProc && !pythonProc.killed) try { pythonProc.kill('SIGKILL') } catch {}
    }, 1500)
  }
})

/* ───────────────────────────────────────────────────────────────── */
/* macOS menu                                                        */
/* ───────────────────────────────────────────────────────────────── */
function setupMenu() {
  if (process.platform !== 'darwin') {
    Menu.setApplicationMenu(null)
    return
  }
  const template = [
    {
      label: APP_NAME,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' }, { role: 'redo' }, { type: 'separator' },
        { role: 'cut' }, { role: 'copy' }, { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' }, { role: 'forceReload' },
        { type: 'separator' },
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
        ...(isDev ? [{ role: 'toggleDevTools' }] : []),
      ],
    },
    {
      label: 'Window',
      submenu: [{ role: 'minimize' }, { role: 'close' }, { role: 'front' }],
    },
    {
      role: 'help',
      submenu: [{
        label: 'Open data directory',
        click: () => shell.openPath(userDataPath()),
      }],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}
