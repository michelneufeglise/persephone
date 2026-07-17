# Persephone × Ableton — AI Music Composer Blueprint

*Status: proposal. No code yet.*
*Owner-to-be: `src/components/ableton/` (frontend) · `server/ableton_*.py` (backend) · `server/ableton_remote/` (Live remote script bundle).*

## 1. What this document is

A phased plan for making Persephone drive Ableton Live as an AI-native music composer. Type "make me a lo-fi hip-hop beat at 85 BPM in E minor with rhodes and vinyl crackle" and Ableton opens, creates tracks, loads instruments, generates MIDI clips, and starts playing. Iterate in chat: "make the bassline more syncopated", "add a rise into the drop", "swap the drums for something lazier" — every prompt turns into concrete edits on the live Ableton project.

The rest of this doc explains **why AbletonOSC**, what each phase ships, what the LLM is really doing under the hood, and where the sharp edges are.

## 2. Guiding principles

1. **Real edits on a real project.** Not a Persephone-side simulation that exports MIDI. The user sees notes appear in Ableton, hits play, and hears them — because that's what music-making feels like.
2. **Local-first.** No cloud generation for the core loop. Music-model-in-the-cloud (e.g. Suno, MusicGen via fal.ai) can be an *opt-in* premium tier — never required.
3. **The LLM is a composer, not a DSP engine.** It writes structured JSON describing musical intent (chord progressions, drum grids, arrangement); a translator layer turns that JSON into concrete OSC commands. Never ship raw ffmpeg-level DSP instructions from the LLM.
4. **Every AI edit is reversible.** Every LLM-issued change goes through an internal undo stack — one "chat message = one undo entry" so users can "no, undo the last drum change" and get back exactly where they were.
5. **Show, then apply.** For non-trivial changes (delete track, swap instrument), show the plan → user approves → we apply. Same pattern that works for Ornith Coder.
6. **Intro-compatible.** The MVP works on Live 12 Intro (no Max for Live, limited instruments). Advanced features that need Suite/M4L are opt-in and gated behind an edition check.

## 3. The one big architectural decision

