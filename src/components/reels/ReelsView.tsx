import { Component, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Clapperboard, Sparkles, Loader2, Palette, Clock, MicVocal,
  Wand2, ImageIcon, Film, X, Play, Square, AlertTriangle,
  Upload, Music, Volume2, Download, Captions, Languages,
} from 'lucide-react'
import { Panel } from '@/components/ui/Panel'

// ── Types ──────────────────────────────────────────────────────────────────────
interface Voice {
  id: string
  name: string
  gender: string
  accent?: string
  description: string
}

interface SceneEffects {
  brightness?: number   // -1 .. 1
  contrast?:   number   //  0 .. 3
  saturation?: number   //  0 .. 3
  speed?:      number   // 0.5 .. 2 (video only)
  grayscale?:  boolean
}

interface Scene {
  n: number
  script: string              // spoken line (also becomes the burned-in caption)
  imagePrompt: string         // SD prompt for the still
  seconds: number
  overrideImagePath?: string  // absolute path (server-side) if user attached an image
  overrideImageUrl?: string   // URL for previewing the override in the UI
  overrideVideoPath?: string  // absolute path if user attached a video clip
  overrideVideoUrl?: string   // URL for previewing the video override
  overrideVideoStart?: number // seconds — start offset into the source clip
  // Master-audio cascade — filled in at render time so image-override scenes
  // can still play the master video's audio.
  masterAudioPath?:  string
  masterAudioStart?: number
  effects?: SceneEffects
}

interface MusicAsset {
  name:  string
  path:  string
  url:   string
  bytes: number
}

interface MasterVideo {
  name:      string
  path:      string
  url:       string
  bytes:     number
  converted: boolean   // did the server re-encode the source to MP4?
}

interface Plan {
  topic:    string
  tone:     Tone
  aspect:   Aspect
  voice:    string
  duration: number
  scenes:   Scene[]
}

interface ComfyStatus {
  running:  boolean
  version?: string
  model?:   string
  error?:   string
}

type Tone   = 'informative' | 'energetic' | 'calm' | 'dramatic' | 'luxury'
type Aspect = '9:16' | '1:1' | '16:9'
type Tab    = 'new' | 'history'

const TONES: { id: Tone; label: string; hint: string }[] = [
  { id: 'informative', label: 'Informative', hint: 'Clear, factual, listicle-ready' },
  { id: 'energetic',   label: 'Energetic',   hint: 'Punchy, hook-first, high-tempo' },
  { id: 'calm',        label: 'Calm',        hint: 'Slow narration, meditative pacing' },
  { id: 'dramatic',    label: 'Dramatic',    hint: 'Cinematic, tension-building' },
  { id: 'luxury',      label: 'Luxury',      hint: 'Editorial, refined, brand-tier' },
]

const DURATIONS = [15, 30, 60] as const
const ASPECTS: { id: Aspect; label: string; platform: string }[] = [
  { id: '9:16', label: '9:16', platform: 'TikTok · Reels · Shorts' },
  { id: '1:1',  label: '1:1',  platform: 'Feed post'              },
  { id: '16:9', label: '16:9', platform: 'YouTube'                },
]

interface RenderProgress {
  stage: 'idle' | 'image' | 'voice' | 'render' | 'concat' | 'music' | 'done' | 'error'
  scene?: number
  total?: number
  error?: string
}

interface Reel {
  id:        string
  topic:     string
  aspect:    Aspect
  voice:     string
  duration:  number
  createdAt: number
  bytes:     number
  url:       string
}

