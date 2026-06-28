import express from 'express'
import cors from 'cors'
import { spawn, execSync } from 'child_process'
import { createServer } from 'http'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const app = express()
const OLLAMA_BASE = process.env.OLLAMA_HOST || 'http://localhost:11434'

// Resolve python3 path at startup
let PYTHON3 = 'python3'
try {
  PYTHON3 = execSync('which python3').toString().trim()
} catch {}
console.log(`[tts] using python: ${PYTHON3}`)

app.use(cors())
app.use(express.json({ limit: '10mb' }))

// --- Ollama models ---
app.get('/api/models', async (_req, res) => {
  try {
    const r = await fetch(`${OLLAMA_BASE}/api/tags`)
    const data = await r.json()
    res.json(data)
  } catch (err) {
    res.status(500).json({ error: 'Ollama unreachable', details: err.message })
  }
})

// --- Chat stream proxy ---
app.post('/api/chat', async (req, res) => {
  const { model, messages, options } = req.body

  res.setHeader('Content-Type', 'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection', 'keep-alive')

  try {
    const ollamaRes = await fetch(`${OLLAMA_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, messages, options, stream: true }),
    })

    if (!ollamaRes.ok) {
      res.write(`data: ${JSON.stringify({ error: 'Model error' })}\n\n`)
      return res.end()
    }

    const reader = ollamaRes.body.getReader()
    const decoder = new TextDecoder()

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value, { stream: true })
      const lines = chunk.split('\n').filter(Boolean)
      for (const line of lines) {
        res.write(`data: ${line}\n\n`)
      }
    }
    res.write('data: [DONE]\n\n')
    res.end()
  } catch (err) {
    res.write(`data: ${JSON.stringify({ error: err.message })}\n\n`)
    res.end()
  }
})

// --- Generate (non-chat) proxy ---
app.post('/api/generate', async (req, res) => {
  try {
    const r = await fetch(`${OLLAMA_BASE}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    })
    const data = await r.json()
    res.json(data)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// --- TTS endpoint ---
app.post('/api/tts', async (req, res) => {
  const { text, voice = 'tara', speed = 1.0 } = req.body

  if (!text || text.trim().length === 0) {
    return res.status(400).json({ error: 'text is required' })
  }

  const scriptPath = path.join(__dirname, 'tts.py')

  const py = spawn(PYTHON3, [scriptPath, text.trim(), voice, String(speed)], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env },
  })

  const chunks = []
  let stderrBuf = ''

  py.stdout.on('data', chunk => chunks.push(chunk))
  py.stderr.on('data', d => {
    stderrBuf += d.toString()
    console.error('[TTS]', d.toString().trim())
  })

  py.on('close', (code, signal) => {
    if (code !== 0 && code !== null) {
      console.error('[TTS error exit]', code, stderrBuf)
      if (!res.headersSent) {
        res.status(500).json({ error: 'TTS generation failed', details: stderrBuf })
      }
      return
    }
    if (signal) {
      // Process was killed (e.g. client disconnected) — ignore
      return
    }
    const wavBuffer = Buffer.concat(chunks)
    if (wavBuffer.length < 44) {
      res.status(500).json({ error: 'TTS returned empty audio', details: stderrBuf })
      return
    }
    res.setHeader('Content-Type', 'audio/wav')
    res.setHeader('Content-Length', wavBuffer.length)
    res.send(wavBuffer)
  })

  py.on('error', err => {
    console.error('[TTS spawn error]', err)
    if (!res.headersSent) {
      res.status(500).json({ error: 'TTS process error', details: err.message })
    }
  })

  // Only kill on abnormal client disconnect (not normal request completion)
  res.on('close', () => {
    if (!res.writableEnded) py.kill()
  })
})

// --- TTS voices list ---
app.get('/api/tts/voices', (_req, res) => {
  res.json({
    voices: [
      { id: 'tara', name: 'Tara', gender: 'female', description: 'Warm & inviting' },
      { id: 'leo', name: 'Leo', gender: 'male', description: 'Confident & clear' },
      { id: 'leah', name: 'Leah', gender: 'female', description: 'Gentle & soft' },
      { id: 'jess', name: 'Jess', gender: 'female', description: 'Energetic & bright' },
      { id: 'mia', name: 'Mia', gender: 'female', description: 'Smooth & calm' },
      { id: 'zac', name: 'Zac', gender: 'male', description: 'Deep & resonant' },
      { id: 'zoe', name: 'Zoe', gender: 'female', description: 'Crisp & expressive' },
      { id: 'zach', name: 'Zach', gender: 'male', description: 'Warm & conversational' },
    ],
  })
})

// Serve built frontend in production
const distPath = path.join(__dirname, '..', 'dist')
app.use(express.static(distPath))
app.get('*', (_req, res) => {
  res.sendFile(path.join(distPath, 'index.html'))
})

const PORT = process.env.PORT || 3001
createServer(app).listen(PORT, () => {
  console.log(`\n🌸 Persephone server running on http://localhost:${PORT}\n`)
})