**AbletonOSC** ([github.com/ideoforms/AbletonOSC](https://github.com/ideoforms/AbletonOSC)) is the correct integration surface. Alternatives I considered and rejected:

| Approach | Verdict |
|---|---|
| **AbletonOSC** (LOM-over-OSC via a Live Remote Script) | ✅ **This.** Bi-directional, covers ~80% of the Live Object Model, actively maintained, works on Intro. |
| **Raw `.als` file manipulation** | ❌ Gzipped XML with undocumented invariants. Writing a valid modern .als by hand takes weeks and breaks on Live updates. Fine for read-only inspection; useless for editing. |
| **Max for Live custom device** | ❌ Requires Suite. Powerful but locks out Intro users (which is *you*). |
| **Virtual MIDI + external clock** | ❌ Can send notes, can't create tracks, can't load instruments, can't route audio. Playing keyboard, not composing. |
| **`pylive` / `LiveOSC`** | ❌ Both are AbletonOSC's less-maintained predecessors. Same idea, worse coverage, older Live support. |
| **Ableton Note / Move APIs** | ❌ Different products. Not applicable to Live desktop. |

**Consequences of picking AbletonOSC:**
- One-time install: copy a folder of Python files into `~/Music/Ableton/User Library/Remote Scripts/AbletonOSC/`, then enable the "AbletonOSC" control surface in Live's preferences. Persephone can automate both steps.
- Ableton must be running for anything to work. That's fine — this is a music-composer, not a background daemon.
- OSC is a stream protocol, not a request/response one. We wrap it in a small async request/reply layer with correlation IDs (like MCP does for JSON-RPC over stdio).

## 4. Phased roadmap

### Phase 0 — Preflight *(now, this planning doc)*

Detection helper, plan doc, alignment on approach. **Deliverable:** this file + a proof-of-concept that verifies Ableton is running and OSC is reachable.

### Phase 1 — Bridge + tab *(~1 week)*

Everything except the LLM. Prove we can control Live from Persephone at all.

- `server/ableton_detect.py` — find `/Applications/Ableton Live *.app`, walk common versions/editions, expose over `GET /api/ableton/status`.
- `server/ableton_bridge.py` — bundled AbletonOSC copy in `server/ableton_remote/`, install helper that copies it into `~/Music/Ableton/User Library/Remote Scripts/AbletonOSC/`, prints instructions for the one-time preferences toggle.
- `server/ableton_client.py` — async OSC client using `python-osc`. Wraps the request → correlation-id → reply pattern.
- **New sidebar tab: "Music"** (with a `Music4` icon), between **Documents** and **Research**. Only visible if Ableton is detected on the machine.
- **Music tab UI (`src/components/ableton/AbletonView.tsx`)**:
  - Header chip: `ableton · not installed / offline / connected · Live 12 Intro`
  - Big **"Install bridge"** button when the remote script is missing.
  - Big **"Launch Ableton"** button when Live isn't running.
  - Placeholder "Composer coming next" panel once connected.
- **Endpoints:** `POST /api/ableton/install-bridge`, `POST /api/ableton/launch`, `GET /api/ableton/status`, `POST /api/ableton/ping`.

### Phase 2 — The composer skeleton *(~2 weeks)*

Same pattern as Reels/Ornith: LLM plans → user approves → we execute.

- **Chat panel** on the left, current project overview on the right.
- LLM-generated **SongSpec** (JSON — see §6 for schema) covering: BPM, key, time sig, sections (intro/verse/chorus/…), and per-section tracks with roles and instrument suggestions.
- **Translator** turns SongSpec into an ordered list of AbletonOSC calls:
  1. Set tempo + time signature (`/live/song/set/tempo`)
  2. Create tracks (`/live/song/create_midi_track`, `/live/song/create_audio_track`)
  3. Load instruments (`/live/track/load_device` — walks the Live browser to find e.g. `Simpler` + a preset)
  4. Set clip lengths + create empty clips per section (`/live/clip_slot/create_clip`)
  5. Populate clips with notes (`/live/clip/add/notes` — batch)
  6. Set mix levels + basic pan (`/live/track/set/volume`, `/live/track/set/panning`)
- **Genre presets** for the first ship: **lo-fi hip-hop**, **house**, **techno**, **ambient**, **cinematic**. Each has a template SongSpec the LLM can populate rather than plan from scratch.
- **Prompt →** `"lo-fi hip-hop, 85 bpm, E minor, chill"` → LLM output SongSpec → user approves → tracks materialise in Ableton.
- No editing yet. Regenerate = fresh SongSpec. Undo = delete everything the composer created.

### Phase 3 — Iterative editing *(~2 weeks)*

Where this actually becomes a composer.

- **Live state sync.** Persephone polls the current project structure (`/live/song/get/tracks`, `/live/song/get/scenes`, etc.) every ~500ms and mirrors it into an in-memory `AbletonProject` object.
- **Chat context = current project.** Every LLM turn sees the current SongSpec as pinned system context.
- **Diff-based edits.** Instead of "regenerate everything", the LLM emits a small `EditPlan` JSON:
  ```json
  {
    "changes": [
      {"kind": "clip.replace_notes", "track": "Drums", "clip": "Verse 1",
       "notes": [{"pitch": 36, "start": 0, "length": 0.25, "vel": 100}, ...]},
      {"kind": "track.add", "role": "shaker", "name": "Shaker",
       "instrument": "Impulse:Shaker 808", "clips": [...]},
      {"kind": "track.set_volume", "track": "Bass", "db": -3.5}
    ]
  }
  ```
- **Approval gate** for structural changes (adding/removing tracks, changing tempo). Chill inline application for note-level tweaks.
- **Undo stack**: every applied `EditPlan` gets a reverse plan pre-computed and stashed. `⌘Z` inside the Music tab replays reverses.

### Phase 4 — Musical intelligence *(~2 weeks)*

The layer between "LLM emits JSON" and "notes go in Ableton" gets smarter.

- **Music theory helper library** (`server/music_theory.py`):
  - Scale/mode expansion, common chord progressions, cadences.
  - Rhythm grid generation (Euclidean patterns, swing quantisation).
  - Key detection from existing MIDI (so "make the bass fit the current key" always works).
- **Style adapters**: the LLM emits musical intent ("syncopated bass, ghost notes on 3+"), the adapter turns that into actual note grids. Removes the LLM's need to be great at counting sixteenth notes.
- **Groove templates**: import Ableton's own groove pool via OSC (`/live/song/get/available_grooves`) so `swing_amount=0.55` on lo-fi does exactly what a producer expects.
- **Reference matching**: user drops a reference MP3 → we extract tempo/key/mood via a tiny local ML pass and set them as constraints on the SongSpec.

### Phase 5 — Sound design + mixing *(~1-2 weeks)*

- **Instrument selection.** Walk the Ableton browser via OSC (`/live/browser/*`); build a searchable index of the user's instruments (Intro's 5 GB / Suite's 70 GB / their own third-party plugins). Match "warm rhodes" → real device paths.
- **Preset chains for effects**: reverb-lo-fi, sidechain-house-pump, cinematic-stereo-widen. LLM picks from the chain list, we insert them via `/live/track/load_device`.
- **Basic mixing**: the LLM sets track volumes/pan based on genre convention (drums up front, pads recessed, etc.). Not a mastering engineer — a competent rough mix.
- **Loudness sanity**: LUFS meter reading via OSC → warn the LLM if any track is silently clipping.

### Phase 6 — Arrangement + export *(~1 week)*

- **Session ↔ Arrangement view** conversion — the composer builds in Session view (loops), then commits to a linear arrangement on demand.
- **Automation curves**: filter sweeps, volume rides, panning drift for build-ups and drops.
- **Bounce to WAV** via OSC-triggered export, delivered back through `/api/ableton/media/*` for preview + download in the Music tab.
- **Save `.als`** and cache in `data_dir()/ableton/projects/` so users can reopen previously AI-generated projects.

## 5. UI architecture

```
┌────────────────────────────────────────────────────────────────┐
│  🎼 Music ▸ Untitled sketch #4          [Save] [Bounce WAV] ⚙︎  │
├──────────────────────────────┬─────────────────────────────────┤
│                              │                                 │
│   Chat with the composer     │        Project map              │
│   ─────────────────────      │  ─ live state, updated ~2 Hz ─  │
│   ↑ user: make it darker     │  ┌─ Drums     ⋮ volume …        │
│   ↓ composer: shifting to    │  │  ○ Beat A   4/4              │
│      C minor, damping        │  │  ● Beat B   4/4 ▶ playing    │
│      the reverb tail…        │  ├─ Bass      ⋮ volume …        │
│      [play preview]          │  │  ○ Verse    2/4              │
│                              │  ├─ Rhodes    ⋮ volume …        │
│   [Type your next move …]    │  └─ Pad       ⋮ volume …        │
│                              │                                 │
│                              │   Section timeline:             │
│                              │   ┌ intro ┬ verse ┬ chorus ┬…   │
│                              │   └───────┴───────┴───────┴…   │
│                              │                                 │
├──────────────────────────────┴─────────────────────────────────┤
│  ● connected · Live 12 Intro · 85 BPM · E min · 4/4 · Session  │
└────────────────────────────────────────────────────────────────┘
```

**Layout:** two-column, resizable.

- **Left (~45%)** — chat with the composer. Same streaming, tool-event, approval-inline pattern we already have for chat + Reels planning. Voice input works (Kokoro TTS is off by default here — you're listening to music, not the assistant).
- **Right (~55%)** — the project map. Live state, updated by OSC subscription. Clicking a track opens a track inspector (volume, pan, current clip contents rendered as a mini piano-roll SVG). Clicking a section jumps Ableton's playhead there.
- **Footer** — connection state, session-wide metadata (BPM, key, time sig), current view mode. `⌘K` command palette for quick actions like "play chorus", "add rise into drop", "focus track: bass".

## 6. Data model

The **SongSpec** is the durable, user-visible source of truth. AbletonOSC calls are its side-effects.

```ts
interface SongSpec {
  version: 1
  bpm:     number
  key:     { root: string; mode: 'major' | 'minor' | ScaleName }
  timeSig: { num: number; den: number }
  bars:    number
  sections: Section[]
  tracks:   Track[]
  meta:     { genre?: string; mood?: string; reference?: string }
}

interface Section {
  id: string
  name: 'intro' | 'verse' | 'chorus' | 'bridge' | 'drop' | 'outro' | string
  startBar: number
  lengthBars: number
  intensity: number     // 0-1, drives which tracks are active
}

interface Track {
  id: string
  role: 'drums' | 'bass' | 'chord' | 'lead' | 'pad' | 'fx' | 'vox'
  name: string
  instrument: { path: string; preset?: string }  // AbletonOSC-resolvable
  effects:    { path: string; params?: Record<string, number> }[]
  clips:      Clip[]
  mix: { volume_db: number; pan: number; sends?: Record<string, number> }
}

interface Clip {
  section: string     // Section.id — clip fires when this section is active
  bars: number        // usually 4 or 8; loops within its section
  notes: Note[]
  groove?: string     // an Ableton groove template name, or 'none'
}

interface Note {
  pitch: number       // MIDI (0-127)
  start: number       // beats from clip start
  length: number      // beats
  velocity: number    // 0-127
  probability?: number  // 0-1, for stochastic hats etc.
}
```

Persistence: SongSpec goes to `data_dir()/ableton/sketches/<sketchId>.json` on every save. Ableton .als file goes alongside as `<sketchId>.als` for double-clicking outside Persephone.

## 7. Backend layout

```
server/
├── ableton_detect.py       ← find install path, edition (Intro/Standard/Suite), version
├── ableton_bridge.py       ← install AbletonOSC into ~/Music/Ableton/User Library
├── ableton_client.py       ← async OSC client, correlation-ID request/reply
├── ableton_project.py      ← in-memory mirror of Live's current state
├── music_theory.py         ← scales, chord voicings, rhythm generation
├── song_translator.py      ← SongSpec → ordered OSC command list
├── edit_translator.py      ← EditPlan → OSC diff commands + reverse plans
├── ableton_composer.py     ← LLM orchestration; owns the /api/ableton/compose SSE
├── ableton_remote/         ← bundled AbletonOSC (copied into Live's Remote Scripts folder)
└── requirements.txt        ← + python-osc
```

## 8. Endpoints

```
GET    /api/ableton/status              {installed, running, edition, version, bridgeInstalled, connected}
POST   /api/ableton/install-bridge      copy AbletonOSC into ~/Music/Ableton/User Library
POST   /api/ableton/launch              spawn Ableton (open /Applications/Ableton Live *.app)
POST   /api/ableton/ping                echo test — verifies OSC bridge is alive

POST   /api/ableton/compose             SSE: LLM plans a SongSpec (streams stages: theme, structure, tracks, done)
POST   /api/ableton/apply-song          apply a SongSpec — creates tracks, clips, notes, mix
POST   /api/ableton/edit                SSE: LLM emits an EditPlan for the currently-loaded SongSpec
POST   /api/ableton/apply-edit          apply an approved EditPlan; returns reverse plan for undo
POST   /api/ableton/undo                pop the last reverse plan and apply it

GET    /api/ableton/project             current in-memory AbletonProject snapshot (poll or SSE-subscribe)
POST   /api/ableton/transport           {action: "play"|"stop"|"jump"|"loop_section", ...}

POST   /api/ableton/bounce              trigger an offline bounce of the current arrangement to WAV
GET    /api/ableton/media/{sketch_id}.wav | .als
GET    /api/ableton/sketches            list saved sketches (SongSpec + WAV thumbnail)
```

## 9. Sidebar detection logic

The **Music** tab appears in the left column only when:
1. `find_ableton_install()` returns a non-empty path, AND
2. Either the AbletonOSC remote script is installed OR the tab needs to appear so the user can install it.

Detection walks:

```
/Applications/Ableton Live *.app             (macOS)
%ProgramFiles%\Ableton\Live *                (Windows)
~/Applications/Ableton Live *.app            (per-user installs)
```

Returns edition by parsing the app name (`Intro`, `Standard`, `Suite`, `Trial`), version by reading `Info.plist → CFBundleShortVersionString`, and warns if only a Trial is present (7-day session limit will bite).

## 10. What the LLM sees (context-engineering)

Every composer LLM turn gets a compact system prompt shaped like:

```
You are Persephone's music composer. You control Ableton Live via a
music-theory-aware translation layer. NEVER emit MIDI note numbers
without musical justification.

CURRENT SESSION (updated each turn):
  bpm: 85, key: E minor, time_sig: 4/4, bars: 32
  sections: intro (0-8), verse (8-16), chorus (16-24), outro (24-32)
  tracks:
    - Drums (Impulse: 909 kit) — vol -6dB, pattern in verse/chorus
    - Bass (Operator: Sub Bass) — vol -4dB, playing E-G-B-A ostinato
    - Rhodes (Electric: Suitcase) — vol -8dB, ii-V-i comp

USER'S GENRE PALETTE: lo-fi hip-hop
USER'S REFERENCE: (none)

ON EACH TURN:
  - Small tweak → emit an EditPlan JSON.
  - Big change (adding a whole section, swapping the drum kit) → propose
    the plan in plain language, ask user to confirm.
```

The active model is user-chosen (defaults to the same `qwen2.5:32b` we already prefer for structured planning). Not thinking-first models — same issue as the Reels planner: they burn all their num_predict on `<think>` and never emit JSON. That preference list already exists in main.py; we reuse it.

## 11. Extension points (deferred, not scoped)

- **Music-model integration** (Meta MusicGen local, or Suno / Riffusion via cloud APIs) for actual stem generation, dropped into Ableton as audio clips.
- **DAW-agnostic layer** — the SongSpec ↔ OSC translator is Ableton-specific but the LLM composer above it isn't. Same JSON could drive Logic (via LogicRemote), Reaper (via ReaScript), Bitwig (via its native controller API) later.
- **Live performance mode** — the composer emits changes in real time while a user is playing. Requires a much tighter loop and probably a dedicated Max device.
- **Vocal generation** — Persephone's Kokoro can already do TTS, but singing needs a different model (Bark, XTTS, or a specialised singing synthesiser).

## 12. Non-goals

- **We are not building a DAW.** No timeline editor, no piano roll editor, no mixer. Ableton is the interface; Persephone is the AI collaborator that talks to it.
- **We are not shipping Ableton with Persephone.** User must install and license Live separately.
- **We are not doing anything Suite-only in the MVP.** All Phase 1-3 features work on Intro. Max for Live-based extensions land later, opt-in.
- **We are not modifying .als files outside of Ableton.** All changes go through OSC to a running Live instance. This is a deliberate boundary — file-level manipulation is a support-burden trap.

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| AbletonOSC has partial LOM coverage — some effect parameters may be unreachable. | Ship what's covered; use `/live/device/get/parameters/names` to discover reachability per device before offering an edit. |
| Live Intro's instrument library is small; the LLM may hallucinate presets that don't exist. | Build an OSC-driven browser index at bridge-install time; only let the LLM pick from confirmed-present devices. |
| First-time bridge install requires a Live restart. | We tell the user this in the "Install bridge" flow and offer a "restart Ableton for me" button that quits + relaunches via osascript. |
| OSC lost packets → out-of-sync UI. | Every OSC command has a correlation ID; missed replies within 500ms trigger a re-fetch of the affected sub-tree. |
| User has Live Trial only (limits sessions to 90 minutes). | Detected at status probe; UI shows a Trial banner with a timer. |
| The LLM writes music that sucks. | Genre presets + music-theory library set floors on quality. Long-term: fine-tune on a corpus of professional productions. |

## 14. Estimated effort

- Phase 1 (bridge + tab):        **~1 week**
- Phase 2 (composer skeleton):   **~2 weeks**
- Phase 3 (iterative editing):   **~2 weeks**
- Phase 4 (musical intelligence): **~2 weeks**
- Phase 5 (sound design + mix):   **~1-2 weeks**
- Phase 6 (arrangement + export): **~1 week**

**Total** to full-featured composer: **~9-10 weeks of focused work**. Phase 1 alone is ship-able and useful (proves the integration works and lets you preview the bridge behaviour).

## 15. Concrete first PR (Phase 1)

Small enough to review, big enough to prove the direction.

**Backend**
- `server/ableton_detect.py` — `find_install()`, `edition()`, `version()`.
- `server/ableton_bridge.py` — `install()` copies bundled `ableton_remote/AbletonOSC/` into `~/Music/Ableton/User Library/Remote Scripts/AbletonOSC/`; `is_installed()` checks presence + version file.
- `server/ableton_client.py` — thin async wrapper over `python-osc` with request/reply.
- Endpoints: `/api/ableton/status`, `/api/ableton/install-bridge`, `/api/ableton/launch`, `/api/ableton/ping`.
- Add `python-osc>=1.9.3` to `server/requirements.txt`.
- Bundle AbletonOSC into `server/ableton_remote/AbletonOSC/` (LGPL-licensed — attribution in README).

**Frontend**
- `src/store/appStore.ts` — extend `currentView` union with `'music'`.
- `src/components/layout/Sidebar.tsx` — new NavItem with `Music4` icon, positioned between Documents and Research. **Only rendered if `abletonAvailable` is true** in the store (fetched on app load).
- `src/components/layout/AppLayout.tsx` — route to `<AbletonView>` when `currentView === 'music'`.
- `src/components/ableton/AbletonView.tsx` — status header chip, "Install bridge" and "Launch Ableton" buttons, "Composer coming next" placeholder card.

**Rough LOC estimate:** ~600 lines frontend, ~400 lines backend, ~1200 lines vendored AbletonOSC (not authored by us — LGPL bundled).

## 16. Open questions

1. **Composer as its own tab, or as an Ornith-style preset inside the chat?** Leaning: own tab. Music work needs a bespoke UI (project map, section timeline) that a chat window can't provide. Also survives the "voice off" pattern — you want to hear the music, not the assistant explaining it.
2. **Should we support opening an existing .als and iterating on it?** Yes but as Phase 3 or later. AbletonOSC can read the current project state; we'd write an initial SongSpec by scraping that state.
3. **What's the free-tier fallback if the user has *only* Intro and hits its 8-track limit mid-composition?** Warn early ("this SongSpec needs 12 tracks; Intro caps at 8"), degrade gracefully by folding roles together (single "melody" instead of "lead" + "pad").
4. **Do we ship a "Vocal AI" song-lyric-generator alongside?** No. Off-scope for this project. Lyrics belong in the main chat with the LLM you already have.
5. **How much of this needs re-architecting when Ableton 13 lands?** Very little — AbletonOSC has tracked LOM changes across 11→12 with minor version bumps. Living with the third-party dependency is preferable to writing our own remote script from scratch.

---

**Total effort estimate:** ~9-10 weeks for the full stack. Phase 1 alone is ~1 week and is real, ship-able value (proves the direction and lets you preview the bridge behaviour). **Next decision:** approve → cut Phase 1 as its own PR. Or narrow it further to *just* "detect + tab appears, install button is disabled" if you want to see the shell before committing to any Live-side install.
