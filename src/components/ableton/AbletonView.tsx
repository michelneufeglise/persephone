import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import {
  Music4, CheckCircle2, AlertTriangle, Download, Loader2,
  Play, Clock, ExternalLink, Copy, RefreshCw, Wand2, Send, Square, Sparkles,
  Undo2, MessagesSquare, User, Star, Trash2, Plus, Zap,
  Save, Library, FilePlus, X,
} from 'lucide-react'
import { Panel } from '@/components/ui/Panel'

// ── Types ─────────────────────────────────────────────────────────────────────
interface AbletonInstall {
  path:          string
  name:          string
  edition:       string
  is_trial:      boolean
  version:       string
  version_major: number
  version_full:  string
  platform:      string
}

interface AbletonStatus {
  installed:       boolean
  installs:        AbletonInstall[]
  best:            AbletonInstall | null
  running:         boolean
  bridgeInstalled: boolean
  bridgeDir:       string
  connected:       boolean
  hostVersion:     string
  hostEdition:     string
  isTrial:         boolean
}

interface InstallProgress {
  stage:   'idle' | 'prep' | 'clone' | 'verify' | 'done' | 'notes' | 'error'
  message?: string
  progress?: number
  error?:  string
}

interface Section {
  id: string
  name: string
  start_bar: number
  length_bars: number
  intensity: number
}
interface TrackMix { volume_db: number; pan: number }
interface Note { pitch: number; start: number; length: number; velocity: number }
interface Clip { section: string; bars: number; notes: Note[]; pattern?: string }
interface Track {
  id: string
  role: string
  name: string
  clips: Clip[]
  mix: TrackMix
  instrument_hint: string
}
interface SongSpec {
  version: number
  bpm: number
  key: { root: string; mode: string }
  timesig: { num: number; den: number }
  bars: number
  sections: Section[]
  tracks: Track[]
  genre: string
  mood: string
  topic: string
}

interface SongMeta {
  id: string
  name: string
  created_at: number
  updated_at: number
  bpm?: number
  key?: { root: string; mode: string }
  genre?: string
  n_tracks: number
  n_sections: number
}

interface ComposeStage {
  stage: 'idle' | 'picking' | 'generating' | 'spec' | 'error'
  message?: string
  model?: string
  error?: string
}

interface InstrumentFailure {
  track:      string
  role:       string
  last_error: string
  attempts:   string[]
}

interface ApplyStage {
  stage: 'idle' | 'tempo' | 'timesig' | 'wipe' | 'track' | 'instrument' | 'browser_probe' | 'done' | 'complete' | 'error'
  message?: string
  progress?: number
  error?: string
  tracks_created?: number
  clips_created?: number
  notes_added?: number
  instruments_loaded?: number
  instruments_failed?: string[]
  instruments_failure_detail?: InstrumentFailure[]
  browser_patch?: boolean
}

interface ProbeStep { name: string; ok: boolean; detail?: string; count?: number; sample?: string[] }
interface ProbeResult { ok: boolean; steps: ProbeStep[]; error?: string }

const GENRES = ['lo-fi hip-hop', 'house', 'techno', 'ambient', 'cinematic'] as const

interface ChatTurn {
  role:      'user' | 'assistant' | 'system'
  message:   string
  reply?:    string             // assistant's chat reply
  summaries?: string[]          // human-readable op summaries
  applied?:  boolean            // did the plan get executed?
  error?:    string
  ts:        number
}