// ── Error boundary ────────────────────────────────────────────────────────────
// Without this, a runtime error in any sub-component blanks the whole Reels tab
// with no message. With it, the user sees exactly what went wrong.
class ReelsErrorBoundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null }
  static getDerivedStateFromError(err: Error) { return { err } }
  componentDidCatch(err: Error, info: unknown) {
    // Surface it to the browser console for the dev-tools trace too.
    // eslint-disable-next-line no-console
    console.error('ReelsView crashed:', err, info)
  }
  render() {
    if (this.state.err) {
      return (
        <div className="h-full flex items-center justify-center p-8">
          <div className="max-w-lg p-5 rounded-2xl border border-red-500/40 bg-red-500/10 space-y-3">
            <div className="flex items-center gap-2 text-red-300">
              <span className="font-mono text-[10px] uppercase tracking-widest">Reels crashed</span>
            </div>
            <div className="text-sm text-[var(--text-primary)] font-mono break-words">
              {this.state.err.message || String(this.state.err)}
            </div>
            <button
              onClick={() => this.setState({ err: null })}
              className="px-3 py-1.5 rounded-lg text-[11px] font-mono uppercase tracking-wider text-red-100 border border-red-500/40 hover:bg-red-500/20"
            >
              Try again
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export function ReelsView() {
  return <ReelsErrorBoundary><ReelsViewInner /></ReelsErrorBoundary>
}

function ReelsViewInner() {
  const [tab, setTab]            = useState<Tab>('new')
  const [topic, setTopic]        = useState('')
  const [tone, setTone]          = useState<Tone>('informative')
  const [duration, setDuration]  = useState<number>(30)
  const [aspect, setAspect]      = useState<Aspect>('9:16')
  const [voices, setVoices]      = useState<Voice[]>([])
  const [voiceId, setVoiceId]    = useState<string>('af_heart')
  const [plan, setPlan]          = useState<Plan | null>(null)
  const [planning, setPlanning]  = useState(false)
  const [planError, setPlanErr]  = useState<string | null>(null)
  const [comfy, setComfy]        = useState<ComfyStatus | null>(null)
  const [checkpoints, setChkps]  = useState<string[]>([])
  const [checkpoint, setChkp]    = useState<string>('')
  const [rendering, setRender]   = useState(false)
  const [progress, setProgress]  = useState<RenderProgress>({ stage: 'idle' })
  const [videoUrl, setVideoUrl]  = useState<string | null>(null)
  const [reels, setReels]        = useState<Reel[]>([])
  const [music, setMusic]        = useState<MusicAsset | null>(null)
  const [musicVol, setMusicVol]  = useState(0.18)
  const [uploading, setUpload]   = useState<string | null>(null)   // asset kind currently uploading
  const [masterVid, setMasterV]  = useState<MasterVideo | null>(null)
  // Rendering options (below the plan, above the render button)
  const [voiceOn, setVoiceOn]     = useState(true)
  const [capsOn, setCapsOn]       = useState(true)
  const [capMode, setCapMode]     = useState<'script' | 'transcript'>('script')
  const [translate, setTranslate] = useState(false)
  const [comfyStarting, setStart]  = useState(false)
  const [comfyStartErr, setSErr]   = useState<string | null>(null)
  const [needComfyPath, setNeedP]  = useState(false)
  const [comfyPathIn, setPathIn]   = useState('')

  // Install flow (only shown if user clicks "Install ComfyUI" from the amber panel)
  const [installOpen, setInstOpen]        = useState(false)
  const [installPath, setInstPath]        = useState('~/ComfyUI')
  const [dlCheckpoint, setDlChkp]         = useState(true)
  const [installing, setInstalling]       = useState(false)
  const [installStage, setInstStage]      = useState<string>('')
  const [installPct, setInstPct]          = useState(0)
  const [installMsg, setInstMsg]          = useState<string>('')
  const [installLog, setInstLog]          = useState<string[]>([])
  const [installErr, setInstErr]          = useState<string | null>(null)
  const abortRef                 = useRef<AbortController | null>(null)
  const renderAbort              = useRef<AbortController | null>(null)

  useEffect(() => {
    fetch('/api/tts/voices')
      .then(r => r.json()).then(d => setVoices(d.voices ?? []))
      .catch(() => {})
    refreshLibrary()
    autoStartComfy()  // status + checkpoints refresh happen inside
  }, [])

  // If the initial /status says offline, ask the backend to start it, then
  // poll every 2s until it's ready (max 90s — ComfyUI first-run can be slow).
  async function autoStartComfy() {
    // First: cheap status probe.
    let status: ComfyStatus | null = null
    try {
      const r = await fetch('/api/reels/comfy/status')
      status = await r.json()
    } catch {
      status = { running: false, error: 'unreachable' }
    }
    setComfy(status)
    if (status?.running) {
      refreshCheckpoints()
      return
    }

    // Try to spawn.
    setStart(true)
    setSErr(null)
    setNeedP(false)
    try {
      const r = await fetch('/api/reels/comfy/start', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({}),   // let backend auto-discover
      })
      const d = await r.json()
      if (d.need_path) {
        setNeedP(true)
        setStart(false)
        return
      }
      if (!d.started) {
        setSErr(d.message ?? 'ComfyUI failed to start')
        setStart(false)
        return
      }
    } catch (exc: any) {
      setSErr(exc?.message ?? 'start failed')
      setStart(false)
      return
    }
    // Poll for readiness.
    const t0 = Date.now()
    while (Date.now() - t0 < 90_000) {
      await new Promise(r => setTimeout(r, 2000))
      try {
        const r = await fetch('/api/reels/comfy/status')
        const s: ComfyStatus = await r.json()
        setComfy(s)
        if (s.running) {
          setStart(false)
          refreshCheckpoints()
          return
        }
      } catch { /* keep polling */ }
    }
    setStart(false)
    setSErr('ComfyUI did not become ready within 90 s. Check the log next to main.py.')
  }

  async function submitComfyPath() {
    if (!comfyPathIn.trim()) return
    setStart(true)
    setSErr(null)
    setNeedP(false)
    try {
      const r = await fetch('/api/reels/comfy/start', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ path: comfyPathIn.trim() }),
      })
      const d = await r.json()
      if (d.need_path) {
        setNeedP(true)
        setSErr('No main.py found at that path.')
        setStart(false)
        return
      }
      if (!d.started) {
        setSErr(d.message ?? 'start failed')
        setStart(false)
        return
      }
      // reuse the poll loop by calling autoStartComfy() — status is offline
      // so it will fall through to the poll block cleanly.
      autoStartComfy()
    } catch (exc: any) {
      setSErr(exc?.message ?? 'start failed')
      setStart(false)
    }
  }

  async function runInstall() {
    if (installing) return
    setInstalling(true)
    setInstErr(null)
    setInstStage('clone')
    setInstPct(0)
    setInstMsg('')
    setInstLog([])
    try {
      const res = await fetch('/api/reels/comfy/install', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ path: installPath, download_checkpoint: dlCheckpoint }),
      })
      if (!res.body) throw new Error('no stream')
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const evts = buf.split('\n\n')
        buf = evts.pop() ?? ''
        for (const evt of evts) {
          const line = evt.replace(/^data:\s?/, '').trim()
          if (!line || line === '[DONE]') continue
          try {
            const chunk = JSON.parse(line)
            if (chunk.stage) setInstStage(chunk.stage)
            if (typeof chunk.progress === 'number') setInstPct(chunk.progress)
            if (chunk.message) {
              setInstMsg(chunk.message)
              setInstLog(prev => [...prev.slice(-40), chunk.message])
            }
            if (chunk.stage === 'error') {
              setInstErr(chunk.error ?? 'install failed')
            }
            if (chunk.stage === 'ready') {
              // Installation complete + ComfyUI is spawning. Kick off our
              // usual status poll loop so the header chip flips to green.
              setNeedP(false)
              setInstOpen(false)
              autoStartComfy()
            }
          } catch { /* ignore malformed chunks */ }
        }
      }
    } catch (exc: any) {
      setInstErr(exc?.message ?? 'install failed')
    } finally {
      setInstalling(false)
    }
  }

  async function stopComfy() {
    try {
      await fetch('/api/reels/comfy/stop', { method: 'POST' })
    } catch {}
    setComfy({ running: false })
    setChkps([])
  }

  const refreshCheckpoints = useCallback(async () => {
    try {
      const r = await fetch('/api/reels/comfy/checkpoints')
      const d = await r.json()
      const list: string[] = d.checkpoints ?? []
      setChkps(list)
      setChkp(prev => prev || list[0] || '')
    } catch { /* ComfyUI probably offline — leave dropdown empty */ }
  }, [])

  const refreshLibrary = useCallback(async () => {
    try {
      const r = await fetch('/api/reels/library')
      const d = await r.json()
      setReels(d.reels ?? [])
    } catch { /* empty is fine */ }
  }, [])

  async function uploadAsset(kind: 'music' | 'scene_image' | 'scene_video', file: File): Promise<{ path: string; url: string; name: string } | null> {
    setUpload(kind)
    try {
      const fd = new FormData()
      fd.append('kind', kind)
      fd.append('file', file)
      const r = await fetch('/api/reels/assets/upload', { method: 'POST', body: fd })
      if (!r.ok) {
        alert(`Upload failed: ${await r.text()}`)
        return null
      }
      return await r.json()
    } catch (exc: any) {
      alert(`Upload failed: ${exc?.message ?? exc}`)
      return null
    } finally {
      setUpload(null)
    }
  }

  async function attachMusic(file: File) {
    const r = await uploadAsset('music', file)
    if (r) setMusic({ name: r.name, path: r.path, url: r.url, bytes: file.size })
  }

  async function attachMasterVideo(file: File) {
    const r = await uploadAsset('scene_video', file)
    if (!r) return
    setMasterV({
      name:  r.name,
      path:  r.path,
      url:   r.url,
      bytes: (r as any).bytes ?? file.size,
      converted: !!(r as any).converted,
    })
  }

  async function attachSceneMedia(sceneN: number, file: File) {
    if (!plan) return
    const isVideo = file.type.startsWith('video/')
    const kind: 'scene_image' | 'scene_video' = isVideo ? 'scene_video' : 'scene_image'
    const r = await uploadAsset(kind, file)
    if (!r) return
    setPlan({
      ...plan,
      scenes: plan.scenes.map(s => {
        if (s.n !== sceneN) return s
        // Attaching a new override replaces whatever was there (video XOR image).
        return isVideo
          ? { ...s,
              overrideVideoPath: r.path, overrideVideoUrl: r.url,
              overrideImagePath: undefined, overrideImageUrl: undefined }
          : { ...s,
              overrideImagePath: r.path, overrideImageUrl: r.url,
              overrideVideoPath: undefined, overrideVideoUrl: undefined }
      }),
    })
  }

  function patchScene(sceneN: number, patch: Partial<Scene>) {
    if (!plan) return
    setPlan({
      ...plan,
      scenes: plan.scenes.map(s => (s.n === sceneN ? { ...s, ...patch } : s)),
    })
  }

  function clearSceneMedia(sceneN: number) {
    if (!plan) return
    setPlan({
      ...plan,
      scenes: plan.scenes.map(s =>
        s.n === sceneN
          ? { ...s,
              overrideImagePath: undefined, overrideImageUrl: undefined,
              overrideVideoPath: undefined, overrideVideoUrl: undefined }
          : s,
      ),
    })
  }

  async function renderVideo() {
    if (!plan || !checkpoint || rendering) return
    setRender(true)
    setProgress({ stage: 'image', scene: 1, total: plan.scenes.length })
    setVideoUrl(null)
    const controller = new AbortController()
    renderAbort.current = controller

    try {
      // Cascade the master footage into every scene that doesn't already have
      // its own per-scene override. Per-scene attachments always win.
      //
      // When we cascade the master, we also cascade a per-scene start offset
      // so scene N plays from `sum(previous durations)` into the source —
      // otherwise every scene would replay the video from t=0.
      let cursor = 0
      const effectivePlan = masterVid ? {
        ...plan,
        scenes: plan.scenes.map(s => {
          const hasPerSceneVideo = !!s.overrideVideoPath
          const hasPerSceneImage = !!s.overrideImagePath
          const useMaster        = !hasPerSceneVideo && !hasPerSceneImage
          // The master-audio start we attach here is the same cursor value
          // whether the scene ends up using master as video or overrides
          // with an image — that way an image scene "sits at" the same time
          // in the master audio as it would have if the video was showing.
          const sceneCursor = s.overrideVideoStart ?? cursor
          const scene = {
            ...s,
            overrideVideoPath: useMaster ? masterVid.path : s.overrideVideoPath,
            overrideVideoUrl:  useMaster ? masterVid.url  : s.overrideVideoUrl,
            overrideVideoStart: useMaster
              ? (s.overrideVideoStart ?? cursor)
              : s.overrideVideoStart,
            // Master audio info cascades to EVERY scene (including per-scene
            // image overrides), so the backend can pick it up when it wants
            // to keep the speaker talking under a still image.
            masterAudioPath:  masterVid.path,
            masterAudioStart: sceneCursor,
          }
          // Advance the cursor unless this scene has its own video clip
          // (per-scene video has its own timeline; image overrides sit
          // at the current master timeline position).
          if (!hasPerSceneVideo) cursor += Math.max(1, Number(s.seconds) || 0)
          return scene
        }),
      } : plan

      const res = await fetch('/api/reels/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          plan: effectivePlan,
          checkpoint,
          musicPath:   music?.path ?? null,
          musicVolume: musicVol,
          voiceover:   voiceOn,
          captions:    capsOn,
          captionMode: capMode,
          translate,
        }),
        signal: controller.signal,
      })
      if (!res.body) throw new Error('no stream')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const evts = buf.split('\n\n')
        buf = evts.pop() ?? ''
        for (const evt of evts) {
          const line = evt.replace(/^data:\s?/, '').trim()
          if (!line || line === '[DONE]') continue
          try {
            const chunk = JSON.parse(line)
            if (chunk.stage === 'done' && chunk.url) {
              setVideoUrl(chunk.url)
              setProgress({ stage: 'done' })
              refreshLibrary()
            } else if (chunk.stage === 'error') {
              setProgress({ stage: 'error', error: chunk.error })
            } else if (chunk.stage) {
              setProgress({
                stage:  chunk.stage,
                scene:  chunk.scene,
                total:  chunk.total,
              })
            }
          } catch { /* skip malformed chunk */ }
        }
      }
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        setProgress({ stage: 'error', error: err?.message ?? 'render failed' })
      }
    } finally {
      setRender(false)
      renderAbort.current = null
    }
  }

  function stopRender() {
    renderAbort.current?.abort()
    setRender(false)
  }

  const groupedVoices = groupBy(voices, v => v.accent ?? 'other')

  async function planScenes() {
    if (!topic.trim() || planning) return
    setPlanning(true)
    setPlanErr(null)
    setPlan(null)
    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch('/api/reels/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic, tone, duration, aspect, voice: voiceId }),
        signal: controller.signal,
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `HTTP ${res.status}`)
      }
      if (!res.body) throw new Error('no response stream')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let partial: Partial<Plan> = { topic, tone, aspect, voice: voiceId, duration, scenes: [] }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const evts = buf.split('\n\n')
        buf = evts.pop() ?? ''
        for (const evt of evts) {
          const line = evt.replace(/^data:\s?/, '').trim()
          if (!line || line === '[DONE]') continue
          try {
            const chunk = JSON.parse(line)
            if (chunk.scene) {
              partial.scenes = [...(partial.scenes ?? []), chunk.scene]
              setPlan({ ...(partial as Plan) })
            } else if (chunk.error) {
              throw new Error(chunk.error)
            }
          } catch (parseErr) {
            // JSON.parse fail on a partial event is normal — keep going
            if (parseErr instanceof Error && parseErr.message && !parseErr.message.startsWith('Unexpected'))
              throw parseErr
          }
        }
      }
    } catch (err: any) {
      if (err?.name !== 'AbortError') setPlanErr(err?.message ?? 'plan failed')
    } finally {
      setPlanning(false)
      abortRef.current = null
    }
  }

  function stopPlanning() {
    abortRef.current?.abort()
    setPlanning(false)
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* ── Header ────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div
            className="w-11 h-11 rounded-2xl flex items-center justify-center"
            style={{
              background: 'radial-gradient(circle at 30% 25%, rgba(255,255,255,0.35), transparent 40%), conic-gradient(from 200deg at 50% 50%, var(--accent), var(--holo), var(--accent))',
              boxShadow:  '0 0 20px var(--accent-glow), inset 0 -3px 6px rgba(0,0,0,0.3)',
            }}
          >
            <Clapperboard className="w-5 h-5 text-white" />
          </div>
          <div>
            <h2 className="font-display text-2xl text-[var(--text-primary)] leading-none">Reels</h2>
            <p className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)] mt-1">
              short-form video studio · 100% local
            </p>
          </div>
        </div>

        <ComfyChip
          status={comfy}
          starting={comfyStarting}
          onRefresh={autoStartComfy}
          onStop={stopComfy}
        />
      </div>

      {needComfyPath && !installOpen && (
        <NeedComfyPathPanel
          pathIn={comfyPathIn}
          onPath={setPathIn}
          onSubmit={submitComfyPath}
          onInstall={() => { setInstOpen(true); setSErr(null) }}
          onCancel={() => { setNeedP(false); setSErr(null) }}
          error={comfyStartErr}
          submitting={comfyStarting}
        />
      )}

      {installOpen && (
        <InstallComfyPanel
          path={installPath}       onPath={setInstPath}
          downloadCheckpoint={dlCheckpoint} onDownloadCheckpoint={setDlChkp}
          installing={installing}
          stage={installStage}
          progress={installPct}
          message={installMsg}
          log={installLog}
          error={installErr}
          onStart={runInstall}
          onCancel={() => setInstOpen(false)}
        />
      )}

      {/* ── Tabs ──────────────────────────────────────────────────── */}
      <div className="flex gap-1 mb-4 flex-shrink-0">
        {(['new', 'history'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest transition-all border ${
              tab === t
                ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)] hover:text-[var(--text-secondary)]'
            }`}
          >
            {t === 'new' ? 'new reel' : 'history'}
          </button>
        ))}
      </div>

      {/* ── Body ──────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto pr-1" style={{ scrollbarWidth: 'thin' }}>
        <AnimatePresence mode="wait">
          {tab === 'new' ? (
            <motion.div
              key="new"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.25 }}
              className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px] gap-4"
            >
              {/* Left: form + plan */}
              <div className="space-y-4">
                <MasterVideoPanel
                  video={masterVid}
                  onAttach={attachMasterVideo}
                  onClear={() => setMasterV(null)}
                  uploading={uploading === 'scene_video'}
                />

                <Panel className="p-5 space-y-4">
                  <div>
                    <label className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
                      Topic
                    </label>
                    <textarea
                      value={topic}
                      onChange={e => setTopic(e.target.value)}
                      rows={3}
                      placeholder="e.g. Three things you didn't know about the pomegranate…"
                      className="w-full mt-2 bg-[var(--bg-primary)]/60 border border-[var(--border)] rounded-xl px-3 py-2.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[var(--accent)] resize-none font-display"
                    />
                  </div>

                  {/* Tone chips */}
                  <ChipGroup
                    icon={Palette}
                    label="Tone"
                    options={TONES.map(t => ({ id: t.id, label: t.label, hint: t.hint }))}
                    value={tone}
                    onChange={id => setTone(id as Tone)}
                  />

                  {/* Duration */}
                  <ChipGroup
                    icon={Clock}
                    label="Duration"
                    options={DURATIONS.map(d => ({ id: String(d), label: `${d}s` }))}
                    value={String(duration)}
                    onChange={id => setDuration(Number(id))}
                  />

                  {/* Aspect */}
                  <ChipGroup
                    icon={Film}
                    label="Aspect"
                    options={ASPECTS.map(a => ({ id: a.id, label: a.label, hint: a.platform }))}
                    value={aspect}
                    onChange={id => setAspect(id as Aspect)}
                  />

                  {/* Voice picker */}
                  <div>
                    <label className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
                      <MicVocal className="w-3 h-3" />
                      Voice
                    </label>
                    <select
                      value={voiceId}
                      onChange={e => setVoiceId(e.target.value)}
                      className="w-full mt-2 bg-[var(--bg-primary)]/60 border border-[var(--border)] rounded-xl px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)] font-mono"
                    >
                      {Object.entries(groupedVoices).map(([accent, list]) => (
                        <optgroup key={accent} label={accent.toUpperCase()}>
                          {list.map(v => (
                            <option key={v.id} value={v.id}>
                              {v.name} · {v.gender} — {v.description}
                            </option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 pt-1">
                    <button
                      onClick={planning ? stopPlanning : planScenes}
                      disabled={!topic.trim() && !planning}
                      className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all ${
                        planning
                          ? 'bg-[var(--accent-dim)] text-[var(--accent)] border border-[var(--accent)]'
                          : 'bg-gradient-to-r from-[var(--accent)] to-[var(--holo)] text-white shadow-lg shadow-[var(--accent-glow)] disabled:from-[var(--bg-tertiary)] disabled:to-[var(--bg-tertiary)] disabled:text-[var(--text-muted)] disabled:shadow-none'
                      }`}
                    >
                      {planning ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Planning… (click to stop)
                        </>
                      ) : (
                        <>
                          <Wand2 className="w-4 h-4" />
                          Plan scenes
                        </>
                      )}
                    </button>
                  </div>

                  {planError && (
                    <div className="flex items-start gap-2 p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-300">
                      <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
                      <span>{planError}</span>
                    </div>
                  )}
                </Panel>

                {/* Plan preview */}
                {plan && plan.scenes.length > 0 && (
                  <Panel className="p-5 space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="font-display text-lg text-[var(--text-primary)]">Scene plan</h3>
                      <span className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
                        {plan.scenes.length} scenes · {plan.scenes.reduce((a, s) => a + s.seconds, 0)}s total
                      </span>
                    </div>

                    <div className="space-y-2">
                      {plan.scenes.map(s => (
                        <SceneCard
                          key={s.n}
                          scene={s}
                          masterVid={masterVid}
                          onAttach={file => attachSceneMedia(s.n, file)}
                          onClear={() => clearSceneMedia(s.n)}
                          onPatch={patch => patchScene(s.n, patch)}
                          uploading={uploading === 'scene_image' || uploading === 'scene_video'}
                        />
                      ))}
                    </div>

                    {/* Music drop-zone (optional) */}
                    <MusicPanel
                      music={music}
                      volume={musicVol}
                      onChangeVolume={setMusicVol}
                      onAttach={attachMusic}
                      onClear={() => setMusic(null)}
                      uploading={uploading === 'music'}
                    />

                    {/* Voiceover / captions / transcript options */}
                    <RenderOptionsPanel
                      voiceOn={voiceOn}    onVoiceOn={setVoiceOn}
                      capsOn={capsOn}      onCapsOn={setCapsOn}
                      capMode={capMode}    onCapMode={setCapMode}
                      translate={translate} onTranslate={setTranslate}
                    />

                    {/* Checkpoint picker (only enabled if ComfyUI is up) */}
                    <div className="pt-1">
                      <label className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
                        <ImageIcon className="w-3 h-3" />
                        SD checkpoint
                      </label>
                      <select
                        value={checkpoint}
                        onChange={e => setChkp(e.target.value)}
                        disabled={!comfy?.running || checkpoints.length === 0}
                        className="w-full mt-2 bg-[var(--bg-primary)]/60 border border-[var(--border)] rounded-xl px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)] font-mono disabled:opacity-50"
                      >
                        {checkpoints.length === 0
                          ? <option value="">{comfy?.running ? 'no checkpoints found in ComfyUI' : 'ComfyUI offline — start it on :8188'}</option>
                          : checkpoints.map(c => <option key={c} value={c}>{c}</option>)
                        }
                      </select>
                    </div>

                    {/* Render button + progress */}
                    <div className="pt-2 space-y-2">
                      <button
                        onClick={rendering ? stopRender : renderVideo}
                        disabled={!checkpoint || !comfy?.running}
                        title={
                          !comfy?.running ? 'ComfyUI must be running on :8188'
                          : !checkpoint    ? 'Pick a checkpoint first'
                          : rendering      ? 'Stop render'
                          : 'Render this reel'
                        }
                        className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all ${
                          rendering
                            ? 'bg-[var(--accent-dim)] text-[var(--accent)] border border-[var(--accent)]'
                            : 'bg-gradient-to-r from-[var(--accent)] to-[var(--holo)] text-white shadow-lg shadow-[var(--accent-glow)] disabled:from-[var(--bg-tertiary)] disabled:to-[var(--bg-tertiary)] disabled:text-[var(--text-muted)] disabled:shadow-none'
                        }`}
                      >
                        {rendering
                          ? <><Square className="w-4 h-4" /> Stop render</>
                          : <><Film className="w-4 h-4" /> Render video</>}
                      </button>
                      {(rendering || progress.stage === 'error' || progress.stage === 'done') && (
                        <RenderProgressBar progress={progress} total={plan.scenes.length} />
                      )}
                    </div>
                  </Panel>
                )}

                {/* Video preview */}
                {videoUrl && (
                  <Panel className="p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="font-display text-lg text-[var(--text-primary)]">Result</h3>
                      <a
                        href={videoUrl}
                        download
                        className="text-[10px] font-mono uppercase tracking-widest text-[var(--accent)] hover:underline"
                      >
                        download mp4 ↗
                      </a>
                    </div>
                    <video
                      src={videoUrl}
                      controls autoPlay
                      className="w-full rounded-xl border border-[var(--border)] bg-black"
                      style={{ maxHeight: 640 }}
                    />
                  </Panel>
                )}
              </div>

              {/* Right: preview + tips */}
              <div className="space-y-4">
                <AspectPreview aspect={aspect} plan={plan} planning={planning} masterVid={masterVid} />
                <PipelinePanel comfy={comfy} />
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="history"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.25 }}
            >
              {reels.length === 0 ? (
                <p className="pt-6 text-center font-display-italic text-[var(--text-muted)]">
                  No reels yet — render one from the New tab.
                </p>
              ) : (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                  {reels.map(r => <ReelCard key={r.id} reel={r} />)}
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────
function ComfyChip({
  status, starting, onRefresh, onStop,
}: {
  status: ComfyStatus | null
  starting: boolean
  onRefresh: () => void
  onStop: () => void
}) {
  if (starting) {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-[var(--accent)]/40 bg-[var(--accent-dim)] text-[10px] font-mono uppercase tracking-widest text-[var(--accent)]">
        <Loader2 className="w-3 h-3 animate-spin" />
        comfyui · starting…
      </div>
    )
  }
  if (!status) {
    return (
      <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] flex items-center gap-1.5">
        <Loader2 className="w-3 h-3 animate-spin" /> comfyui…
      </div>
    )
  }
  const running = status.running
  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={onRefresh}
        title={running
          ? `ComfyUI ${status.version ?? ''} · click to re-probe`
          : `ComfyUI unreachable: ${status.error ?? 'no process on :8188'} · click to try starting`
        }
        className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-[10px] font-mono uppercase tracking-widest transition-all ${
          running
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20'
            : 'border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20'
        }`}
      >
        <span className={`w-2 h-2 rounded-full ${running ? 'bg-emerald-400 shadow-[0_0_6px_2px_rgba(52,211,153,0.6)]' : 'bg-amber-400'}`} />
        comfyui · {running ? 'ready' : 'offline'}
      </button>
      {running && (
        <button
          onClick={onStop}
          title="Stop the ComfyUI process Persephone spawned"
          className="p-1.5 rounded-full border border-[var(--border)] text-[var(--text-muted)] hover:text-red-300 hover:border-red-300/50 transition-colors"
        >
          <Square className="w-3 h-3" />
        </button>
      )}
    </div>
  )
}

