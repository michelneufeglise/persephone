/**
 * Persephone preload (currently minimal).
 *
 * The renderer talks to the bundled FastAPI server directly over HTTP — no
 * IPC bridge is needed. This preload exists so the BrowserWindow can keep
 * contextIsolation + sandbox on, and to expose a tiny `persephone` global
 * the renderer can use to detect that it's running inside Electron.
 */

'use strict'

const { contextBridge } = require('electron')

contextBridge.exposeInMainWorld('persephone', {
  isElectron: true,
  platform:   process.platform,
})