// ── Component ──────────────────────────────────────────────────────────────────
export function AbletonView() {
  const [status, setStatus]         = useState<AbletonStatus | null>(null)
  const [installing, setInstalling] = useState(false)
  const [progress, setProgress]     = useState<InstallProgress>({ stage: 'idle' })
  const [installLog, setLog]        = useState<string[]>([])
  const [notes, setNotes]           = useState<string | null>(null)
  const [copied, setCopied]         = useState(false)
  const [pinging, setPinging]       = useState(false)
  const [pingMs, setPingMs]         = useState<number | null>(null)
  const pollRef                     = useRef<number | null>(null)

  // Composer state
  const [topic, setTopic]           = useState('')
  const [genre, setGenre]           = useState<string>('lo-fi hip-hop')
  const [composing, setComposing]   = useState(false)
  const [composeStage, setComposeStage] = useState<ComposeStage>({ stage: 'idle' })
  const [spec, setSpec]             = useState<SongSpec | null>(null)
  const [applying, setApplying]     = useState(false)
  const [applyStage, setApplyStage] = useState<ApplyStage>({ stage: 'idle' })
  const [autoLoad,  setAutoLoad]    = useState(true)
  const [autoPlay,  setAutoPlay]    = useState(true)
  const [playing,   setPlaying]     = useState(false)
  // "Deep reasoning" swaps compose over to whatever the user configured as
  // their `ableton_deep_model` (default gemma4:26b) instead of the standard
  // `ableton_composer_model` (default qwen3.6:35b-a3b). The backend resolves
  // the actual name from config — the frontend just sends `deep: true`.
  const [deepReasoning, setDeepReasoning] = useState(false)
  // ── Track-first workflow state ──
  // Which tracks are currently "active" (edit chat sees them as context; LLM
  // may modify them). Multi-select. Focus track = the primary target, marked
  // with a star; a single-track-focused edit will land here first.
  const [activeTrackIds, setActiveTrackIds] = useState<Set<string>>(new Set())
  const [focusTrackId,   setFocusTrackId]   = useState<string | null>(null)
  // Tracks whose spec has diverged from what's in Ableton (compose or
  // add-track produced them, but the user hasn't hit Apply for them yet).
  // Keyed by track.id.
  const [dirtyTrackIds,  setDirtyTrackIds]  = useState<Set<string>>(new Set())
  // Which track's clip is currently soloed for preview.
  const [previewingTrackId, setPreviewingTrackId] = useState<string | null>(null)
  // Live track_index by track.id — maintained after apply so per-track
  // ops (fire-clip, apply-track, delete-track) hit the right Ableton track.
  const [liveTrackByTrackId, setLiveTrackByTrackId] = useState<Record<string, number>>({})
  // Add-track mini-composer state.
  const [showAddTrack,       setShowAddTrack]       = useState(false)
  const [addTrackRole,       setAddTrackRole]       = useState<string>('chord')
  const [addTrackDescription,setAddTrackDescription]= useState('')
  const [addingTrack,        setAddingTrack]        = useState(false)
  const [addTrackError,      setAddTrackError]      = useState<string | null>(null)
  // Per-track apply state — track.id → 'idle' | 'applying'.
  const [applyingTrackId,    setApplyingTrackId]    = useState<string | null>(null)
  // Song library UI state.
  const [showLibrary,        setShowLibrary]        = useState(false)
  const [songLibrary,        setSongLibrary]       = useState<SongMeta[]>([])
  const [libraryLoading,     setLibraryLoading]    = useState(false)
  const [showSaveInput,      setShowSaveInput]     = useState(false)
  const [saveName,           setSaveName]          = useState('')
  const [savingSong,         setSavingSong]        = useState(false)
  // Track the id of the currently-loaded saved song so re-saving updates
  // in place instead of duplicating.
  const [currentSongId,      setCurrentSongId]     = useState<string | null>(null)
  const [browserPatch, setBrowserPatch] = useState<boolean | null>(null)
  const [browserList,  setBrowserList]  = useState<{ instruments: {name:string;uri:string}[]; drums: {name:string;uri:string}[] } | null>(null)
  const [browserLoading, setBrowserLoading] = useState(false)
  const [probe, setProbe]                   = useState<ProbeResult | null>(null)
  const [probing, setProbing]               = useState(false)
  // Configured composer / deep-reasoning models pulled from Settings so the
  // music panel can show what will actually run. Empty string ⇒ backend uses
  // the built-in default (qwen3.6:35b-a3b / gemma4:26b).
  const [composerModel, setComposerModel] = useState<string>('')
  const [deepModel,     setDeepModel]     = useState<string>('')

  // Fetch role config on mount + whenever the tab is refocused, so users who
  // change their pick in Settings see it reflected here without a reload.
  useEffect(() => {
    let cancelled = false
    async function loadRoles() {
      try {
        const r = await fetch('/api/models/roles')
        const d = await r.json()
        if (cancelled) return
        setComposerModel(String(d?.ableton_composer_model ?? '').trim())
        setDeepModel(    String(d?.ableton_deep_model     ?? '').trim())
      } catch { /* silent — falls back to defaults in UI copy */ }
    }
    loadRoles()
    const onFocus = () => { void loadRoles() }
    window.addEventListener('focus', onFocus)
    return () => { cancelled = true; window.removeEventListener('focus', onFocus) }
  }, [])

  async function runBrowserProbe() {
    if (probing) return
    setProbing(true)
    setProbe(null)
    try {
      const r = await fetch('/api/ableton/browser-probe', { method: 'POST' })
      const d = await r.json()
      setProbe(d)
    } catch (exc: any) {
      setProbe({ ok: false, steps: [], error: exc?.message ?? 'probe failed' })
    } finally {
      setProbing(false)
    }
  }

  async function fetchBrowserList() {
    if (browserLoading) return
    setBrowserLoading(true)
    try {
      const r = await fetch('/api/ableton/browser-list')
      const d = await r.json()
      setBrowserList({ instruments: d.instruments ?? [], drums: d.drums ?? [] })
      setBrowserPatch(!!d.patched)
    } catch { /* silent */ }
    finally { setBrowserLoading(false) }
  }
  const composeAbort                = useRef<AbortController | null>(null)
  const applyAbort                  = useRef<AbortController | null>(null)

  // Chat / editing state
  const [turns, setTurns]           = useState<ChatTurn[]>([])
  const [chatInput, setChatInput]   = useState('')
  const [editing, setEditing]       = useState(false)
  const [undoDepth, setUndoDepth]   = useState(0)
  const [patternVocab, setPatternVocab] = useState<Record<string, string[]>>({})
  const chatBottomRef               = useRef<HTMLDivElement>(null)

  // Load persisted session on mount so a page refresh doesn't lose the working spec.
  useEffect(() => {
    fetch('/api/ableton/session')
      .then(r => r.json())
      .then(d => {
        if (d?.spec) setSpec(d.spec as SongSpec)
        if (typeof d?.undo_depth === 'number') setUndoDepth(d.undo_depth)
      })
      .catch(() => {})
    fetch('/api/ableton/patterns')
      .then(r => r.json())
      .then(d => setPatternVocab(d?.patterns ?? {}))
      .catch(() => {})
  }, [])

  // Auto-scroll chat.
  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  // ⌘Z inside the Music tab = undo the last edit.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'z' && !e.shiftKey && undoDepth > 0) {
        e.preventDefault()
        void doUndo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undoDepth])

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/ableton/status')
      const d = await r.json()
      setStatus(d)
    } catch { /* keep last state */ }
  }, [])

  useEffect(() => {
    refresh()
    // Poll while installing / waiting for user to enable in Live prefs.
    pollRef.current = window.setInterval(refresh, 3000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [refresh])

  async function runInstall() {
    if (installing) return
    setInstalling(true)
    setLog([])
    setNotes(null)
    setProgress({ stage: 'prep' })
    try {
      const res = await fetch('/api/ableton/install-bridge', { method: 'POST' })
      if (!res.body) throw new Error('no stream')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const evts = buf.split('\n\n'); buf = evts.pop() ?? ''
        for (const evt of evts) {
          const line = evt.replace(/^data:\s?/, '').trim()
          if (!line || line === '[DONE]') continue
          try {
            const c = JSON.parse(line)
            if (c.stage) setProgress(p => ({ ...p, stage: c.stage, ...(c.progress != null ? { progress: c.progress } : {}), ...(c.message ? { message: c.message } : {}) }))
            if (c.message) setLog(prev => [...prev.slice(-40), c.message])
            if (c.stage === 'notes' && c.message) setNotes(c.message)
            if (c.stage === 'error') setProgress({ stage: 'error', error: c.error ?? 'install failed' })
          } catch { /* ignore malformed */ }
        }
      }
      refresh()
    } catch (exc: any) {
      setProgress({ stage: 'error', error: exc?.message ?? 'install failed' })
    } finally {
      setInstalling(false)
    }
  }

  async function launchLive() {
    if (!status?.best) return
    try { await fetch('/api/ableton/launch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: status.best.path }) }) } catch {}
    setTimeout(refresh, 1500)
  }

  async function ping() {
    if (pinging) return
    setPinging(true); setPingMs(null)
    try {
      const r = await fetch('/api/ableton/ping', { method: 'POST' })
      const d = await r.json()
      setPingMs(d.ok ? d.latency_ms : null)
    } catch { setPingMs(null) }
    finally { setPinging(false) }
  }

  async function compose() {
    if (composing || !topic.trim()) return
    setComposing(true); setComposeStage({ stage: 'picking' }); setSpec(null)
    const ctl = new AbortController(); composeAbort.current = ctl
    try {
      const res = await fetch('/api/ableton/compose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic, genre, deep: deepReasoning }),
        signal: ctl.signal,
      })
      if (!res.body) throw new Error('no stream')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const evts = buf.split('\n\n'); buf = evts.pop() ?? ''
        for (const evt of evts) {
          const line = evt.replace(/^data:\s?/, '').trim()
          if (!line || line === '[DONE]') continue
          try {
            const c = JSON.parse(line)
            if (c.stage === 'spec' && c.spec) {
              setSpec(c.spec)
              setComposeStage({ stage: 'spec' })
              // Fresh compose = every track is unapplied until the user hits Apply.
              setDirtyTrackIds(new Set(((c.spec as SongSpec).tracks ?? []).map(t => t.id)))
              setLiveTrackByTrackId({})
              setActiveTrackIds(new Set())
              setFocusTrackId(null)
            }
            else if (c.stage === 'error') setComposeStage({ stage: 'error', error: c.error })
            else if (c.stage) setComposeStage({ stage: c.stage, message: c.message, model: c.model })
          } catch { /* ignore malformed */ }
        }
      }
    } catch (exc: any) {
      if (exc?.name !== 'AbortError') setComposeStage({ stage: 'error', error: exc?.message ?? 'compose failed' })
    } finally {
      setComposing(false); composeAbort.current = null
    }
  }

  function stopCompose() {
    composeAbort.current?.abort()
    setComposing(false)
  }

  async function applySpec() {
    if (!spec || applying) return
    setApplying(true); setApplyStage({ stage: 'wipe', progress: 0 })
    const ctl = new AbortController(); applyAbort.current = ctl
    try {
      const res = await fetch('/api/ableton/apply-song', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spec, wipe_first: true, load_instruments: autoLoad }),
        signal: ctl.signal,
      })
      if (!res.body) throw new Error('no stream')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const evts = buf.split('\n\n'); buf = evts.pop() ?? ''
        for (const evt of evts) {
          const line = evt.replace(/^data:\s?/, '').trim()
          if (!line || line === '[DONE]') continue
          try {
            const c = JSON.parse(line)
            if (c.stage === 'error') setApplyStage({ stage: 'error', error: c.error })
            else if (c.stage === 'browser_probe') {
              setBrowserPatch(!!c.browser_patch)
              setApplyStage(prev => ({ ...prev, message: c.message }))
            } else if (c.stage) setApplyStage({
              stage:    c.stage,
              message:  c.message,
              progress: c.progress,
              tracks_created:             c.tracks_created,
              clips_created:              c.clips_created,
              notes_added:                c.notes_added,
              instruments_loaded:         c.instruments_loaded,
              instruments_failed:         c.instruments_failed,
              instruments_failure_detail: c.instruments_failure_detail,
            })
          } catch { /* ignore malformed */ }
        }
      }
    } catch (exc: any) {
      if (exc?.name !== 'AbortError') setApplyStage({ stage: 'error', error: exc?.message ?? 'apply failed' })
    } finally {
      setApplying(false); applyAbort.current = null
    }

    // Whole-song apply: every track is now in sync with Ableton, and their
    // live_track_index follows their order in the spec (song_translator.apply
    // creates them sequentially starting at 0).
    if (spec) {
      const map: Record<string, number> = {}
      spec.tracks.forEach((t, i) => { map[t.id] = i })
      setLiveTrackByTrackId(map)
    }
    markAllClean()

    // Auto-fire scene 0 so the user actually hears the sketch. Ableton doesn't
    // auto-play — it just materialises the clips. Small delay so Live has time
    // to finish loading the last device.
    if (autoPlay) {
      setTimeout(() => { void playScene(0) }, 400)
    }
  }

  async function playScene(sceneIndex: number = 0) {
    try {
      await fetch('/api/ableton/fire-scene', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene_index: sceneIndex }),
      })
      setPlaying(true)
    } catch { /* silent */ }
  }

  async function stopAll() {
    try {
      await fetch('/api/ableton/stop-all', { method: 'POST' })
      setPlaying(false)
      setPreviewingTrackId(null)
    } catch { /* silent */ }
  }

  // ── Track-first handlers ──
  function toggleActive(trackId: string) {
    setActiveTrackIds(prev => {
      const next = new Set(prev)
      if (next.has(trackId)) next.delete(trackId)
      else next.add(trackId)
      return next
    })
  }

  function setFocus(trackId: string) {
    // Focus implies active — you can't focus a track you haven't marked active.
    setActiveTrackIds(prev => {
      if (prev.has(trackId)) return prev
      const next = new Set(prev)
      next.add(trackId)
      return next
    })
    setFocusTrackId(prev => prev === trackId ? null : trackId)
  }

  function markDirty(trackId: string) {
    setDirtyTrackIds(prev => {
      if (prev.has(trackId)) return prev
      const next = new Set(prev); next.add(trackId); return next
    })
  }

  function markAllClean() {
    setDirtyTrackIds(new Set())
  }

  function markClean(trackId: string) {
    setDirtyTrackIds(prev => {
      if (!prev.has(trackId)) return prev
      const next = new Set(prev); next.delete(trackId); return next
    })
  }

  // ── Song library handlers ──
  async function refreshLibrary() {
    setLibraryLoading(true)
    try {
      const r = await fetch('/api/ableton/song/library')
      const d = await r.json()
      setSongLibrary((d?.songs ?? []) as SongMeta[])
    } catch { /* silent */ }
    finally { setLibraryLoading(false) }
  }

  async function saveSong() {
    if (savingSong || !spec) return
    setSavingSong(true)
    try {
      const r = await fetch('/api/ableton/song/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: saveName || spec.topic || spec.genre || 'Untitled',
          song_id: currentSongId ?? '',
        }),
      })
      const d = await r.json()
      if (d?.song?.id) setCurrentSongId(d.song.id)
      setShowSaveInput(false)
      setSaveName('')
      // Refresh library if the user has it open so they see the new entry.
      if (showLibrary) await refreshLibrary()
    } catch { /* silent */ }
    finally { setSavingSong(false) }
  }

  async function loadSong(songId: string) {
    try {
      const r = await fetch(`/api/ableton/song/${songId}/load`, { method: 'POST' })
      const d = await r.json()
      if (d?.spec) {
        setSpec(d.spec as SongSpec)
        setCurrentSongId(songId)
        // Loaded song = "in Ableton yet? No" — mark every track dirty until Apply.
        setDirtyTrackIds(new Set(((d.spec as SongSpec).tracks ?? []).map(t => t.id)))
        setLiveTrackByTrackId({})
        setActiveTrackIds(new Set())
        setFocusTrackId(null)
        setShowLibrary(false)
      }
    } catch { /* silent */ }
  }

  async function deleteSong(songId: string, name: string) {
    if (!window.confirm(`Delete "${name}" from the library?`)) return
    try {
      await fetch(`/api/ableton/song/${songId}`, { method: 'DELETE' })
      // Optimistic — remove from local list immediately.
      setSongLibrary(prev => prev.filter(s => s.id !== songId))
      if (currentSongId === songId) setCurrentSongId(null)
    } catch { /* silent */ }
  }

  async function newSong() {
    // If the user has an in-progress song they haven't saved, warn before nuking.
    if (spec && spec.tracks?.length > 0) {
      if (!window.confirm('Start a new song? Any unsaved work here will be cleared. (Your saved Library entries are safe.)')) {
        return
      }
    }
    // Ask whether to also wipe Ableton — the user often wants a full reset.
    const wipe = window.confirm('Also delete every track in the running Ableton session?')
    try {
      await fetch('/api/ableton/song/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wipe_ableton: wipe }),
      })
    } catch { /* silent */ }
    // Reset frontend state.
    setSpec(null)
    setCurrentSongId(null)
    setDirtyTrackIds(new Set())
    setLiveTrackByTrackId({})
    setActiveTrackIds(new Set())
    setFocusTrackId(null)
    setPreviewingTrackId(null)
    setTurns([])
    setUndoDepth(0)
    setComposeStage({ stage: 'idle' })
    setApplyStage({ stage: 'idle' })
  }

  async function stopTrack(trackId: string) {
    const idx = liveTrackByTrackId[trackId]
    if (idx === undefined) return
    try {
      await fetch('/api/ableton/stop-track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_index: idx, slot_index: 0 }),
      })
      // If this was the soloed preview track, clear the visual state.
      if (previewingTrackId === trackId) setPreviewingTrackId(null)
    } catch { /* silent */ }
  }

  async function fireClipForTrack(trackId: string, ev?: React.MouseEvent) {
    // Solo-preview by default; shift+click → additive (layers with anything
    // already playing, no solo). Matches the user's chosen preview semantics.
    const solo = !(ev?.shiftKey)
    let idx  = liveTrackByTrackId[trackId]
    // If the track hasn't been applied to Ableton yet, apply it FIRST so
    // there's actually something to fire — otherwise ▶ is a silent no-op.
    if (idx === undefined) {
      await applyTrack(trackId)
      // applyTrack updated liveTrackByTrackId via its SSE `complete` event —
      // React state updates are async, but we can read the freshly-committed
      // value from the same setter closure by peeking one more time.
      idx = liveTrackByTrackId[trackId]
      // Fall back to spec position if the map still doesn't know — after
      // applyTrack ran, Live has the track appended at the end.
      if (idx === undefined && spec) {
        const specIdx = spec.tracks.findIndex(t => t.id === trackId)
        if (specIdx >= 0) idx = specIdx
      }
    }
    if (idx === undefined) return
    try {
      await fetch('/api/ableton/fire-clip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_index: idx, slot_index: 0, solo }),
      })
      setPreviewingTrackId(solo ? trackId : previewingTrackId)
      setPlaying(true)
    } catch { /* silent */ }
  }

  async function deleteTrack(trackId: string) {
    if (!spec) return
    const idx = liveTrackByTrackId[trackId]
    // 1) Remove from Ableton if it's live.
    if (idx !== undefined) {
      try {
        await fetch('/api/ableton/delete-track', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ track_index: idx }),
        })
      } catch { /* fall through — still remove from spec */ }
    }
    // 2) Remove from the local spec so the UI updates immediately.
    setSpec(prev => prev ? { ...prev, tracks: prev.tracks.filter(t => t.id !== trackId) } : prev)
    // 3) Cleanup state maps.
    setActiveTrackIds(prev => { const n = new Set(prev); n.delete(trackId); return n })
    setDirtyTrackIds(prev => { const n = new Set(prev); n.delete(trackId); return n })
    setLiveTrackByTrackId(prev => {
      const n = { ...prev }; delete n[trackId]
      // Every track after the deleted one shifts down by one.
      if (idx !== undefined) {
        for (const k of Object.keys(n)) if (n[k] > idx) n[k] -= 1
      }
      return n
    })
    setFocusTrackId(prev => prev === trackId ? null : prev)
  }

  async function applyTrack(trackId: string) {
    if (!spec || applyingTrackId) return
    setApplyingTrackId(trackId)
    try {
      const liveIdx = liveTrackByTrackId[trackId]  // may be undefined → new track
      const res = await fetch('/api/ableton/apply-track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          spec, track_id: trackId,
          live_track_index: liveIdx ?? null,
          load_instrument: autoLoad,
        }),
      })
      if (!res.body) throw new Error('no stream')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const evts = buf.split('\n\n'); buf = evts.pop() ?? ''
        for (const evt of evts) {
          const line = evt.replace(/^data:\s?/, '').trim()
          if (!line || line === '[DONE]') continue
          try {
            const c = JSON.parse(line)
            if (c.stage === 'complete') {
              markClean(trackId)
              // Remember the live index so future fire-clip / apply-track hit the same track.
              if (typeof c.track_index === 'number') {
                setLiveTrackByTrackId(prev => ({ ...prev, [trackId]: c.track_index }))
              }
            }
          } catch { /* ignore parse noise */ }
        }
      }
    } catch { /* silent */ }
    finally { setApplyingTrackId(null) }
  }

  async function applyModifiedTracks() {
    if (!spec || applying) return
    // Sequential — parallel apply-track calls fight for Ableton's audio thread.
    for (const t of spec.tracks) {
      if (dirtyTrackIds.has(t.id)) await applyTrack(t.id)
    }
    if (autoPlay) setTimeout(() => { void playScene(0) }, 400)
  }

  async function addTrackViaLLM() {
    if (addingTrack) return
    setAddingTrack(true); setAddTrackError(null)
    try {
      const res = await fetch('/api/ableton/add-track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role: addTrackRole,
          description: addTrackDescription,
          deep: deepReasoning,
        }),
      })
      if (!res.body) throw new Error('no stream')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let addedTrackId: string | null = null
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const evts = buf.split('\n\n'); buf = evts.pop() ?? ''
        for (const evt of evts) {
          const line = evt.replace(/^data:\s?/, '').trim()
          if (!line || line === '[DONE]') continue
          try {
            const c = JSON.parse(line)
            if (c.stage === 'track' && c.track) {
              addedTrackId = String(c.track.id ?? '')
              // Use the updated full spec echoed by the backend so ids stay coherent.
              if (c.spec) setSpec(c.spec)
              else if (spec) setSpec({ ...spec, tracks: [...spec.tracks, c.track] })
            } else if (c.stage === 'error') {
              setAddTrackError(c.error ?? 'add-track failed')
            }
          } catch { /* ignore */ }
        }
      }
      if (addedTrackId) {
        markDirty(addedTrackId)
        // Auto-focus the new track so the user can immediately edit it.
        setActiveTrackIds(prev => { const n = new Set(prev); n.add(addedTrackId!); return n })
        setFocusTrackId(addedTrackId)
        setShowAddTrack(false)
        setAddTrackDescription('')
      }
    } catch (e: any) {
      setAddTrackError(e?.message ?? 'add-track failed')
    } finally {
      setAddingTrack(false)
    }
  }

  async function sendChat() {
    const msg = chatInput.trim()
    if (!msg || editing) return
    setChatInput('')
    const now = Date.now()
    setTurns(prev => [...prev, { role: 'user', message: msg, ts: now }])
    setEditing(true)
    try {
      // 1) LLM emits an EditPlan.
      const res = await fetch('/api/ableton/edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: msg,
          active_track_ids: Array.from(activeTrackIds),
          focus_track_id:   focusTrackId ?? '',
        }),
      })
      if (!res.body) throw new Error('no stream')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let plan: any = null
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const evts = buf.split('\n\n'); buf = evts.pop() ?? ''
        for (const evt of evts) {
          const line = evt.replace(/^data:\s?/, '').trim()
          if (!line || line === '[DONE]') continue
          try {
            const c = JSON.parse(line)
            if (c.stage === 'plan') plan = c
            else if (c.stage === 'error') {
              setTurns(prev => [...prev, { role: 'assistant', message: '', error: c.error, ts: Date.now() }])
              return
            }
          } catch { /* ignore */ }
        }
      }
      if (!plan) return

      // 2) If there are changes, apply them.  Empty-changes = pure chat reply.
      let applied = false
      if (Array.isArray(plan.changes) && plan.changes.length > 0) {
        const applyRes = await fetch('/api/ableton/apply-edit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ plan: { reply: plan.reply, changes: plan.changes } }),
        })
        // Drain SSE just to know when to stop; UI updates come from the summaries.
        if (applyRes.body) {
          const r2 = applyRes.body.getReader()
          const d2 = new TextDecoder()
          let b2 = ''
          while (true) {
            const { done, value } = await r2.read()
            if (done) break
            b2 += d2.decode(value, { stream: true })
            const evts = b2.split('\n\n'); b2 = evts.pop() ?? ''
            for (const evt of evts) {
              const line = evt.replace(/^data:\s?/, '').trim()
              if (!line || line === '[DONE]') continue
              try {
                const c = JSON.parse(line)
                if (c.stage === 'complete') applied = true
                if (c.stage === 'error') throw new Error(c.error)
                if (typeof c.undo_depth === 'number') setUndoDepth(c.undo_depth)
              } catch { /* ignore */ }
            }
          }
        }
      } else {
        // Pure chat reply — nothing to undo.
        applied = true
      }

      setTurns(prev => [...prev, {
        role: 'assistant', message: '', ts: Date.now(),
        reply: plan.reply, summaries: plan.summaries, applied,
      }])
      // Refresh current spec from session so the SongSpecCard reflects edits.
      const s = await (await fetch('/api/ableton/session')).json()
      if (s?.spec) setSpec(s.spec as SongSpec)
      if (typeof s?.undo_depth === 'number') setUndoDepth(s.undo_depth)
    } catch (exc: any) {
      setTurns(prev => [...prev, { role: 'assistant', message: '', error: exc?.message ?? 'edit failed', ts: Date.now() }])
    } finally {
      setEditing(false)
    }
  }

  async function setClipPattern(trackIndex: number, sectionId: string, pattern: string) {
    try {
      await fetch('/api/ableton/set-pattern', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_index: trackIndex, section_id: sectionId, pattern }),
      })
      const s = await (await fetch('/api/ableton/session')).json()
      if (s?.spec) setSpec(s.spec as SongSpec)
      if (typeof s?.undo_depth === 'number') setUndoDepth(s.undo_depth)
    } catch { /* silent */ }
  }

  async function doUndo() {
    if (undoDepth === 0) return
    try {
      const r = await fetch('/api/ableton/undo', { method: 'POST' })
      const d = await r.json()
      setUndoDepth(d.remaining ?? 0)
      const s = await (await fetch('/api/ableton/session')).json()
      if (s?.spec) setSpec(s.spec as SongSpec)
      setTurns(prev => [...prev, { role: 'system', message: `Undid the last edit (${d.applied} op${d.applied === 1 ? '' : 's'} reversed).`, ts: Date.now() }])
    } catch { /* silent */ }
  }

  const copyNotes = async () => {
    if (!notes) return
    try { await navigator.clipboard.writeText(notes); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch {}
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* ── Header ───────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div
            className="w-11 h-11 rounded-2xl flex items-center justify-center"
            style={{
              background: 'radial-gradient(circle at 30% 25%, rgba(255,255,255,0.35), transparent 40%), conic-gradient(from 200deg at 50% 50%, var(--accent), var(--holo), var(--accent))',
              boxShadow:  '0 0 20px var(--accent-glow), inset 0 -3px 6px rgba(0,0,0,0.3)',
            }}
          >
            <Music4 className="w-5 h-5 text-white" />
          </div>
          <div>
            <h2 className="font-display text-2xl text-[var(--text-primary)] leading-none">Music</h2>
            <p className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)] mt-1">
              ableton composer · phase 1 · bridge shell
            </p>
          </div>
        </div>
        <StatusChip status={status} onRefresh={refresh} />
      </div>

      {/* ── Body ─────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto pr-1 space-y-4" style={{ scrollbarWidth: 'thin' }}>
        {/* Detection card */}
        <Panel className="p-5 space-y-4">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Detected installs</span>
          </div>
          {status === null ? (
            <div className="flex items-center gap-2 text-[var(--text-muted)] text-sm">
              <Loader2 className="w-4 h-4 animate-spin" /> probing…
            </div>
          ) : !status.installed ? (
            <div className="flex items-start gap-3 p-3 rounded-xl border border-amber-500/40 bg-amber-500/10 text-amber-100">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div className="text-xs leading-snug">
                No Ableton Live install found. Get Live from{' '}
                <a href="https://www.ableton.com/en/products/live/" target="_blank" rel="noreferrer" className="underline">
                  ableton.com <ExternalLink className="w-3 h-3 inline" />
                </a>. The Intro edition works for this integration.
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              {status.installs.map((i, idx) => (
                <InstallRow key={i.path} install={i} isPreferred={idx === 0 && !!status.best && i.path === status.best.path} />
              ))}
            </div>
          )}
          {status?.isTrial && (
            <div className="flex items-start gap-2 p-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 text-[11px] text-amber-200/90">
              <Clock className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
              <span>Only the Trial edition is available — sessions cap at 90 minutes and Save is disabled. Consider installing Live Intro (free with an account) for uninterrupted use.</span>
            </div>
          )}
        </Panel>

        {/* Bridge card */}
        <Panel className="p-5 space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">AbletonOSC bridge</span>
            {status?.bridgeInstalled && (
              <span className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-widest text-emerald-300">
                <CheckCircle2 className="w-3 h-3" /> installed
              </span>
            )}
          </div>

          {!status?.bridgeInstalled ? (
            <>
              <p className="text-sm text-[var(--text-secondary)] leading-snug">
                Persephone talks to Live through <a href="https://github.com/ideoforms/AbletonOSC" target="_blank" rel="noreferrer" className="underline decoration-dotted">AbletonOSC</a> — a Python control-surface script that runs inside Live and exposes the Live Object Model over OSC. One-time install (~1 s clone), then a one-time toggle in Live's preferences.
              </p>
              <button
                onClick={runInstall}
                disabled={installing || !status?.installed}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium bg-gradient-to-r from-[var(--accent)] to-[var(--holo)] text-white shadow-lg shadow-[var(--accent-glow)] disabled:from-[var(--bg-tertiary)] disabled:to-[var(--bg-tertiary)] disabled:text-[var(--text-muted)] disabled:shadow-none"
              >
                {installing ? <><Loader2 className="w-4 h-4 animate-spin" /> installing…</>
                            : <><Download className="w-4 h-4" /> Install bridge</>}
              </button>
            </>
          ) : (
            <div className="space-y-2">
              <div className="text-[11px] font-mono text-[var(--text-muted)] break-all">{status.bridgeDir}</div>
              <div className="flex items-center gap-2">
                <button
                  onClick={runInstall}
                  disabled={installing}
                  title="Re-install from upstream (overwrites the local copy)"
                  className="px-3 py-1.5 rounded-lg text-[10.5px] font-mono uppercase tracking-widest text-[var(--text-muted)] hover:text-[var(--accent)] border border-[var(--border)] hover:border-[var(--accent)] transition-colors flex items-center gap-1.5"
                >
                  {installing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                  re-install
                </button>
              </div>
            </div>
          )}

          {(installing || progress.stage === 'error') && (
            <>
              <ProgressBar progress={progress} />
              {installLog.length > 0 && (
                <div className="max-h-32 overflow-y-auto rounded-lg border border-[var(--border)] bg-black/40 p-2 font-mono text-[10.5px] leading-tight text-[var(--text-muted)] space-y-0.5">
                  {installLog.slice(-30).map((l, i) => <div key={i} className="truncate">{l}</div>)}
                </div>
              )}
              {progress.stage === 'error' && (
                <div className="flex items-start gap-2 p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-300">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
                  <span>{progress.error}</span>
                </div>
              )}
            </>
          )}

          {notes && (
            <div className="rounded-xl border border-[var(--accent)]/40 bg-[var(--accent-dim)] p-4 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-mono uppercase tracking-widest text-[var(--accent)]">One last step — inside Ableton</span>
                <button onClick={copyNotes} title="Copy instructions"
                        className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-mono uppercase tracking-widest text-[var(--accent)] hover:bg-[var(--accent-dim)]">
                  <Copy className="w-3 h-3" />{copied ? 'copied' : 'copy'}
                </button>
              </div>
              <pre className="whitespace-pre-wrap text-[11.5px] leading-snug text-[var(--text-primary)] font-mono">{notes}</pre>
            </div>
          )}
        </Panel>

        {/* Launch + Ping card */}
        <Panel className="p-5 space-y-4">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Runtime</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={launchLive}
              disabled={!status?.best}
              title={status?.best ? `Open ${status.best.name}` : 'No install to launch'}
              className="flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-sm font-medium border border-[var(--border)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)] text-[var(--text-primary)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Play className="w-4 h-4" />
              {status?.running ? 'Live is running' : 'Launch Live'}
            </button>
            <button
              onClick={ping}
              disabled={!status?.running || !status?.bridgeInstalled || pinging}
              title={
                !status?.running       ? 'Live must be running'
                : !status.bridgeInstalled ? 'Bridge must be installed'
                : 'Send an OSC ping to the bridge'
              }
              className="flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-sm font-medium border border-[var(--border)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)] text-[var(--text-primary)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {pinging ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {pingMs !== null ? `ping · ${pingMs} ms` : 'ping bridge'}
            </button>
          </div>

          {/* Browser-patch diagnostic — helps debug auto-load misses. */}
          <div className="pt-2 border-t border-[var(--border)]/60 space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={fetchBrowserList}
                disabled={!status?.connected || browserLoading}
                className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-[10.5px] font-mono uppercase tracking-widest border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                title="List what Live's browser reports"
              >
                {browserLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                browse
              </button>
              <button
                onClick={runBrowserProbe}
                disabled={!status?.connected || probing}
                className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-[10.5px] font-mono uppercase tracking-widest border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                title="Run an end-to-end auto-load probe on track 0"
              >
                {probing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <AlertTriangle className="w-3.5 h-3.5" />}
                diagnose
              </button>
            </div>
            {probe && <ProbeReport result={probe} />}
            {browserList && (
              <div className="space-y-2 text-[11px]">
                {(browserList.instruments.length === 0 && browserList.drums.length === 0) ? (
                  <div className="p-2.5 rounded-lg border border-amber-500/40 bg-amber-500/10 text-amber-200 leading-snug">
                    Live returned nothing — the browser patch didn't load. Re-install the bridge (button above), then <span className="font-mono text-[10.5px]">Cmd+Q</span> Ableton completely and reopen.
                  </div>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    <BrowserList title="Instruments" items={browserList.instruments} />
                    <BrowserList title="Drums"       items={browserList.drums} />
                  </div>
                )}
              </div>
            )}
          </div>
        </Panel>

        {/* Composer card (only when the bridge is connected) */}
        {status?.connected ? (
          <>
            <ComposerPanel
              topic={topic} setTopic={setTopic}
              genre={genre} setGenre={setGenre}
              composing={composing} composeStage={composeStage}
              spec={spec}
              onCompose={compose} onStopCompose={stopCompose}
              applying={applying} applyStage={applyStage}
              onApply={applySpec}
              onApplyModified={applyModifiedTracks}
              autoLoad={autoLoad} setAutoLoad={setAutoLoad}
              autoPlay={autoPlay} setAutoPlay={setAutoPlay}
              deepReasoning={deepReasoning} setDeepReasoning={setDeepReasoning}
              composerModel={composerModel}
              deepModel={deepModel}
              onNewSong={newSong}
              onSaveSong={saveSong}
              savingSong={savingSong}
              showSaveInput={showSaveInput} setShowSaveInput={setShowSaveInput}
              saveName={saveName} setSaveName={setSaveName}
              currentSongId={currentSongId}
              showLibrary={showLibrary}
              onToggleLibrary={async () => {
                const next = !showLibrary
                setShowLibrary(next)
                if (next) await refreshLibrary()
              }}
              songLibrary={songLibrary}
              libraryLoading={libraryLoading}
              onLoadSong={loadSong}
              onDeleteSong={deleteSong}
              onRefreshLibrary={refreshLibrary}
              playing={playing}
              onPlay={() => playScene(0)}
              onStop={stopAll}
              browserPatch={browserPatch}
              patternVocab={patternVocab}
              onPatternChange={setClipPattern}
              activeTrackIds={activeTrackIds}
              focusTrackId={focusTrackId}
              dirtyTrackIds={dirtyTrackIds}
              previewingTrackId={previewingTrackId}
              onToggleActive={toggleActive}
              onSetFocus={setFocus}
              onFireClip={fireClipForTrack}
              onStopClip={stopTrack}
              onApplyTrack={applyTrack}
              onDeleteTrack={deleteTrack}
              applyingTrackId={applyingTrackId}
              showAddTrack={showAddTrack}
              setShowAddTrack={setShowAddTrack}
              addTrackRole={addTrackRole}
              setAddTrackRole={setAddTrackRole}
              addTrackDescription={addTrackDescription}
              setAddTrackDescription={setAddTrackDescription}
              addingTrack={addingTrack}
              addTrackError={addTrackError}
              onAddTrack={addTrackViaLLM}
            />
            {spec && (
              <EditChatPanel
                turns={turns}
                input={chatInput} setInput={setChatInput}
                editing={editing}
                onSend={sendChat}
                undoDepth={undoDepth}
                onUndo={doUndo}
                bottomRef={chatBottomRef}
              />
            )}
          </>
        ) : (
          <Panel className="p-5 space-y-2 border-dashed">
            <div className="flex items-center gap-2">
              <Music4 className="w-3.5 h-3.5 text-[var(--accent)]" />
              <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Composer</span>
            </div>
            <p className="text-[13px] text-[var(--text-secondary)] leading-snug">
              The composer unlocks once the bridge is <span className="text-emerald-300">connected</span>. Install the bridge, restart Live, and enable AbletonOSC in the Control Surface dropdown — then this card turns into your chat with the composer.
            </p>
          </Panel>
        )}
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────
function StatusChip({ status, onRefresh }: { status: AbletonStatus | null; onRefresh: () => void }) {
  if (status === null) {
    return (
      <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] flex items-center gap-1.5">
        <Loader2 className="w-3 h-3 animate-spin" /> probing…
      </div>
    )
  }
  const { installed, running, bridgeInstalled, connected, hostEdition, hostVersion } = status

  let variant: 'success' | 'warn' | 'neutral' = 'neutral'
  let label = 'not installed'
  if (connected)                { variant = 'success'; label = 'connected' }
  else if (running && bridgeInstalled) { variant = 'warn'; label = 'toggle in prefs' }
  else if (running)             { variant = 'warn'; label = 'live is up · bridge missing' }
  else if (installed)           { variant = 'neutral'; label = 'live not running' }

  const colours = variant === 'success'
    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
    : variant === 'warn'
    ? 'border-amber-500/40 bg-amber-500/10 text-amber-300'
    : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-muted)]'

  return (
    <button
      onClick={onRefresh}
      title="Re-probe status"
      className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-[10px] font-mono uppercase tracking-widest ${colours} hover:brightness-110 transition-all`}
    >
      <span className={`w-2 h-2 rounded-full ${variant === 'success' ? 'bg-emerald-400 shadow-[0_0_6px_2px_rgba(52,211,153,0.6)]' : variant === 'warn' ? 'bg-amber-400' : 'bg-[var(--text-muted)]'}`} />
      ableton · {label}
      {installed && hostEdition && (
        <span className="opacity-70">· {hostEdition} {hostVersion}</span>
      )}
    </button>
  )
}

function InstallRow({ install, isPreferred }: { install: AbletonInstall; isPreferred: boolean }) {
  return (
    <div className={`flex items-center gap-3 p-3 rounded-xl border ${isPreferred ? 'border-[var(--accent)]' : 'border-[var(--border)]'} bg-[var(--bg-primary)]/40`}>
      <Music4 className={`w-4 h-4 flex-shrink-0 ${isPreferred ? 'text-[var(--accent)]' : 'text-[var(--text-muted)]'}`} />
      <div className="flex-1 min-w-0">
        <div className="text-sm text-[var(--text-primary)] leading-tight">
          {install.name}
          {isPreferred && <span className="ml-2 text-[9px] font-mono uppercase tracking-widest text-[var(--accent)]">preferred</span>}
          {install.is_trial && <span className="ml-2 text-[9px] font-mono uppercase tracking-widest text-amber-300">trial</span>}
        </div>
        <div className="text-[10.5px] font-mono text-[var(--text-muted)] mt-0.5">
          {install.edition} · v{install.version} · {install.path}
        </div>
      </div>
    </div>
  )
}

function ProgressBar({ progress }: { progress: InstallProgress }) {
  const stageLabel: Record<InstallProgress['stage'], string> = {
    idle:   'idle',
    prep:   'preparing directory',
    clone:  'cloning AbletonOSC',
    verify: 'verifying install',
    done:   'installed',
    notes:  'installed',
    error:  'error',
  }
  const pct = Math.max(0, Math.min(1, Number(progress.progress ?? 0))) * 100
  return (
    <div className="space-y-1.5">
      <div className="h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
        <div
          className="h-full transition-all duration-300"
          style={{
            width: `${pct}%`,
            background: 'linear-gradient(90deg, var(--accent), var(--holo))',
            boxShadow:  '0 0 10px var(--accent-glow)',
          }}
        />
      </div>
      <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
        <span className="truncate">{progress.message || stageLabel[progress.stage]}</span>
        <span className="tabular-nums">{Math.round(pct)}%</span>
      </div>
    </div>
  )
}

function ProbeReport({ result }: { result: ProbeResult }) {
  const allOk = result.ok && result.steps.every(s => s.ok)
  return (
    <div className={`p-2.5 rounded-lg border text-[11px] space-y-1.5 ${
      allOk ? 'border-emerald-500/30 bg-emerald-500/[0.05]'
            : 'border-amber-500/40 bg-amber-500/[0.03]'
    }`}>
      <div className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-widest">
        {allOk
          ? <><CheckCircle2 className="w-3 h-3 text-emerald-300" /><span className="text-emerald-300">Auto-load ready</span></>
          : <><AlertTriangle className="w-3 h-3 text-amber-300" /><span className="text-amber-300">Diagnostic</span></>
        }
      </div>
      {result.error && (
        <div className="text-[11px] text-amber-200/80 leading-snug">{result.error}</div>
      )}
      <div className="space-y-0.5">
        {result.steps.map((s, i) => (
          <div key={i} className="flex items-start gap-1.5">
            <span className={s.ok ? 'text-emerald-300' : 'text-amber-300'}>{s.ok ? '✓' : '✗'}</span>
            <div className="flex-1 min-w-0">
              <div className="font-mono text-[10.5px] text-[var(--text-secondary)] break-all">{s.name}</div>
              {s.detail && (
                <div className="text-[10px] font-mono text-[var(--text-muted)] break-all">→ {s.detail}</div>
              )}
              {typeof s.count === 'number' && (
                <div className="text-[10px] font-mono text-[var(--text-muted)]">
                  {s.count} items{s.sample && s.sample.length ? ` · ${s.sample.slice(0, 4).join(', ')}${s.sample.length > 4 ? '…' : ''}` : ''}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function BrowserList({ title, items }: { title: string; items: { name: string; uri: string }[] }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-primary)]/40 overflow-hidden">
      <div className="px-2.5 py-1.5 border-b border-[var(--border)] text-[9.5px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
        {title} · {items.length}
      </div>
      <div className="max-h-40 overflow-y-auto p-1.5 space-y-0.5" style={{ scrollbarWidth: 'thin' }}>
        {items.length === 0
          ? <div className="text-[10.5px] italic text-[var(--text-muted)] p-1">(empty)</div>
          : items.map(it => (
              <div key={it.uri} title={it.uri} className="text-[10.5px] font-mono text-[var(--text-secondary)] px-1.5 py-0.5 rounded hover:bg-[var(--accent-dim)] truncate">
                {it.name}
              </div>
            ))
        }
      </div>
    </div>
  )
}

// ── Composer panel ────────────────────────────────────────────────────────────
function ComposerPanel({
  topic, setTopic, genre, setGenre,
  composing, composeStage, spec,
  onCompose, onStopCompose,
  applying, applyStage, onApply, onApplyModified,
  autoLoad, setAutoLoad,
  autoPlay, setAutoPlay,
  deepReasoning, setDeepReasoning,
  composerModel, deepModel,
  onNewSong, onSaveSong, savingSong,
  showSaveInput, setShowSaveInput,
  saveName, setSaveName,
  currentSongId,
  showLibrary, onToggleLibrary,
  songLibrary, libraryLoading,
  onLoadSong, onDeleteSong, onRefreshLibrary,
  playing, onPlay, onStop,
  browserPatch,
  patternVocab, onPatternChange,
  activeTrackIds, focusTrackId, dirtyTrackIds, previewingTrackId,
  onToggleActive, onSetFocus, onFireClip, onStopClip, onApplyTrack, onDeleteTrack,
  applyingTrackId,
  showAddTrack, setShowAddTrack,
  addTrackRole, setAddTrackRole,
  addTrackDescription, setAddTrackDescription,
  addingTrack, addTrackError, onAddTrack,
}: {
  topic: string; setTopic: (s: string) => void
  genre: string; setGenre: (s: string) => void
  composing: boolean; composeStage: ComposeStage; spec: SongSpec | null
  onCompose: () => void; onStopCompose: () => void
  applying: boolean; applyStage: ApplyStage; onApply: () => void
  onApplyModified: () => void
  autoLoad: boolean; setAutoLoad: (v: boolean) => void
  autoPlay: boolean; setAutoPlay: (v: boolean) => void
  deepReasoning: boolean; setDeepReasoning: (v: boolean) => void
  composerModel: string; deepModel: string
  onNewSong:  () => void
  onSaveSong: () => void
  savingSong: boolean
  showSaveInput: boolean;  setShowSaveInput: (v: boolean) => void
  saveName: string;        setSaveName: (v: string) => void
  currentSongId: string | null
  showLibrary: boolean;    onToggleLibrary: () => void
  songLibrary: SongMeta[]; libraryLoading: boolean
  onLoadSong:   (id: string) => void
  onDeleteSong: (id: string, name: string) => void
  onRefreshLibrary: () => void
  playing: boolean
  onPlay: () => void
  onStop: () => void
  browserPatch: boolean | null
  patternVocab: Record<string, string[]>
  onPatternChange: (trackIndex: number, sectionId: string, pattern: string) => void
  activeTrackIds: Set<string>
  focusTrackId: string | null
  dirtyTrackIds: Set<string>
  previewingTrackId: string | null
  onToggleActive: (id: string) => void
  onSetFocus:     (id: string) => void
  onFireClip:     (id: string, ev?: React.MouseEvent) => void
  onStopClip:     (id: string) => void
  onApplyTrack:   (id: string) => void
  onDeleteTrack:  (id: string) => void
  applyingTrackId: string | null
  showAddTrack: boolean; setShowAddTrack: (v: boolean) => void
  addTrackRole: string;  setAddTrackRole: (v: string) => void
  addTrackDescription: string; setAddTrackDescription: (v: string) => void
  addingTrack: boolean; addTrackError: string | null
  onAddTrack: () => void
}) {
  // Human labels shown throughout the panel. Empty config → hardcoded default
  // (must match server/ableton_composer.py's `_PLANNER_PREF[0]` and
  // `_DEEP_PLANNER_PREF[0]`, kept in sync intentionally).
  const composerModelLabel = composerModel || 'qwen3.6:35b-a3b'
  const deepModelLabel     = deepModel     || 'gemma4:26b'
  const activeModelLabel   = deepReasoning ? deepModelLabel : composerModelLabel
  return (
    <div className="space-y-4">
      <Panel className="p-5 space-y-4">
        <div className="flex items-center gap-2">
          <Wand2 className="w-3.5 h-3.5 text-[var(--accent)]" />
          <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Composer</span>
          <div className="ml-auto flex items-center gap-1.5 text-[10px] font-mono text-[var(--text-muted)]" title="Configured in Settings → Model Roles">
            <span className={deepReasoning ? 'text-[var(--text-muted)]/60' : 'text-[var(--accent)]'}>
              {composerModelLabel}
            </span>
            <span>/</span>
            <span className={deepReasoning ? 'text-amber-300' : 'text-[var(--text-muted)]/60'}>
              {deepModelLabel}
            </span>
          </div>
        </div>

        {/* Song controls — new / save / library */}
        <div className="grid grid-cols-3 gap-2">
          <button
            onClick={onNewSong}
            title="Start a new empty song (optionally wipe Ableton too)"
            className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-[var(--border)] text-sm text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/20 transition-colors"
          >
            <FilePlus className="w-4 h-4" /> new
          </button>
          <button
            onClick={() => { setShowSaveInput(!showSaveInput); if (showLibrary) onToggleLibrary() }}
            disabled={!spec}
            title={spec ? 'Save current song to the library' : 'Compose a song first'}
            className={`flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border text-sm transition-colors ${
              showSaveInput
                ? 'border-[var(--accent)] bg-[var(--accent-dim)]/20 text-[var(--accent)]'
                : 'border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/20'
            } disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            <Save className="w-4 h-4" /> {currentSongId ? 'update' : 'save'}
          </button>
          <button
            onClick={() => { if (showSaveInput) setShowSaveInput(false); onToggleLibrary() }}
            title="Browse and load saved songs"
            className={`flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border text-sm transition-colors ${
              showLibrary
                ? 'border-[var(--accent)] bg-[var(--accent-dim)]/20 text-[var(--accent)]'
                : 'border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/20'
            }`}
          >
            <Library className="w-4 h-4" /> library
          </button>
        </div>

        {/* Inline save input */}
        {showSaveInput && (
          <div className="rounded-lg border border-[var(--accent)]/60 bg-[var(--accent-dim)]/10 p-3 space-y-2.5">
            <label className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--accent)]">
              {currentSongId ? 'Update saved song' : 'Save as'}
            </label>
            <input
              type="text"
              value={saveName}
              onChange={e => setSaveName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); onSaveSong() } }}
              placeholder={spec?.topic || spec?.genre || 'Song name…'}
              autoFocus
              className="w-full bg-[var(--bg-primary)]/60 border border-[var(--border)] rounded-lg px-2.5 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[var(--accent)]"
            />
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => { setShowSaveInput(false); setSaveName('') }}
                disabled={savingSong}
                className="px-3 py-2 rounded-lg border border-[var(--border)] text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] disabled:opacity-40"
              >
                cancel
              </button>
              <button
                onClick={onSaveSong}
                disabled={savingSong}
                className="px-3 py-2 rounded-lg bg-gradient-to-r from-[var(--accent)] to-[var(--holo)] text-white font-medium text-sm disabled:opacity-50 flex items-center justify-center gap-1.5"
              >
                {savingSong
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> saving</>
                  : <><Save className="w-3.5 h-3.5" /> {currentSongId ? 'update' : 'save'}</>}
              </button>
            </div>
          </div>
        )}

        {/* Inline library browser */}
        {showLibrary && (
          <div className="rounded-lg border border-[var(--accent)]/60 bg-[var(--accent-dim)]/10 p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Library className="w-3.5 h-3.5 text-[var(--accent)]" />
              <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--accent)]">Library</span>
              <div className="ml-auto text-[10px] font-mono text-[var(--text-muted)]">
                {libraryLoading ? 'loading…' : `${songLibrary.length} song${songLibrary.length === 1 ? '' : 's'}`}
              </div>
              <button
                onClick={onRefreshLibrary}
                title="Refresh"
                className="w-6 h-6 rounded flex items-center justify-center text-[var(--text-muted)] hover:text-[var(--accent)]"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${libraryLoading ? 'animate-spin' : ''}`} />
              </button>
              <button
                onClick={onToggleLibrary}
                title="Close"
                className="w-6 h-6 rounded flex items-center justify-center text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            {songLibrary.length === 0 && !libraryLoading ? (
              <div className="text-[11px] text-[var(--text-muted)] italic py-4 text-center">
                No saved songs yet — compose one and hit Save.
              </div>
            ) : (
              <div className="space-y-1 max-h-64 overflow-y-auto pr-1">
                {songLibrary.map(s => {
                  const isCurrent = s.id === currentSongId
                  const d = new Date((s.updated_at || s.created_at || 0) * 1000)
                  const dateStr = isNaN(d.getTime())
                    ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
                  return (
                    <div
                      key={s.id}
                      className={`flex items-center gap-2 rounded-md border p-2 transition-colors ${
                        isCurrent
                          ? 'border-[var(--accent)] bg-[var(--accent-dim)]/20'
                          : 'border-[var(--border)] bg-[var(--bg-primary)]/40 hover:border-[var(--border-bright)]'
                      }`}
                    >
                      <button
                        onClick={() => onLoadSong(s.id)}
                        className="flex-1 min-w-0 text-left"
                        title="Load this song (does not push to Ableton until you hit Apply)"
                      >
                        <div className="flex items-center gap-1.5">
                          <div className="text-sm text-[var(--text-primary)] truncate">{s.name}</div>
                          {isCurrent && <span className="text-[9.5px] font-mono uppercase tracking-wider text-[var(--accent)]">current</span>}
                        </div>
                        <div className="flex items-center gap-2 text-[10px] font-mono text-[var(--text-muted)] mt-0.5">
                          {s.bpm && <span>{Math.round(s.bpm)} BPM</span>}
                          {s.key && <span>{s.key.root} {s.key.mode}</span>}
                          {s.genre && <span className="truncate">{s.genre}</span>}
                          <span>·</span>
                          <span>{s.n_tracks} tr</span>
                          {dateStr && <><span>·</span><span>{dateStr}</span></>}
                        </div>
                      </button>
                      <button
                        onClick={() => onDeleteSong(s.id, s.name)}
                        title="Delete this song from the library"
                        className="w-7 h-7 rounded flex-shrink-0 flex items-center justify-center border border-[var(--border)] text-[var(--text-muted)] hover:text-red-300 hover:border-red-500/50 transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        <div>
          <label className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Topic</label>
          <textarea
            value={topic}
            onChange={e => setTopic(e.target.value)}
            onKeyDown={e => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); onCompose() }
            }}
            rows={2}
            placeholder="e.g. rainy Sunday morning, warm rhodes, boom-bap kit"
            className="w-full mt-2 bg-[var(--bg-primary)]/60 border border-[var(--border)] rounded-xl px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[var(--accent)] resize-none font-display"
          />
        </div>

        <div>
          <label className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Genre</label>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {GENRES.map(g => (
              <button
                key={g}
                onClick={() => setGenre(g)}
                className={`px-3 py-1.5 rounded-full text-[11px] font-mono uppercase tracking-wider border transition-all ${
                  genre === g
                    ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)] shadow-inner'
                    : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)] hover:text-[var(--text-secondary)]'
                }`}
              >
                {g}
              </button>
            ))}
          </div>
        </div>

        {/* Deep-reasoning toggle — swaps compose model to deepseek-r1:70b */}
        <label className="flex items-center justify-between gap-3 p-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-primary)]/40 cursor-pointer select-none">
          <div className="flex items-center gap-2.5 flex-1 min-w-0">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors flex-shrink-0 ${
              deepReasoning ? 'bg-[var(--accent-dim)] text-[var(--accent)]' : 'bg-[var(--bg-tertiary)] text-[var(--text-muted)]'
            }`}>
              <Sparkles className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="text-sm text-[var(--text-primary)] leading-none">Deep reasoning</div>
              <div className="text-[10.5px] text-[var(--text-muted)] mt-1 leading-snug">
                Uses <span className="font-mono text-amber-300/90">{deepModelLabel}</span> instead of{' '}
                <span className="font-mono text-[var(--accent)]">{composerModelLabel}</span> — slower,
                but reasons about section arrangement, dynamic contour, chord choice.
                Change either in Settings → Model Roles.
              </div>
            </div>
          </div>
          <input type="checkbox" checked={deepReasoning} onChange={e => setDeepReasoning(e.target.checked)} className="sr-only" />
          <span className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
            deepReasoning ? 'bg-[var(--accent)]' : 'bg-[var(--bg-tertiary)]'
          }`}>
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
              deepReasoning ? 'translate-x-4' : ''
            }`} />
          </span>
        </label>

        <button
          onClick={composing ? onStopCompose : onCompose}
          disabled={!topic.trim() && !composing}
          className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all ${
            composing
              ? 'bg-[var(--accent-dim)] text-[var(--accent)] border border-[var(--accent)]'
              : 'bg-gradient-to-r from-[var(--accent)] to-[var(--holo)] text-white shadow-lg shadow-[var(--accent-glow)] disabled:from-[var(--bg-tertiary)] disabled:to-[var(--bg-tertiary)] disabled:text-[var(--text-muted)] disabled:shadow-none'
          }`}
        >
          {composing
            ? <><Square className="w-4 h-4" /> stop</>
            : <><Send className="w-4 h-4" /> compose</>}
        </button>

        {composing && (
          <div className="flex items-center gap-2 text-[10.5px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
            <Loader2 className="w-3 h-3 animate-spin" />
            {composeStage.stage === 'picking'   ? 'choosing model…' :
             composeStage.stage === 'generating' ? `generating with ${composeStage.model ?? '…'}` :
             'thinking…'}
          </div>
        )}
        {composeStage.stage === 'error' && (
          <div className="flex items-start gap-2 p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-300">
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
            <span>{composeStage.error}</span>
          </div>
        )}
      </Panel>

      {/* SongSpec preview */}
      {spec && (
        <SongSpecCard
          spec={spec}
          onApply={onApply}
          onApplyModified={onApplyModified}
          applying={applying}
          applyStage={applyStage}
          autoLoad={autoLoad}
          setAutoLoad={setAutoLoad}
          autoPlay={autoPlay}
          setAutoPlay={setAutoPlay}
          playing={playing}
          onPlay={onPlay}
          onStop={onStop}
          browserPatch={browserPatch}
          patternVocab={patternVocab}
          onPatternChange={onPatternChange}
          activeTrackIds={activeTrackIds}
          focusTrackId={focusTrackId}
          dirtyTrackIds={dirtyTrackIds}
          previewingTrackId={previewingTrackId}
          onToggleActive={onToggleActive}
          onSetFocus={onSetFocus}
          onFireClip={onFireClip}
          onStopClip={onStopClip}
          onApplyTrack={onApplyTrack}
          onDeleteTrack={onDeleteTrack}
          applyingTrackId={applyingTrackId}
          showAddTrack={showAddTrack}
          setShowAddTrack={setShowAddTrack}
          addTrackRole={addTrackRole}
          setAddTrackRole={setAddTrackRole}
          addTrackDescription={addTrackDescription}
          setAddTrackDescription={setAddTrackDescription}
          addingTrack={addingTrack}
          addTrackError={addTrackError}
          onAddTrack={onAddTrack}
          deepReasoning={deepReasoning}
          activeModelLabel={activeModelLabel}
        />
      )}
    </div>
  )
}

function SongSpecCard({
  spec, onApply, onApplyModified, applying, applyStage,
  autoLoad, setAutoLoad,
  autoPlay, setAutoPlay,
  playing, onPlay, onStop,
  browserPatch,
  patternVocab, onPatternChange,
  activeTrackIds, focusTrackId, dirtyTrackIds, previewingTrackId,
  onToggleActive, onSetFocus, onFireClip, onStopClip, onApplyTrack, onDeleteTrack,
  applyingTrackId,
  showAddTrack, setShowAddTrack,
  addTrackRole, setAddTrackRole,
  addTrackDescription, setAddTrackDescription,
  addingTrack, addTrackError, onAddTrack,
  deepReasoning, activeModelLabel,
}: {
  spec: SongSpec
  onApply: () => void
  onApplyModified: () => void
  applying: boolean
  applyStage: ApplyStage
  autoLoad: boolean
  setAutoLoad: (v: boolean) => void
  autoPlay: boolean
  setAutoPlay: (v: boolean) => void
  playing: boolean
  onPlay: () => void
  onStop: () => void
  browserPatch: boolean | null
  patternVocab: Record<string, string[]>
  onPatternChange: (trackIndex: number, sectionId: string, pattern: string) => void
  activeTrackIds: Set<string>
  focusTrackId: string | null
  dirtyTrackIds: Set<string>
  previewingTrackId: string | null
  onToggleActive: (id: string) => void
  onSetFocus:     (id: string) => void
  onFireClip:     (id: string, ev?: React.MouseEvent) => void
  onStopClip:     (id: string) => void
  onApplyTrack:   (id: string) => void
  onDeleteTrack:  (id: string) => void
  applyingTrackId: string | null
  showAddTrack: boolean; setShowAddTrack: (v: boolean) => void
  addTrackRole: string;  setAddTrackRole: (v: string) => void
  addTrackDescription: string; setAddTrackDescription: (v: string) => void
  addingTrack: boolean; addTrackError: string | null
  onAddTrack: () => void
  deepReasoning: boolean
  activeModelLabel: string
}) {
  const totalNotes = spec.tracks.reduce(
    (sum, t) => sum + t.clips.reduce((s, c) => s + (c.notes?.length ?? 0), 0), 0,
  )
  const totalClips = spec.tracks.reduce((sum, t) => sum + t.clips.length, 0)

  return (
    <Panel className="p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles className="w-3.5 h-3.5 text-[var(--accent)]" />
          <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Song plan</span>
        </div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
          {spec.tracks.length} tracks · {totalClips} clips · {totalNotes} notes
        </div>
      </div>

      {/* Top-line summary */}
      <div className="flex flex-wrap gap-2">
        {[
          `${Math.round(spec.bpm)} BPM`,
          `${spec.key.root} ${spec.key.mode}`,
          `${spec.timesig.num}/${spec.timesig.den}`,
          spec.genre || 'genre?',
        ].map((chip, i) => (
          <span key={i} className="px-2.5 py-1 rounded-full text-[10.5px] font-mono uppercase tracking-widest border border-[var(--border)] bg-[var(--bg-primary)]/40 text-[var(--text-secondary)]">
            {chip}
          </span>
        ))}
      </div>

      {/* Sections timeline */}
      {spec.sections.length > 0 && (
        <div>
          <div className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)] mb-1.5">Sections</div>
          <div className="flex items-stretch rounded-lg overflow-hidden border border-[var(--border)]">
            {spec.sections.map((sec, i) => (
              <div
                key={sec.id}
                className="flex-1 flex flex-col items-center justify-center py-2 border-r last:border-r-0 border-[var(--border)] bg-[var(--bg-primary)]/40"
                style={{ flexGrow: Math.max(1, sec.length_bars) }}
                title={`${sec.name} · bars ${sec.start_bar} → ${sec.start_bar + sec.length_bars}`}
              >
                <span className="text-[11px] text-[var(--text-primary)] uppercase font-mono tracking-wider">{sec.name}</span>
                <span className="text-[9px] font-mono text-[var(--text-muted)]">{sec.length_bars} bars</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Track list — each track is fully interactive: active + focus + preview + apply + delete */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <div className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Tracks</div>
          <div className="text-[9.5px] font-mono tabular-nums text-[var(--text-muted)]">
            {activeTrackIds.size} active · {dirtyTrackIds.size} unapplied
          </div>
        </div>
        {spec.tracks.map((t, trackIdx) => {
          const noteCount  = t.clips.reduce((s, c) => s + (c.notes?.length ?? 0), 0)
          const vocab      = patternVocab[t.role] ?? []
          const isActive   = activeTrackIds.has(t.id)
          const isFocus    = focusTrackId === t.id
          const isDirty    = dirtyTrackIds.has(t.id)
          const isPreview  = previewingTrackId === t.id
          const isApplying = applyingTrackId === t.id
          return (
            <div
              key={t.id}
              className={`rounded-lg border p-2.5 space-y-1.5 transition-colors ${
                isFocus  ? 'border-[var(--accent)] bg-[var(--accent-dim)]/20'
                : isActive ? 'border-[var(--accent)]/60 bg-[var(--bg-primary)]/50'
                : 'border-[var(--border)] bg-[var(--bg-primary)]/40'
              }`}
            >
              <div className="flex items-center gap-2">
                {/* Active checkbox */}
                <button
                  onClick={() => onToggleActive(t.id)}
                  title={isActive ? 'Deactivate (edit chat will ignore)' : 'Activate (edit chat will target this track)'}
                  className={`w-5 h-5 rounded border flex-shrink-0 flex items-center justify-center transition-colors ${
                    isActive ? 'bg-[var(--accent)] border-[var(--accent)] text-black'
                             : 'border-[var(--border)] hover:border-[var(--accent)]'
                  }`}
                >
                  {isActive && <CheckCircle2 className="w-3.5 h-3.5" strokeWidth={3} />}
                </button>

                {/* Focus star */}
                <button
                  onClick={() => onSetFocus(t.id)}
                  title={isFocus ? 'Unset focus' : 'Set as primary edit target'}
                  className={`w-5 h-5 rounded flex-shrink-0 flex items-center justify-center transition-colors ${
                    isFocus ? 'text-amber-300' : 'text-[var(--text-muted)] hover:text-amber-300/70'
                  }`}
                >
                  <Star className="w-3.5 h-3.5" fill={isFocus ? 'currentColor' : 'none'} />
                </button>

                <span className="w-14 text-[10px] font-mono uppercase tracking-widest text-[var(--accent)]">{t.role}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <div className="text-sm text-[var(--text-primary)] leading-none truncate">{t.name}</div>
                    {isDirty && (
                      <span title="unapplied changes" className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
                    )}
                  </div>
                  {t.instrument_hint && (
                    <div className="text-[10.5px] text-[var(--text-muted)] italic mt-0.5 truncate">{t.instrument_hint}</div>
                  )}
                </div>
                <span className="hidden xl:inline text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] whitespace-nowrap">
                  {t.clips.length}cl · {noteCount}nt
                </span>

                {/* ▶ Preview (click = solo, shift+click = additive) */}
                <button
                  onClick={ev => onFireClip(t.id, ev)}
                  title="Preview — click for solo, shift+click to layer additive"
                  className={`w-7 h-7 rounded flex-shrink-0 flex items-center justify-center border transition-colors ${
                    isPreview
                      ? 'border-emerald-500 bg-emerald-500/20 text-emerald-300'
                      : 'border-[var(--border)] text-[var(--text-muted)] hover:text-emerald-300 hover:border-emerald-500/50'
                  }`}
                >
                  <Play className="w-3.5 h-3.5" />
                </button>

                {/* ■ Stop this track's clip + un-solo (doesn't touch other tracks) */}
                <button
                  onClick={() => onStopClip(t.id)}
                  title="Stop this track's clip"
                  className="w-7 h-7 rounded flex-shrink-0 flex items-center justify-center border border-[var(--border)] text-[var(--text-muted)] hover:text-red-300 hover:border-red-500/50 transition-colors"
                >
                  <Square className="w-3.5 h-3.5" />
                </button>

                {/* Apply this track only */}
                <button
                  onClick={() => onApplyTrack(t.id)}
                  disabled={isApplying || applying}
                  title={isDirty ? 'Apply this track to Ableton' : 'No changes to apply'}
                  className={`w-7 h-7 rounded flex-shrink-0 flex items-center justify-center border transition-colors ${
                    isDirty
                      ? 'border-amber-400/50 bg-amber-400/10 text-amber-300 hover:bg-amber-400/20'
                      : 'border-[var(--border)] text-[var(--text-muted)]/40'
                  } disabled:opacity-50`}
                >
                  {isApplying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
                </button>

                {/* Delete */}
                <button
                  onClick={() => {
                    if (window.confirm(`Delete track "${t.name}"?`)) onDeleteTrack(t.id)
                  }}
                  title="Delete this track"
                  className="w-7 h-7 rounded flex-shrink-0 flex items-center justify-center border border-[var(--border)] text-[var(--text-muted)] hover:text-red-300 hover:border-red-500/50 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>

              {/* Per-clip pattern chips */}
              {t.clips.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pl-[68px]">
                  {t.clips.map((c, i) => (
                    <ClipPatternChip
                      key={`${c.section}-${i}`}
                      section={c.section}
                      pattern={c.pattern ?? ''}
                      vocab={vocab}
                      onChange={next => onPatternChange(trackIdx, c.section, next)}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}

        {/* + Add track button + mini-composer */}
        {!showAddTrack ? (
          <button
            onClick={() => setShowAddTrack(true)}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-dashed border-[var(--border)] text-[var(--text-muted)] text-sm hover:text-[var(--accent)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/20 transition-colors"
          >
            <Plus className="w-4 h-4" /> add track
          </button>
        ) : (
          <div className="rounded-lg border border-[var(--accent)]/60 bg-[var(--accent-dim)]/10 p-3 space-y-2.5">
            <div className="flex items-center gap-2 mb-1">
              <Plus className="w-3.5 h-3.5 text-[var(--accent)]" />
              <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--accent)]">New track</span>
              <div className="ml-auto text-[10px] font-mono text-[var(--text-muted)]" title="Model comes from the composer configured in Settings → Model Roles">
                {addingTrack
                  ? <>composing on <span className={deepReasoning ? 'text-amber-300' : 'text-[var(--accent)]'}>{activeModelLabel}</span></>
                  : <>via <span className={deepReasoning ? 'text-amber-300' : 'text-[var(--accent)]'}>{activeModelLabel}</span></>}
              </div>
            </div>
            <div>
              <label className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Role</label>
              <div className="flex flex-wrap gap-1.5 mt-2">
                {(['drums','bass','chord','lead','pad','fx','vox'] as const).map(r => (
                  <button
                    key={r}
                    onClick={() => setAddTrackRole(r)}
                    disabled={addingTrack}
                    className={`px-2.5 py-1 rounded-full text-[10.5px] font-mono uppercase tracking-wider border transition-all ${
                      addTrackRole === r
                        ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                        : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]'
                    }`}
                  >
                    {r}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Describe the sound</label>
              <textarea
                value={addTrackDescription}
                onChange={e => setAddTrackDescription(e.target.value)}
                disabled={addingTrack}
                onKeyDown={e => {
                  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); onAddTrack() }
                }}
                rows={2}
                placeholder="e.g. warm rhodes chords with subtle vibrato, sparse in verses, fuller in chorus"
                className="w-full mt-1.5 bg-[var(--bg-primary)]/60 border border-[var(--border)] rounded-lg px-2.5 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[var(--accent)] resize-none"
              />
            </div>
            {addTrackError && (
              <div className="text-[11px] text-red-300 flex items-start gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" /> {addTrackError}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => { setShowAddTrack(false); setAddTrackDescription('') }}
                disabled={addingTrack}
                className="px-3 py-2 rounded-lg border border-[var(--border)] text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] disabled:opacity-40"
              >
                cancel
              </button>
              <button
                onClick={onAddTrack}
                disabled={addingTrack}
                className="px-3 py-2 rounded-lg bg-gradient-to-r from-[var(--accent)] to-[var(--holo)] text-white font-medium text-sm disabled:opacity-50 flex items-center justify-center gap-1.5"
              >
                {addingTrack
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> composing</>
                  : <><Sparkles className="w-3.5 h-3.5" /> compose track</>}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Auto-load toggle */}
      <label className="flex items-center justify-between gap-3 p-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-primary)]/40 cursor-pointer select-none">
        <div className="flex items-center gap-2.5 flex-1 min-w-0">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors flex-shrink-0 ${
            autoLoad ? 'bg-[var(--accent-dim)] text-[var(--accent)]' : 'bg-[var(--bg-tertiary)] text-[var(--text-muted)]'
          }`}>
            <Music4 className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <div className="text-sm text-[var(--text-primary)] leading-none">Auto-load instruments</div>
            <div className="text-[10.5px] text-[var(--text-muted)] mt-1 leading-snug">
              {browserPatch === false
                ? <span className="text-amber-300">Browser patch not loaded — will be silent. Re-install the bridge, restart Live.</span>
                : browserPatch === true
                ? 'Persephone patch detected. Wavetable / Drum Rack / Simpler for common roles.'
                : 'Wavetable / Drum Rack / Simpler for common roles.'}
            </div>
          </div>
        </div>
        <input type="checkbox" checked={autoLoad} onChange={e => setAutoLoad(e.target.checked)} className="sr-only" />
        <span className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
          autoLoad ? 'bg-[var(--accent)]' : 'bg-[var(--bg-tertiary)]'
        }`}>
          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
            autoLoad ? 'translate-x-4' : ''
          }`} />
        </span>
      </label>

      {/* Auto-play toggle */}
      <label className="flex items-center justify-between gap-3 p-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-primary)]/40 cursor-pointer select-none">
        <div className="flex items-center gap-2.5 flex-1 min-w-0">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors flex-shrink-0 ${
            autoPlay ? 'bg-[var(--accent-dim)] text-[var(--accent)]' : 'bg-[var(--bg-tertiary)] text-[var(--text-muted)]'
          }`}>
            <Play className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <div className="text-sm text-[var(--text-primary)] leading-none">Auto-play after apply</div>
            <div className="text-[10.5px] text-[var(--text-muted)] mt-1 leading-snug">
              Fires the first Session scene automatically — Ableton doesn't auto-play on its own.
            </div>
          </div>
        </div>
        <input type="checkbox" checked={autoPlay} onChange={e => setAutoPlay(e.target.checked)} className="sr-only" />
        <span className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
          autoPlay ? 'bg-[var(--accent)]' : 'bg-[var(--bg-tertiary)]'
        }`}>
          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
            autoPlay ? 'translate-x-4' : ''
          }`} />
        </span>
      </label>

      {/* Apply Modified | Apply All + Play + Stop */}
      <div className="grid grid-cols-[1fr_1fr_auto_auto] gap-2">
        <button
          onClick={onApplyModified}
          disabled={applying || applyingTrackId !== null || dirtyTrackIds.size === 0}
          title={dirtyTrackIds.size === 0
            ? 'Nothing to apply — all tracks are in sync'
            : `Apply ${dirtyTrackIds.size} modified track${dirtyTrackIds.size === 1 ? '' : 's'} without wiping the session`}
          className={`flex items-center justify-center gap-1.5 px-3 py-2.5 rounded-xl text-sm font-medium transition-all ${
            dirtyTrackIds.size > 0 && !applying
              ? 'bg-amber-400/15 border border-amber-400/50 text-amber-200 hover:bg-amber-400/25'
              : 'bg-[var(--bg-primary)]/40 border border-[var(--border)] text-[var(--text-muted)]'
          } disabled:opacity-40`}
        >
          {applyingTrackId ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
          apply modified{dirtyTrackIds.size > 0 ? ` (${dirtyTrackIds.size})` : ''}
        </button>
        <button
          onClick={onApply}
          disabled={applying}
          title="Wipe everything and rebuild the entire song"
          className={`flex items-center justify-center gap-1.5 px-3 py-2.5 rounded-xl text-sm font-medium transition-all ${
            applying
              ? 'bg-[var(--accent-dim)] text-[var(--accent)] border border-[var(--accent)]'
              : 'bg-gradient-to-r from-emerald-500 to-emerald-400 text-black shadow-lg shadow-emerald-500/30'
          }`}
        >
          {applying
            ? <><Loader2 className="w-4 h-4 animate-spin" /> materialising…</>
            : <><Sparkles className="w-4 h-4" /> apply all</>}
        </button>
        <button
          onClick={onPlay}
          disabled={applying}
          title="Fire the top Session-view scene"
          className="flex items-center justify-center gap-1.5 px-3 py-2.5 rounded-xl text-sm font-medium border border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-40"
        >
          <Play className="w-4 h-4" />
          play
        </button>
        <button
          onClick={onStop}
          disabled={applying}
          title="Stop all playback + running clips + un-solo everything"
          className="flex items-center justify-center gap-1.5 px-3 py-2.5 rounded-xl text-sm font-medium border border-[var(--border)] text-[var(--text-muted)] hover:text-red-300 hover:border-red-500/40 hover:bg-red-500/10 disabled:opacity-40"
        >
          <Square className="w-4 h-4" />
          stop
        </button>
      </div>

      {(applying || applyStage.stage === 'error' || applyStage.stage === 'complete') && (
        <ApplyProgressBar stage={applyStage} totalTracks={spec.tracks.length} />
      )}

      {applyStage.stage === 'complete' && (
        <AddSoundCard
          spec={spec}
          instrumentsLoaded={applyStage.instruments_loaded}
          instrumentsFailed={applyStage.instruments_failed}
          failureDetail={applyStage.instruments_failure_detail}
          browserPatch={browserPatch}
          autoLoad={autoLoad}
        />
      )}
    </Panel>
  )
}

// ── "No sound?" helper — role → default Live Intro instrument mapping ───────
// Live Intro's built-in instrument set is small but complete:
//   Wavetable · Drum Rack · Impulse · Simpler
// (No Operator / Analog / Electric — those need Suite.)
const INSTRUMENT_HINTS: Record<string, { device: string; hint: string }> = {
  drums:  { device: 'Drum Rack',  hint: 'Drag any factory kit (e.g. "Kit-Core 909", "Kit-Core 707").' },
  bass:   { device: 'Wavetable',  hint: 'Load Wavetable → try preset "Bass ▸ Sub Bass" or "Bass ▸ Rounded".' },
  chord:  { device: 'Wavetable',  hint: 'Load Wavetable → try preset "Keys ▸ Warm Rhodes" or "Pad ▸ Airy".' },
  lead:   { device: 'Wavetable',  hint: 'Load Wavetable → try preset "Lead ▸ Bright" or any Pluck preset.' },
  pad:    { device: 'Wavetable',  hint: 'Load Wavetable → try preset "Pad ▸ Soft" or "Ambient ▸ Wash".' },
  fx:     { device: 'Wavetable',  hint: 'Load Wavetable → any FX preset, or a Simpler with a Noise sample.' },
  vox:    { device: 'Simpler',    hint: 'Drop a vocal sample onto Simpler and set MIDI mode.' },
}

function AddSoundCard({
  spec, instrumentsLoaded, instrumentsFailed, failureDetail, browserPatch, autoLoad,
}: {
  spec: SongSpec
  instrumentsLoaded?: number
  instrumentsFailed?: string[]
  failureDetail?: InstrumentFailure[]
  browserPatch: boolean | null
  autoLoad: boolean
}) {
  const failedNames = new Set(instrumentsFailed ?? [])
  const failedTracks = spec.tracks.filter(t => failedNames.has(t.name))
  const totalTracks  = spec.tracks.length
  const loadedCount  = instrumentsLoaded ?? 0

  // Case A: auto-load was on AND everything worked — pure success banner.
  if (autoLoad && browserPatch !== false && failedTracks.length === 0 && loadedCount > 0) {
    return (
      <div className="flex items-center gap-3 p-3.5 rounded-xl border-2 border-emerald-500/40 bg-emerald-500/[0.06] text-sm text-emerald-200">
        <CheckCircle2 className="w-5 h-5 text-emerald-300 flex-shrink-0" />
        <div className="flex-1 min-w-0 leading-snug">
          <div className="text-[var(--text-primary)] font-medium">Instruments auto-loaded on {loadedCount} track{loadedCount === 1 ? '' : 's'}.</div>
          <div className="text-[11px] text-emerald-200/70 mt-0.5">Hit Play on the top scene in Ableton's Session view — every clip fires together.</div>
        </div>
      </div>
    )
  }

  // Case B: auto-load intentionally off, or browser patch missing.
  const title = !autoLoad
    ? "Auto-load is off — instruments need to be added manually."
    : browserPatch === false
    ? "Browser patch not loaded — instruments need to be added manually."
    : `Auto-loaded ${loadedCount}/${totalTracks} track${totalTracks === 1 ? '' : 's'} — the rest need manual instruments.`

  const targets = failedTracks.length > 0 ? failedTracks : spec.tracks

  return (
    <Panel className="p-5 space-y-3 border-2 border-amber-500/40 bg-amber-500/[0.03]">
      <div className="flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-amber-300" />
        <span className="text-[11px] font-mono uppercase tracking-[0.24em] text-amber-200">{title}</span>
      </div>
      {browserPatch === false && (
        <p className="text-[12px] text-amber-200/90 leading-snug bg-amber-500/[0.05] border border-amber-500/20 rounded-lg p-2.5">
          Reinstall the AbletonOSC bridge from the Music tab's <span className="font-mono">re-install</span> button — the Persephone browser patch will be applied on top. Then fully quit + re-open Ableton so the new module loads.
        </p>
      )}

      {/* Per-track failure detail (what OSC actually said) */}
      {failureDetail && failureDetail.length > 0 && (
        <details className="text-[11px] rounded-lg border border-amber-500/20 bg-amber-500/[0.03] p-2.5" open>
          <summary className="cursor-pointer text-amber-200/90 font-mono uppercase tracking-widest text-[10px]">
            what Ableton actually said
          </summary>
          <div className="mt-2 space-y-2">
            {failureDetail.map((f, i) => (
              <div key={i} className="pl-3 border-l border-amber-500/40 space-y-1">
                <div className="text-[12px] text-amber-100">
                  <span className="font-mono text-[10px] uppercase tracking-widest text-amber-300/80 mr-1.5">{f.role}</span>
                  <span className="font-medium">{f.track}</span>
                  <span className="text-amber-200/80"> — {f.last_error || 'unknown error'}</span>
                </div>
                {f.attempts && f.attempts.length > 0 && (
                  <ul className="text-[10px] font-mono text-amber-200/60 space-y-0.5 pl-3">
                    {f.attempts.map((a, j) => (
                      <li key={j}>· {a}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
      <p className="text-[12.5px] text-[var(--text-secondary)] leading-snug">
        Open Ableton's <span className="font-mono text-[11.5px] text-[var(--accent)]">Browser</span> (Cmd+Opt+B). Under <span className="font-mono text-[11.5px] text-[var(--accent)]">Categories → Instruments</span>, drag one instrument onto each track:
      </p>
      <div className="space-y-1.5 pt-1">
        {targets.map(t => {
          const hint = INSTRUMENT_HINTS[t.role] ?? INSTRUMENT_HINTS.chord
          return (
            <div key={t.id} className="flex items-start gap-3 p-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-primary)]/40">
              <span className="w-14 text-[10px] font-mono uppercase tracking-widest text-[var(--accent)] flex-shrink-0 pt-0.5">{t.role}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-[var(--text-primary)] leading-tight">
                  <span className="text-[var(--text-muted)]">{t.name} → drop </span>
                  <span className="font-mono text-[12px] text-emerald-300">{hint.device}</span>
                </div>
                <div className="text-[11px] text-[var(--text-muted)] italic mt-0.5 leading-snug">{hint.hint}</div>
              </div>
            </div>
          )
        })}
      </div>
    </Panel>
  )
}

// ── Iterative-editing chat ────────────────────────────────────────────────────
function EditChatPanel({
  turns, input, setInput, editing, onSend, undoDepth, onUndo, bottomRef,
}: {
  turns: ChatTurn[]
  input: string
  setInput: (s: string) => void
  editing: boolean
  onSend: () => void
  undoDepth: number
  onUndo: () => void
  bottomRef: React.RefObject<HTMLDivElement>
}) {
  return (
    <Panel className="p-4 space-y-3 flex flex-col" style={{ minHeight: 240 }}>
      <div className="flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-2">
          <MessagesSquare className="w-3.5 h-3.5 text-[var(--accent)]" />
          <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
            Iterate with the composer
          </span>
        </div>
        <button
          onClick={onUndo}
          disabled={undoDepth === 0}
          title={undoDepth ? `Undo last edit (⌘Z) — ${undoDepth} in stack` : 'Nothing to undo'}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] hover:text-[var(--accent)] border border-[var(--border)] hover:border-[var(--accent)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          <Undo2 className="w-3 h-3" />
          undo {undoDepth > 0 && <span className="opacity-70">({undoDepth})</span>}
        </button>
      </div>

      {/* Turn log */}
      <div className="flex-1 overflow-y-auto space-y-2 pr-1" style={{ maxHeight: 320, scrollbarWidth: 'thin' }}>
        {turns.length === 0 ? (
          <p className="text-[11.5px] italic text-[var(--text-muted)] px-1 py-2">
            Ask the composer to tweak the sketch — e.g. <span className="text-[var(--text-secondary)]">"make the bass a bit darker"</span>, <span className="text-[var(--text-secondary)]">"transpose the pad up an octave"</span>, <span className="text-[var(--text-secondary)]">"drop the tempo to 78"</span>.
          </p>
        ) : (
          turns.map((t, i) => <TurnRow key={i} turn={t} />)
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex items-end gap-2 flex-shrink-0">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend() }
          }}
          rows={1}
          placeholder={editing ? 'thinking…' : 'ask the composer for a change… (Enter to send)'}
          disabled={editing}
          className="flex-1 bg-[var(--bg-primary)]/60 border border-[var(--border)] rounded-xl px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[var(--accent)] resize-none font-display disabled:opacity-60"
          style={{ maxHeight: 96 }}
        />
        <button
          onClick={onSend}
          disabled={!input.trim() || editing}
          className={`flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium transition-all ${
            editing
              ? 'bg-[var(--accent-dim)] text-[var(--accent)] border border-[var(--accent)]'
              : 'bg-gradient-to-r from-[var(--accent)] to-[var(--holo)] text-white shadow-lg shadow-[var(--accent-glow)] disabled:from-[var(--bg-tertiary)] disabled:to-[var(--bg-tertiary)] disabled:text-[var(--text-muted)] disabled:shadow-none'
          }`}
        >
          {editing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
        </button>
      </div>
    </Panel>
  )
}

function TurnRow({ turn }: { turn: ChatTurn }) {
  if (turn.role === 'system') {
    return (
      <div className="text-[10.5px] font-mono uppercase tracking-widest text-[var(--text-muted)] text-center py-1">
        {turn.message}
      </div>
    )
  }
  if (turn.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] px-3 py-2 rounded-2xl rounded-tr-md text-sm text-[var(--text-primary)] bg-[var(--accent-dim)] border border-[var(--accent)]/40">
          {turn.message}
        </div>
      </div>
    )
  }
  // assistant
  if (turn.error) {
    return (
      <div className="flex items-start gap-2 p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-300">
        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
        <span>{turn.error}</span>
      </div>
    )
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] px-3 py-2 rounded-2xl rounded-tl-md text-sm text-[var(--text-primary)] bg-[var(--bg-tertiary)]/60 border border-[var(--border)] space-y-1.5">
        {turn.reply && <div className="leading-snug">{turn.reply}</div>}
        {turn.summaries && turn.summaries.length > 0 && (
          <ul className="space-y-0.5 pt-0.5">
            {turn.summaries.map((s, i) => (
              <li key={i} className="text-[11px] font-mono text-emerald-300/90">
                <CheckCircle2 className="w-3 h-3 inline mr-1.5 -mt-0.5" />{s}
              </li>
            ))}
          </ul>
        )}
        {(!turn.reply && (!turn.summaries || turn.summaries.length === 0)) && (
          <div className="text-[11px] italic text-[var(--text-muted)]">(no changes)</div>
        )}
      </div>
    </div>
  )
}

function ClipPatternChip({
  section, pattern, vocab, onChange,
}: {
  section: string
  pattern:  string
  vocab:    string[]
  onChange: (next: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)

  const label = pattern || '(default)'
  const handlePick = async (next: string) => {
    setOpen(false)
    if (next === pattern) return
    setSaving(true)
    try { await onChange(next) } finally { setSaving(false) }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        disabled={saving || vocab.length === 0}
        title={vocab.length === 0 ? 'No pattern vocabulary for this role' : 'Change pattern for this clip'}
        className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[10px] font-mono uppercase tracking-widest transition-all ${
          pattern
            ? 'border-[var(--accent)]/50 bg-[var(--accent-dim)] text-[var(--accent)] hover:bg-[var(--accent-dim)]/70'
            : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)] hover:text-[var(--text-secondary)]'
        } disabled:opacity-40 disabled:cursor-not-allowed`}
      >
        <span className="opacity-70">{section}</span>
        <span className="opacity-40">·</span>
        {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <span>{label}</span>}
      </button>
      {open && vocab.length > 0 && (
        <div className="absolute top-full left-0 mt-1 z-10 min-w-[180px] max-h-64 overflow-y-auto rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] shadow-lg py-1"
             style={{ scrollbarWidth: 'thin' }}>
          <button
            onClick={() => handlePick('')}
            className={`w-full text-left px-3 py-1 text-[11px] font-mono transition-colors ${pattern === '' ? 'text-[var(--accent)] bg-[var(--accent-dim)]' : 'text-[var(--text-muted)] hover:bg-[var(--bg-tertiary)]/50'}`}
          >
            (default)
          </button>
          {vocab.map(name => (
            <button
              key={name}
              onClick={() => handlePick(name)}
              className={`w-full text-left px-3 py-1 text-[11px] font-mono transition-colors ${
                name === pattern
                  ? 'text-[var(--accent)] bg-[var(--accent-dim)]'
                  : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]/50'
              }`}
            >
              {name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function ApplyProgressBar({ stage, totalTracks }: { stage: ApplyStage; totalTracks: number }) {
  if (stage.stage === 'error') {
    return (
      <div className="flex items-start gap-2 p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-xs text-red-300">
        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
        <span>{stage.error}</span>
      </div>
    )
  }
  if (stage.stage === 'complete') {
    return (
      <div className="flex items-center gap-2 p-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-sm text-emerald-300">
        <CheckCircle2 className="w-4 h-4" />
        Materialised {stage.tracks_created} tracks · {stage.clips_created} clips · {stage.notes_added} notes. Play it in Ableton.
      </div>
    )
  }
  const labels: Record<ApplyStage['stage'], string> = {
    idle:           'idle',
    tempo:          'setting tempo',
    timesig:        'setting time signature',
    wipe:           'clearing existing tracks',
    track:          stage.message ?? 'adding tracks',
    instrument:     stage.message ?? 'loading instrument',
    browser_probe:  'probing browser patch',
    done:           'ready',
    complete:       'ready',
    error:          'error',
  }
  const pct = Math.max(0, Math.min(1, stage.progress ?? 0)) * 100
  return (
    <div className="space-y-1.5">
      <div className="h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
        <div
          className="h-full transition-all duration-300"
          style={{
            width: `${pct}%`,
            background: 'linear-gradient(90deg, var(--accent), var(--holo))',
            boxShadow:  '0 0 10px var(--accent-glow)',
          }}
        />
      </div>
      <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)]">
        <span className="truncate">{labels[stage.stage]}</span>
        <span className="tabular-nums">
          {stage.stage === 'track' && totalTracks > 0
            ? `${Math.min(totalTracks, Math.round(pct * totalTracks / 100))}/${totalTracks}`
            : `${Math.round(pct)}%`}
        </span>
      </div>
    </div>
  )
}