function NeedComfyPathPanel({
  pathIn, onPath, onSubmit, onInstall, onCancel, error, submitting,
}: {
  pathIn: string
  onPath: (s: string) => void
  onSubmit: () => void
  onInstall: () => void
  onCancel: () => void
  error: string | null
  submitting: boolean
}) {
  return (
    <div className="mb-4 p-4 rounded-2xl border border-amber-500/40 bg-amber-500/10 space-y-3">
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5 text-amber-300" />
        <div className="flex-1 text-xs text-amber-100 leading-snug">
          <div className="font-semibold mb-0.5">ComfyUI not found</div>
          Persephone looked in <code className="font-mono">~/ComfyUI</code>, <code className="font-mono">~/comfyui</code>, and a few common locations but didn't find <code className="font-mono">main.py</code>. Either point it at your existing install, or let Persephone install one for you.
        </div>
      </div>

      {/* Option A — install for me */}
      <button
        onClick={onInstall}
        className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-sm font-medium bg-gradient-to-r from-amber-500/70 to-amber-400/70 text-black hover:from-amber-400 hover:to-amber-300 transition-all"
      >
        <Download className="w-4 h-4" />
        Install ComfyUI for me
      </button>

      <div className="text-center text-[10px] font-mono uppercase tracking-widest text-amber-200/60">— or —</div>

      {/* Option B — point at existing install */}
      <div className="flex gap-2">
        <input
          type="text"
          value={pathIn}
          onChange={e => onPath(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') onSubmit() }}
          placeholder="/Users/you/ComfyUI"
          className="flex-1 bg-black/30 border border-amber-500/30 rounded-lg px-3 py-2 text-xs font-mono text-amber-50 placeholder:text-amber-200/40 outline-none focus:border-amber-400"
        />
        <button
          onClick={onSubmit}
          disabled={!pathIn.trim() || submitting}
          className="px-3 py-2 rounded-lg text-[11px] font-mono uppercase tracking-wider text-amber-100 border border-amber-500/40 hover:bg-amber-500/20 disabled:opacity-40 flex items-center gap-1"
        >
          {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'use path'}
        </button>
        <button
          onClick={onCancel}
          className="p-2 rounded-lg text-amber-200/70 hover:text-amber-100 hover:bg-amber-500/10"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
      {error && (
        <div className="text-[10.5px] text-red-300 font-mono">{error}</div>
      )}
    </div>
  )
}

function InstallComfyPanel({
  path, onPath,
  downloadCheckpoint, onDownloadCheckpoint,
  installing, stage, progress, message, log, error,
  onStart, onCancel,
}: {
  path: string
  onPath: (s: string) => void
  downloadCheckpoint: boolean
  onDownloadCheckpoint: (v: boolean) => void
  installing: boolean
  stage: string
  progress: number
  message: string
  log: string[]
  error: string | null
  onStart: () => void
  onCancel: () => void
}) {
  const stages: { id: string; label: string }[] = [
    { id: 'clone',      label: 'Clone repo'          },
    { id: 'venv',       label: 'Create Python venv'  },
    { id: 'deps',       label: 'Install dependencies' },
    { id: 'checkpoint', label: 'Download checkpoint' },
    { id: 'start',      label: 'Launch ComfyUI'      },
  ]
  const stageIdx = stages.findIndex(s => s.id === stage)
  const done     = stageIdx < 0 && stage === 'ready'

  return (
    <div className="mb-4 p-5 rounded-2xl border border-[var(--accent)]/40 bg-[var(--accent-dim)] space-y-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="font-display text-lg text-[var(--text-primary)] leading-none">Install ComfyUI</h3>
          <p className="text-[11px] text-[var(--text-muted)] mt-1.5">
            Clones the ComfyUI repo, sets up a Python venv, installs its dependencies, and optionally downloads SDXL Base 1.0 (~6.5&nbsp;GB). One-time.
          </p>
        </div>
        {!installing && (
          <button
            onClick={onCancel}
            className="p-1.5 rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Config (only editable before start) */}
      {!installing && !done && (
        <div className="space-y-3">
          <div>
            <label className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
              Install path
            </label>
            <input
              type="text"
              value={path}
              onChange={e => onPath(e.target.value)}
              className="w-full mt-2 bg-[var(--bg-primary)]/60 border border-[var(--border)] rounded-xl px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)] font-mono"
            />
          </div>
          <label className="flex items-center gap-2.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={downloadCheckpoint}
              onChange={e => onDownloadCheckpoint(e.target.checked)}
              className="w-4 h-4 accent-[var(--accent)]"
            />
            <span className="text-sm text-[var(--text-primary)]">Also download SDXL Base 1.0</span>
            <span className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] ml-auto">~6.5 GB</span>
          </label>
          <button
            onClick={onStart}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium bg-gradient-to-r from-[var(--accent)] to-[var(--holo)] text-white shadow-lg shadow-[var(--accent-glow)] hover:brightness-110 transition-all"
          >
            <Download className="w-4 h-4" />
            Start install
          </button>
          <p className="text-[10px] font-mono text-[var(--text-muted)] text-center">
            Takes ~5–15 min on a decent connection. Runs in a detached process — safe to leave open.
          </p>
        </div>
      )}

      {/* Progress */}
      {(installing || done) && (
        <div className="space-y-3">
          {/* stage tracker */}
          <div className="flex items-center gap-1">
            {stages.map((s, i) => {
              const state =
                error && stages.findIndex(x => x.id === stage) === i ? 'error' :
                i < stageIdx    ? 'done' :
                i === stageIdx  ? 'active' :
                stage === 'ready' ? 'done' : 'pending'
              return (
                <div key={s.id} className="flex-1 min-w-0">
                  <div className={`h-1 rounded-full transition-all ${
                    state === 'done'   ? 'bg-emerald-400'
                    : state === 'active' ? 'bg-gradient-to-r from-[var(--accent)] to-[var(--holo)] animate-pulse'
                    : state === 'error'  ? 'bg-red-400'
                                         : 'bg-[var(--bg-tertiary)]'
                  }`} />
                  <div className={`text-[9px] font-mono uppercase tracking-widest mt-1.5 truncate ${
                    state === 'done'   ? 'text-emerald-300'
                    : state === 'active' ? 'text-[var(--accent)]'
                    : state === 'error'  ? 'text-red-300'
                                         : 'text-[var(--text-muted)]'
                  }`}>
                    {s.label}
                  </div>
                </div>
              )
            })}
          </div>

          {/* per-stage progress bar */}
          {installing && (
            <div className="space-y-1.5">
              <div className="h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
                <div
                  className="h-full transition-all duration-300"
                  style={{
                    width: `${Math.round(Math.max(0, Math.min(1, progress)) * 100)}%`,
                    background: 'linear-gradient(90deg, var(--accent), var(--holo))',
                    boxShadow:  '0 0 10px var(--accent-glow)',
                  }}
                />
              </div>
              <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
                <span className="truncate">{message || `${stage}…`}</span>
                <span className="tabular-nums">{Math.round(Math.max(0, Math.min(1, progress)) * 100)}%</span>
              </div>
            </div>
          )}

          {/* log tail */}
          {log.length > 0 && (
            <div className="max-h-32 overflow-y-auto rounded-lg border border-[var(--border)] bg-black/40 p-2 font-mono text-[10.5px] leading-tight text-[var(--text-muted)] space-y-0.5">
              {log.slice(-30).map((l, i) => (
                <div key={i} className="truncate">{l}</div>
              ))}
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-300">
              <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
              <span>{error}</span>
            </div>
          )}

          {done && !error && (
            <div className="flex items-center justify-center gap-2 text-sm text-emerald-300">
              <Sparkles className="w-4 h-4" />
              ComfyUI is starting — the header chip will turn green in a moment.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ChipGroup<T extends string>({
  icon: Icon, label, options, value, onChange,
}: {
  icon: React.ElementType
  label: string
  options: { id: T; label: string; hint?: string }[]
  value: T
  onChange: (id: T) => void
}) {
  return (
    <div>
      <label className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
        <Icon className="w-3 h-3" />
        {label}
      </label>
      <div className="flex flex-wrap gap-1.5 mt-2">
        {options.map(o => (
          <button
            key={o.id}
            onClick={() => onChange(o.id)}
            title={o.hint}
            className={`px-3 py-1.5 rounded-full text-[11px] font-mono uppercase tracking-wider border transition-all ${
              value === o.id
                ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)] shadow-inner'
                : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)] hover:text-[var(--text-secondary)]'
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function SceneCard({
  scene, masterVid, onAttach, onClear, onPatch, uploading,
}: {
  scene: Scene
  masterVid: MasterVideo | null
  onAttach: (file: File) => void
  onClear:  () => void
  onPatch:  (patch: Partial<Scene>) => void
  uploading: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const hasVideo = !!scene.overrideVideoUrl
  const hasImage = !hasVideo && !!scene.overrideImageUrl
  const hasOverride = hasVideo || hasImage
  const usingMaster = !hasOverride && !!masterVid
  // Chromium can't preview every container/codec (.mov ProRes, some HEVC).
  // The render pipeline uses Persephone's Homebrew ffmpeg which supports
  // everything, so a preview failure is cosmetic — we just show a card.
  const [videoPreviewErr, setVideoPreviewErr] = useState(false)
  const [scriptDraft, setScriptDraft] = useState(scene.script)
  const [startDraft,  setStartDraft]  = useState<string>(String(scene.overrideVideoStart ?? 0))

  // Keep local drafts in sync when the plan gets replanned or master cascades.
  useEffect(() => { setScriptDraft(scene.script) }, [scene.script])
  useEffect(() => { setStartDraft(String(scene.overrideVideoStart ?? 0)) },
            [scene.overrideVideoStart])

  const commitScript = () => {
    const trimmed = scriptDraft.trim()
    if (trimmed !== scene.script) onPatch({ script: trimmed })
  }
  const commitStart = () => {
    const num = Math.max(0, Number(startDraft) || 0)
    if (num !== (scene.overrideVideoStart ?? 0)) onPatch({ overrideVideoStart: num })
  }

  const fx = scene.effects ?? {}
  const setFx = (patch: Partial<SceneEffects>) =>
    onPatch({ effects: { ...fx, ...patch } })
  const fxIsDefault =
    (fx.brightness ?? 0) === 0 &&
    (fx.contrast ?? 1) === 1 &&
    (fx.saturation ?? 1) === 1 &&
    (fx.speed ?? 1) === 1 &&
    !fx.grayscale

  const [fxOpen, setFxOpen] = useState(false)

  const badge = hasVideo
    ? 'your video · ComfyUI + Ken Burns skipped'
    : hasImage
    ? (masterVid
        ? 'your image · master audio still plays'
        : 'your image · ComfyUI skipped')
    : ''

  return (
    <div className="p-3 rounded-xl border border-[var(--border)] bg-[var(--bg-primary)]/40 hover:border-[var(--border-bright)] transition-colors space-y-2">
    <div className="flex gap-3">
      {/* Thumbnail: video preview, image preview, or numbered orb */}
      {hasVideo ? (
        <div className="relative flex-shrink-0">
          {videoPreviewErr ? (
            // Codec unsupported by Electron's decoder — show a stylised
            // placeholder that still signals "video attached".
            <div
              className="w-14 h-14 rounded-lg border border-[var(--accent)] flex items-center justify-center"
              style={{
                background: 'radial-gradient(circle at 30% 30%, rgba(255,255,255,0.08), transparent 70%), linear-gradient(180deg, #0d0913, #060309)',
              }}
              title="Preview unsupported by browser codec — render will still work"
            >
              <Film className="w-6 h-6 text-emerald-300" />
            </div>
          ) : (
            <video
              src={scene.overrideVideoUrl}
              className="w-14 h-14 rounded-lg object-cover border border-[var(--accent)] bg-black"
              muted playsInline preload="metadata"
              onError={() => setVideoPreviewErr(true)}
            />
          )}
          <span
            className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-mono font-bold text-white"
            style={{
              background: 'conic-gradient(from 220deg, var(--accent), var(--holo), var(--accent))',
              boxShadow:  '0 0 6px var(--accent-glow)',
            }}
          >
            {scene.n}
          </span>
          <span className="absolute top-0.5 left-0.5 px-1 py-px rounded-sm text-[8px] font-mono uppercase tracking-wider bg-black/70 text-white">
            {videoPreviewErr ? 'mov' : 'mp4'}
          </span>
        </div>
      ) : hasImage ? (
        <div className="relative flex-shrink-0">
          <img
            src={scene.overrideImageUrl}
            alt=""
            className="w-14 h-14 rounded-lg object-cover border border-[var(--accent)]"
          />
          <span
            className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-mono font-bold text-white"
            style={{
              background: 'conic-gradient(from 220deg, var(--accent), var(--holo), var(--accent))',
              boxShadow:  '0 0 6px var(--accent-glow)',
            }}
          >
            {scene.n}
          </span>
        </div>
      ) : (
        <div
          className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-mono font-bold text-white"
          style={{
            background: 'conic-gradient(from 220deg, var(--accent), var(--holo), var(--accent))',
            boxShadow:  '0 0 10px var(--accent-glow)',
          }}
        >
          {scene.n}
        </div>
      )}

      <div className="flex-1 min-w-0 space-y-1.5">
        {/* Editable script line — blur or ⌘Enter persists */}
        <textarea
          value={scriptDraft}
          onChange={e => setScriptDraft(e.target.value)}
          onBlur={commitScript}
          onKeyDown={e => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              commitScript()
              ;(e.target as HTMLTextAreaElement).blur()
            }
          }}
          rows={1}
          placeholder="caption text · read by Kokoro if voiceover is on"
          title="This text is what Kokoro will speak AND what gets burned in as the scene caption."
          className="w-full bg-transparent outline-none resize-none text-sm text-[var(--text-primary)] leading-snug hover:bg-[var(--bg-primary)]/40 focus:bg-[var(--bg-primary)]/60 rounded px-1 -mx-1 py-0.5"
          style={{ minHeight: '1.4em' }}
        />

        <div className="flex items-start gap-1.5 text-[11px] text-[var(--text-muted)]">
          {hasVideo ? <Film className="w-3 h-3 flex-shrink-0 mt-0.5 text-emerald-300" />
                    : <ImageIcon className="w-3 h-3 flex-shrink-0 mt-0.5" />}
          {hasOverride ? (
            <span className="text-emerald-300 not-italic font-mono uppercase tracking-wider text-[10px]">
              {badge}
            </span>
          ) : (
            <span className="italic truncate">{scene.imagePrompt}</span>
          )}
        </div>

        {/* Per-scene start-offset — visible only when a video (master or per-scene) will be used */}
        {(usingMaster || hasVideo) && (
          <div className="flex items-center gap-2 text-[10px] font-mono text-[var(--text-muted)]">
            <Clock className="w-3 h-3 flex-shrink-0" />
            <span className="uppercase tracking-widest">start at</span>
            <input
              type="number"
              min={0}
              step={0.5}
              value={startDraft}
              onChange={e => setStartDraft(e.target.value)}
              onBlur={commitStart}
              onKeyDown={e => {
                if (e.key === 'Enter') { commitStart(); (e.target as HTMLInputElement).blur() }
              }}
              className="w-14 bg-[var(--bg-primary)]/60 border border-[var(--border)] rounded px-1.5 py-0.5 text-right text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
            />
            <span className="lowercase">s into {hasVideo ? 'clip' : 'master'}</span>
          </div>
        )}
      </div>

      <div className="flex-shrink-0 flex flex-col items-end gap-1">
        <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
          {scene.seconds}s
        </div>
        <div className="flex items-center gap-1">
          <input
            ref={inputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,video/x-matroska"
            className="hidden"
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) onAttach(f)
              e.target.value = ''
            }}
          />
          {hasOverride ? (
            <button
              onClick={onClear}
              title="Remove override — go back to ComfyUI generation"
              className="p-1 rounded-md text-[var(--text-muted)] hover:text-red-300 hover:bg-red-500/10 transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          ) : null}
          <button
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
            title={
              masterVid
                ? 'Choose an image OR video clip.\n· Image: replaces the visual for this scene, master audio keeps playing.\n· Video: replaces both video AND its own audio.'
                : 'Choose an image OR video clip for this scene.'
            }
            className={`p-1 rounded-md transition-colors ${
              hasOverride
                ? 'text-[var(--accent)] hover:bg-[var(--accent-dim)]'
                : 'text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)]'
            } disabled:opacity-40`}
          >
            {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>
    </div>

    {/* Effects strip — collapsible, per-scene look controls */}
    <div className="pt-1 border-t border-[var(--border)]/60">
      <button
        onClick={() => setFxOpen(v => !v)}
        className="w-full flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
      >
        <Palette className="w-3 h-3" />
        Effects
        {!fxIsDefault && (
          <span className="ml-1 px-1.5 py-0.5 rounded text-[9px] bg-[var(--accent-dim)] text-[var(--accent)]">
            modified
          </span>
        )}
        <span className="ml-auto text-[var(--text-muted)]">{fxOpen ? '▾' : '▸'}</span>
      </button>
      {fxOpen && (
        <div className="mt-2 space-y-2 px-1">
          <SliderRow label="Brightness" value={fx.brightness ?? 0}
            min={-0.5} max={0.5} step={0.02} format={v => v.toFixed(2)}
            onChange={v => setFx({ brightness: v })}
          />
          <SliderRow label="Contrast" value={fx.contrast ?? 1}
            min={0.5} max={2.0} step={0.02} format={v => `${v.toFixed(2)}×`}
            onChange={v => setFx({ contrast: v })}
          />
          <SliderRow label="Saturation" value={fx.saturation ?? 1}
            min={0} max={2.5} step={0.02} format={v => `${v.toFixed(2)}×`}
            onChange={v => setFx({ saturation: v })}
          />
          <SliderRow label="Speed" value={fx.speed ?? 1}
            min={0.5} max={2.0} step={0.05} format={v => `${v.toFixed(2)}×`}
            onChange={v => setFx({ speed: v })}
          />
          <div className="flex items-center justify-between pt-1">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={!!fx.grayscale}
                onChange={e => setFx({ grayscale: e.target.checked })}
                className="w-3.5 h-3.5 accent-[var(--accent)]"
              />
              <span className="text-[11px] text-[var(--text-secondary)]">Grayscale</span>
            </label>
            {!fxIsDefault && (
              <button
                onClick={() => onPatch({ effects: {} })}
                className="text-[10px] font-mono uppercase tracking-wider text-[var(--text-muted)] hover:text-red-300"
              >
                reset
              </button>
            )}
          </div>
        </div>
      )}
    </div>
    </div>
  )
}

function SliderRow({
  label, value, min, max, step, format, onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  format: (v: number) => string
  onChange: (v: number) => void
}) {
  return (
    <div className="flex items-center gap-2 text-[10.5px] text-[var(--text-muted)]">
      <span className="w-16 font-mono uppercase tracking-widest">{label}</span>
      <input
        type="range"
        min={min} max={max} step={step}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="flex-1 accent-[var(--accent)]"
      />
      <span className="w-12 text-right font-mono tabular-nums text-[var(--text-secondary)]">
        {format(value)}
      </span>
    </div>
  )
}

function AspectPreview({
  aspect, plan, planning, masterVid,
}: {
  aspect: Aspect
  plan: Plan | null
  planning: boolean
  masterVid: MasterVideo | null
}) {
  const { w, h }        = aspectBox(aspect)
  const [scene, setScene] = useState(0)
  const [videoErr, setVE] = useState(false)
  const videoRef          = useRef<HTMLVideoElement>(null)
  const total             = plan?.scenes.length ?? 0

  useEffect(() => {
    if (scene >= total) setScene(0)
  }, [total, scene])

  const s = plan?.scenes[scene] ?? null
  const showVideo = !!(s?.overrideVideoUrl || masterVid)
  const videoSrc  = s?.overrideVideoUrl ?? masterVid?.url ?? ''
  const imgSrc    = s?.overrideImageUrl ?? ''
  // If this scene is using master video (no per-scene video/image), start
  // the preview player at the same offset the render pipeline will use.
  const videoStart = s?.overrideVideoStart ?? 0

  // Seek the preview <video> to the scene's start-time whenever the user
  // clicks between scenes or the offset changes.
  useEffect(() => {
    if (!videoRef.current || !showVideo || s?.overrideImageUrl) return
    const v = videoRef.current
    const seek = () => { try { v.currentTime = Math.max(0, videoStart) } catch {} }
    if (v.readyState >= 1) seek()
    else v.addEventListener('loadedmetadata', seek, { once: true })
  }, [videoStart, videoSrc, showVideo, s?.overrideImageUrl])

  return (
    <Panel className="p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
          Preview · {aspect}
        </div>
        {total > 0 && !planning && (
          <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
            scene {scene + 1} / {total}
          </div>
        )}
      </div>

      <div
        className="mx-auto rounded-2xl border border-[var(--border)] relative overflow-hidden"
        style={{
          width:  w,
          height: h,
          background:
            'radial-gradient(ellipse 70% 50% at 50% 30%, var(--accent-dim), transparent 60%), linear-gradient(180deg, var(--bg-secondary), var(--bg-primary))',
        }}
      >
        {planning ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-[var(--text-muted)]">
            <Sparkles className="w-6 h-6 animate-pulse text-[var(--accent)]" />
            <span className="text-[10px] font-mono uppercase tracking-widest">planning scenes…</span>
          </div>
        ) : s ? (
          <>
            {/* Background: per-scene image beats video, master video is fallback */}
            {imgSrc ? (
              <img src={imgSrc} alt="" className="absolute inset-0 w-full h-full object-cover" />
            ) : showVideo && !videoErr ? (
              <video
                ref={videoRef}
                src={videoSrc}
                muted playsInline loop preload="metadata"
                className="absolute inset-0 w-full h-full object-cover"
                onError={() => setVE(true)}
              />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center text-[var(--text-muted)]">
                {videoErr ? <Film className="w-8 h-8 opacity-40" /> : <Play className="w-8 h-8 opacity-30" />}
              </div>
            )}

            {/* Bottom caption preview — matches the render's low-third position */}
            {s.script && (
              <div className="absolute inset-x-3 bottom-[11%] pointer-events-none">
                <div className="mx-auto max-w-[85%] px-3 py-1.5 rounded-lg text-white text-[11px] leading-tight text-center font-semibold"
                     style={{
                       background: 'rgba(0,0,0,0.28)',
                       textShadow: '0 0 3px black, 0 0 5px black',
                     }}>
                  {s.script}
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-[var(--text-muted)]">
            <Play className="w-8 h-8 opacity-30" />
          </div>
        )}
      </div>

      {/* Scene scrubber — dots you can click to preview any scene */}
      {total > 1 && !planning && (
        <div className="mt-3 flex items-center justify-center gap-1.5">
          {Array.from({ length: total }).map((_, i) => (
            <button
              key={i}
              onClick={() => setScene(i)}
              title={`Scene ${i + 1}`}
              className={`w-2 h-2 rounded-full transition-all ${
                i === scene
                  ? 'bg-[var(--accent)] w-6 shadow-[0_0_6px_var(--accent-glow)]'
                  : 'bg-[var(--bg-tertiary)] hover:bg-[var(--border-bright)]'
              }`}
            />
          ))}
        </div>
      )}
    </Panel>
  )
}

function PipelinePanel({ comfy }: { comfy: ComfyStatus | null }) {
  const steps = [
    { icon: Wand2,       label: 'Plan',      note: 'LLM scene planner',                ready: true            },
    { icon: ImageIcon,   label: 'Images',    note: 'ComfyUI · Stable Diffusion',       ready: !!comfy?.running },
    { icon: MicVocal,    label: 'Voice',     note: 'Kokoro TTS (per selected voice)',  ready: true            },
    { icon: Film,        label: 'Composite', note: 'ffmpeg · Ken Burns · captions',    ready: true            },
  ]
  return (
    <Panel className="p-4">
      <div className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)] mb-3">
        Pipeline
      </div>
      <div className="space-y-2">
        {steps.map((s, i) => (
          <div key={i} className="flex items-center gap-3">
            <div className={`flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${
              s.ready ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/40'
                     : 'bg-[var(--bg-tertiary)] text-[var(--text-muted)] border border-[var(--border)]'
            }`}>
              <s.icon className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm text-[var(--text-primary)] leading-none">{s.label}</div>
              <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] mt-1">{s.note}</div>
            </div>
            <span className={`text-[9px] font-mono uppercase tracking-widest ${
              s.ready ? 'text-emerald-300' : 'text-[var(--text-muted)]'
            }`}>
              {s.ready ? 'ready' : 'idle'}
            </span>
          </div>
        ))}
      </div>
    </Panel>
  )
}

function RenderOptionsPanel({
  voiceOn, onVoiceOn,
  capsOn,  onCapsOn,
  capMode, onCapMode,
  translate, onTranslate,
}: {
  voiceOn: boolean;   onVoiceOn:   (v: boolean) => void
  capsOn:  boolean;   onCapsOn:    (v: boolean) => void
  capMode: 'script' | 'transcript'; onCapMode: (v: 'script' | 'transcript') => void
  translate: boolean; onTranslate: (v: boolean) => void
}) {
  return (
    <div className="pt-1 space-y-3">
      <label className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
        Rendering options
      </label>

      {/* Voiceover */}
      <OptionRow
        icon={MicVocal}
        title="Voiceover"
        hint={voiceOn
          ? 'Kokoro speaks each scene\'s script line.'
          : 'No TTS. Video scenes use their own audio, image scenes go silent.'}
        checked={voiceOn}
        onChange={onVoiceOn}
      />

      {/* Captions */}
      <OptionRow
        icon={Captions}
        title="Captions"
        hint={capsOn ? 'Burned-in text at the low third.' : 'No text overlays at all.'}
        checked={capsOn}
        onChange={onCapsOn}
      />

      {/* Caption mode + translate — only when captions are on */}
      {capsOn && (
        <div className="pl-8 space-y-2">
          <div className="flex items-center gap-1.5">
            {(['script', 'transcript'] as const).map(m => (
              <button
                key={m}
                onClick={() => onCapMode(m)}
                className={`flex-1 px-2.5 py-1.5 rounded-lg text-[10px] font-mono uppercase tracking-wider border transition-all ${
                  capMode === m
                    ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                    : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)] hover:text-[var(--text-secondary)]'
                }`}
              >
                {m === 'script' ? 'ai script' : 'transcribe source'}
              </button>
            ))}
          </div>
          {capMode === 'transcript' && (
            <div className="space-y-2 p-3 rounded-lg border border-[var(--border)] bg-[var(--bg-primary)]/40">
              <div className="flex items-start gap-2 text-[11px] text-[var(--text-muted)] leading-snug">
                <Languages className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-[var(--accent)]" />
                <span>
                  Whisper transcribes the source audio of every video scene. First run downloads ~150 MB.
                </span>
              </div>
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={translate}
                  onChange={e => onTranslate(e.target.checked)}
                  className="w-4 h-4 accent-[var(--accent)]"
                />
                <span className="text-xs text-[var(--text-primary)]">
                  Translate to English
                </span>
                <span className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] ml-auto">
                  any language → EN
                </span>
              </label>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function OptionRow({
  icon: Icon, title, hint, checked, onChange,
}: {
  icon: React.ElementType
  title: string
  hint: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="w-full flex items-center gap-3 p-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-primary)]/40 hover:border-[var(--border-bright)] transition-colors text-left"
    >
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors flex-shrink-0 ${
        checked ? 'bg-[var(--accent-dim)] text-[var(--accent)]' : 'bg-[var(--bg-tertiary)] text-[var(--text-muted)]'
      }`}>
        <Icon className="w-4 h-4" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-[var(--text-primary)] leading-none">{title}</div>
        <div className="text-[10.5px] text-[var(--text-muted)] mt-1 leading-snug">{hint}</div>
      </div>
      {/* Pill switch — purely visual, the whole row is one <button>. */}
      <span className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
        checked ? 'bg-[var(--accent)]' : 'bg-[var(--bg-tertiary)]'
      }`}>
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
          checked ? 'translate-x-4' : ''
        }`} />
      </span>
    </button>
  )
}

function MasterVideoPanel({
  video, onAttach, onClear, uploading,
}: {
  video: MasterVideo | null
  onAttach: (file: File) => void
  onClear:  () => void
  uploading: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [previewErr, setPreviewErr] = useState(false)

  return (
    <Panel className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Film className="w-3.5 h-3.5 text-[var(--accent)]" />
          <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
            Your footage (optional)
          </span>
        </div>
        {video && (
          <button
            onClick={() => { onClear(); setPreviewErr(false) }}
            title="Remove — go back to AI-generated stills"
            className="p-1 rounded-md text-[var(--text-muted)] hover:text-red-300 hover:bg-red-500/10"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="video/mp4,video/quicktime,video/webm,video/x-matroska,video/x-m4v,video/mpeg"
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]
          if (f) { setPreviewErr(false); onAttach(f) }
          e.target.value = ''
        }}
      />

      {video ? (
        <>
          <div className="rounded-xl overflow-hidden border border-[var(--accent)] bg-black">
            {previewErr ? (
              <div className="aspect-video flex flex-col items-center justify-center gap-2 text-emerald-300">
                <Film className="w-8 h-8" />
                <span className="text-[10px] font-mono uppercase tracking-widest">preview unsupported · will still render</span>
              </div>
            ) : (
              <video
                src={video.url}
                controls
                preload="metadata"
                className="w-full max-h-64"
                onError={() => setPreviewErr(true)}
              />
            )}
          </div>
          <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
            <span className="truncate max-w-[70%]" title={video.name}>{video.name}</span>
            <span>{(video.bytes / 1_048_576).toFixed(1)} MB</span>
          </div>
          {video.converted && (
            <div className="text-[10px] font-mono text-emerald-300 flex items-center gap-1.5">
              <Sparkles className="w-3 h-3" />
              re-encoded to H.264 MP4 for broad compatibility
            </div>
          )}
          <p className="text-[10.5px] text-[var(--text-muted)] italic leading-snug">
            This clip is used as the background for <span className="text-[var(--text-secondary)]">every scene</span> in your plan. Per-scene attachments still override.
          </p>
        </>
      ) : (
        <button
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
          className="w-full flex flex-col items-center justify-center gap-1.5 px-4 py-6 rounded-xl border border-dashed border-[var(--border)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/40 text-[var(--text-muted)] hover:text-[var(--accent)] transition-all disabled:opacity-40"
        >
          {uploading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Upload className="w-5 h-5" />}
          <span className="text-[12px] font-mono uppercase tracking-widest">
            {uploading ? 'uploading & converting…' : 'upload video'}
          </span>
          <span className="text-[10px] font-mono lowercase text-[var(--text-muted)]/70">
            mp4 · mov · webm · mkv · ≤ 300 MB · auto-converted to mp4
          </span>
        </button>
      )}
    </Panel>
  )
}

function MusicPanel({
  music, volume, onChangeVolume, onAttach, onClear, uploading,
}: {
  music: MusicAsset | null
  volume: number
  onChangeVolume: (v: number) => void
  onAttach: (file: File) => void
  onClear:  () => void
  uploading: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <div className="pt-1 space-y-2">
      <label className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
        <Music className="w-3 h-3" />
        Background music (optional)
      </label>
      <input
        ref={inputRef}
        type="file"
        accept="audio/mpeg,audio/mp3,audio/wav,audio/aac,audio/mp4,audio/ogg,audio/flac"
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]
          if (f) onAttach(f)
          e.target.value = ''
        }}
      />
      {music ? (
        <div className="p-3 rounded-xl border border-[var(--accent)] bg-[var(--accent-dim)] space-y-2">
          <div className="flex items-center gap-2">
            <Music className="w-3.5 h-3.5 text-[var(--accent)] flex-shrink-0" />
            <span className="text-xs text-[var(--text-primary)] font-mono truncate flex-1">{music.name}</span>
            <span className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
              {(music.bytes / 1_048_576).toFixed(1)} MB
            </span>
            <button
              onClick={onClear}
              className="p-1 rounded-md text-[var(--text-muted)] hover:text-red-300"
              title="Remove music"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
          <audio src={music.url} controls className="w-full h-8" preload="none" style={{ colorScheme: 'dark' }} />
          <div className="flex items-center gap-2">
            <Volume2 className="w-3 h-3 text-[var(--text-muted)] flex-shrink-0" />
            <input
              type="range"
              min={0} max={1} step={0.02}
              value={volume}
              onChange={e => onChangeVolume(Number(e.target.value))}
              className="flex-1 accent-[var(--accent)]"
            />
            <span className="text-[10px] font-mono tabular-nums text-[var(--text-muted)] w-9 text-right">
              {Math.round(volume * 100)}%
            </span>
          </div>
          <p className="text-[10px] text-[var(--text-muted)] italic">
            Music is auto-ducked ~8× while the voiceover plays so speech stays legible.
          </p>
        </div>
      ) : (
        <button
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
          className="w-full flex flex-col items-center justify-center gap-1 px-4 py-4 rounded-xl border border-dashed border-[var(--border)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/40 text-[var(--text-muted)] hover:text-[var(--accent)] transition-all disabled:opacity-40"
        >
          {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
          <span className="text-[11px] font-mono uppercase tracking-widest">
            {uploading ? 'uploading…' : 'add background track'}
          </span>
          <span className="text-[10px] font-mono lowercase text-[var(--text-muted)]/70">
            mp3 · wav · m4a · ogg · flac · ≤ 40 MB
          </span>
        </button>
      )}
    </div>
  )
}

function RenderProgressBar({ progress, total }: { progress: RenderProgress; total: number }) {
  const stageLabel: Record<RenderProgress['stage'], string> = {
    idle:   'idle',
    image:  'generating image',
    voice:  'voicing script',
    render: 'compositing scene',
    concat: 'stitching scenes',
    music:  'mixing music',
    done:   'ready',
    error:  'error',
  }
  const stageIdx = ['image', 'voice', 'render'].indexOf(progress.stage)
  const perSceneFrac = stageIdx >= 0
    ? ((progress.scene ?? 1) - 1) / total + (stageIdx / 3) / total
    : progress.stage === 'concat' ? 0.94
    : progress.stage === 'music'  ? 0.98
    : progress.stage === 'done'   ? 1.0
    : 0
  const pct = Math.max(0, Math.min(1, perSceneFrac)) * 100

  if (progress.stage === 'error') {
    return (
      <div className="flex items-start gap-2 p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-300">
        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
        <span>{progress.error ?? 'render failed'}</span>
      </div>
    )
  }
  return (
    <div className="space-y-1.5">
      <div className="h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
        <div
          className="h-full transition-all duration-300"
          style={{
            width: `${pct}%`,
            background: 'linear-gradient(90deg, var(--accent), var(--holo))',
            boxShadow: '0 0 10px var(--accent-glow)',
          }}
        />
      </div>
      <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
        <span>{stageLabel[progress.stage]}</span>
        <span>
          {progress.scene && progress.total ? `scene ${progress.scene}/${progress.total} · ` : ''}
          {Math.round(pct)}%
        </span>
      </div>
    </div>
  )
}

function ReelCard({ reel }: { reel: Reel }) {
  const dims = reel.aspect === '9:16' ? { w: 180, h: 320 } :
               reel.aspect === '1:1'  ? { w: 260, h: 260 } : { w: 320, h: 180 }
  return (
    <div className="rounded-2xl overflow-hidden border border-[var(--border)] bg-[var(--bg-primary)]/40 hover:border-[var(--accent)] transition-all group">
      <video
        src={reel.url}
        controls
        preload="metadata"
        className="w-full bg-black"
        style={{ aspectRatio: reel.aspect.replace(':', ' / '), maxHeight: dims.h }}
      />
      <div className="p-3 space-y-1">
        <div className="text-sm text-[var(--text-primary)] leading-snug line-clamp-2">
          {reel.topic || '(untitled)'}
        </div>
        <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
          <span>{reel.aspect} · {reel.duration}s</span>
          <span>{(reel.bytes / 1_048_576).toFixed(1)} MB</span>
        </div>
      </div>
    </div>
  )
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function aspectBox(a: Aspect): { w: number; h: number } {
  switch (a) {
    case '9:16': return { w: 180, h: 320 }
    case '1:1':  return { w: 240, h: 240 }
    case '16:9': return { w: 320, h: 180 }
  }
}

function groupBy<T>(list: T[], key: (x: T) => string): Record<string, T[]> {
  const out: Record<string, T[]> = {}
  for (const item of list) {
    const k = key(item)
    ;(out[k] ??= []).push(item)
  }
  return out
}
