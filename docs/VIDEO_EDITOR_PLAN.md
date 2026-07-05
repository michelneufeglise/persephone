# Persephone Video Editor — Design Blueprint

*Status: proposal. No code yet.*
*Owner: `src/components/reels/` (frontend) · `server/reels_render.py` (backend).*

## 1. Why this doc exists

The current **Reels** tab is a *slideshow generator*: LLM plans scenes → ComfyUI paints stills → Kokoro voices script → ffmpeg composites 9:16 with Ken Burns + captions. It is fully local, fast, and produces publishable output for talking-head/listicle/quote formats.

But it hides its own limits. Users can't:
- Change *how long* an individual clip lasts (only what the plan-LLM guessed).
- Move a scene from position 3 to position 1.
- Replace ComfyUI's still with a user-recorded 10-second video.
- Add a lower-third graphic that appears at 0:04 and disappears at 0:07.
- Preview a trim before rendering the whole reel.
- Fine-tune the caption font, colour, or position per project.

This document describes how to grow Persephone into a real, opinionated short-form video editor without becoming Adobe Premiere. **Scope: vertical short-form (TikTok/Reels/Shorts) + horizontal cuts (YouTube).** Not: colour grading, multi-cam sync, keyframe animation curves. If a user wants those, they should open Resolve.

## 2. Guiding principles

1. **Local-first, always.** Every asset stays on disk in `data_dir()/reels/`. No cloud APIs required at any stage.
2. **The plan-LLM stays.** The editor is a *refinement* layer on top of the current LLM-generated plan, not a replacement. Users who want "just make me a reel" still get the one-click flow.
3. **The timeline is the source of truth.** Once a user opens the editor, they're editing a document, not "editing settings." Save/load is JSON on disk.
4. **Preview must be sub-second.** Any edit — trim, reorder, caption tweak — reflects in the preview within ~300 ms. No round-trip to ffmpeg for previews.
5. **ffmpeg is the export renderer, never the preview renderer.** Preview is a `<canvas>` or `<video>` compositor in the browser; export walks the same timeline data through `ffmpeg -filter_complex`.
6. **One binary format.** All exports go through the same "timeline → ffmpeg" path — no per-feature bespoke pipeline.

## 3. Phased roadmap

### Phase 0 — Shipped (as of 2026-07-04)
- Slideshow generator, ComfyUI stills, Kokoro voice, ffmpeg per-scene + concat, music with sidechain ducking, per-scene image override, background music upload.

### Phase 1 — Editable Plan (~1 week)
**Goal:** turn the current linear plan into a mutable object with in-place edits.

- Extend `Scene` with `id: string` (uuid) so scenes have identity independent of `n`.
- Add per-scene inline editors on `SceneCard`:
  - Edit `script` (regenerate TTS on blur).
  - Edit `imagePrompt` + "regenerate image" button (single scene, not whole reel).
  - Edit `seconds` (with min = TTS audio length so voice never truncates).
- Reorder scenes via drag handles (`framer-motion` `Reorder` primitive).
- Delete / duplicate scene actions.
- **Persist to disk** — every plan becomes a `data_dir()/reels/projects/<uuid>.json`, auto-saved on every change. New "Projects" subtab replaces the current one-shot flow.

### Phase 2 — Multi-track timeline (~2 weeks)
**Goal:** stop pretending scenes are the atomic unit. Introduce tracks.

- **Data model** (see §4): tracks (video / audio / caption / graphic) with clips. Scenes become derived: "scene N" = "the video clip at position N on the main video track."
- **Timeline UI** below the preview:
  - Horizontal scrolling ruler with time markers.
  - Track lanes stacked vertically.
  - Clips are draggable, resizable at edges (trim in/out).
  - Playhead scrubs the preview.
  - Zoom in/out with cmd+scroll.
- **Preview compositor** — HTML `<canvas>` that draws:
  - Current video clip's still (or `<video>` frame if a real MP4).
  - Any active caption / graphic overlay.
  - Waveform bar showing audio tracks.
  - Driven by `requestAnimationFrame` + a virtual clock synced to `<audio>`.
- The Ken Burns "in / out" pattern becomes a per-clip *effect* with an in-timeline expand handle showing the pan.

### Phase 3 — User-supplied media (~1 week)
**Goal:** treat user footage as first-class citizens.

- Extend the upload endpoint to accept video clips (mp4/mov/webm), not just images.
- ffprobe on upload → cache `{duration, width, height, fps, has_audio}` in a sidecar JSON.
- Timeline lets you drag a video clip onto the video track; renderer stops using `-loop 1 -i still.png` for that clip and starts using the actual `-ss X -to Y -i clip.mp4`.
- Audio track auto-populated from clip audio if present (with a "detach audio" checkbox).
- Trimming a video clip on the timeline updates the export command's `-ss` / `-to`.

### Phase 4 — Overlays, transitions, effects (~1-2 weeks)
**Goal:** the small vocabulary of visual language TikTok actually uses.

- **Overlay clips** on a separate track: PNG (logo), text (drawtext), animated emoji, safe-zone guides.
- **Transitions** between adjacent clips: cut (default), fade, slide-left, whip-pan, dip-to-black. Rendered via ffmpeg `xfade` filter.
- **Per-clip effects**: brightness, contrast, saturation, blur, mirror. Chain via `-filter_complex` on the clip's segment.
- **Speed ramps**: 0.5× / 1× / 1.5× / 2× per clip via ffmpeg `setpts` + `atempo`.

### Phase 5 — Templates & sharing (~1 week)
**Goal:** compound the editor's value with reusable patterns.

- A **Template** is a serialised timeline where clips have `role: "hook" | "body_1" | "cta"` placeholders instead of concrete content.
- LLM ingests a template + a topic and produces a new project auto-filled from that template.
- Share `.persephone-reel.json` files with others (they load in one click; missing assets prompt for re-upload).

## 4. Data model

```ts
type ID = string  // ULID or nanoid

interface Project {
  id:        ID
  title:     string
  aspect:    '9:16' | '1:1' | '16:9'
  fps:       24 | 30 | 60
  createdAt: number
  updatedAt: number
  tracks:    Track[]
  voices:    { defaultVoiceId: string }
  captions:  CaptionStyle
  meta:      { topic?: string; tone?: Tone; templateId?: ID }
}

interface Track {
  id:      ID
  kind:    'video' | 'audio' | 'caption' | 'graphic'
  name:    string
  muted?:  boolean
  hidden?: boolean
  clips:   Clip[]
}

interface Clip {
  id:            ID
  start:         number   // seconds on the master timeline
  duration:      number   // seconds
  source:        ClipSource
  trim:          { in: number; out: number }   // within the source
  effects:       Effect[]                       // ordered
  transition?:   { kind: 'cut'|'fade'|'slide'|'whip'|'dip'; duration: number }
  keyframes?:    Keyframe[]                     // opacity, position, scale over time
}

type ClipSource =
  | { kind: 'still';        assetId: ID }
  | { kind: 'video';        assetId: ID }
  | { kind: 'tts_audio';    text: string; voiceId: string }
  | { kind: 'uploaded_audio'; assetId: ID }
  | { kind: 'text_overlay'; text: string; style: TextStyle }
  | { kind: 'sd_still';     prompt: string; checkpoint: string; seed?: number }

interface Effect {
  kind: 'ken_burns_in' | 'ken_burns_out' | 'blur' | 'brightness'
      | 'saturation'  | 'speed'          | 'mirror' | 'greyscale'
  params?: Record<string, number>
}

interface Keyframe {
  t:        number  // seconds relative to clip start
  property: 'opacity' | 'x' | 'y' | 'scale' | 'rotate'
  value:    number
  easing?:  'linear' | 'ease_in_out' | 'cubic'
}
```

An **Asset** is any bytes on disk with metadata:

```ts
interface Asset {
  id:       ID
  kind:     'image' | 'video' | 'audio' | 'font'
  path:     string   // absolute in data_dir()
  bytes:    number
  mime:     string
  probe:    { duration?: number; width?: number; height?: number; fps?: number; has_audio?: boolean }
  origin:   'upload' | 'comfy' | 'kokoro' | 'system'
  createdAt: number
}
```

Everything is content-addressable by asset id, so a project references paths *only through assets* — you can move `data_dir()/reels/` to another disk and reindex.

## 5. Export strategy

Every export walks the same code path:

1. **Resolve** — flatten the timeline into a per-second render plan. Group co-active clips per frame.
2. **Materialise** — ensure every clip has bytes on disk (call ComfyUI for `sd_still` sources, call Kokoro for `tts_audio` sources).
3. **Segment** — for each maximal contiguous stretch of the timeline where the active clip set doesn't change, emit an ffmpeg segment description.
4. **Assemble** — build one `-filter_complex` graph that stitches the segments, applies transitions, layers overlays, mixes audio.
5. **Render** — one ffmpeg invocation, one MP4 out.

**Why one ffmpeg call?** Because `xfade` transitions work across ffmpeg's internal filter graph, not across separately-encoded MP4s. As soon as we ship transitions in Phase 4 the per-scene-then-concat approach breaks. Better to move to `filter_complex` in Phase 2 and never look back.

**Progress reporting** — parse ffmpeg's `-progress` output (structured k=v pairs) instead of stderr regex hacking. Ship a real "42% · ETA 00:00:18" bar.

## 6. UI architecture

```
┌────────────────────────────────────────────────────────────┐
│  ⚘ Reels ▸ Project Title            [Save] [Export] ⚙︎     │
├───────────────────────────────┬────────────────────────────┤
│                               │                            │
│      Preview <canvas>         │       Inspector            │
│      (aspect-locked)          │  ─ selected clip only ─    │
│                               │  Source: still / video     │
│                               │  Trim in:  00:00.400       │
│                               │  Trim out: 00:03.240       │
│                               │  Effects: [Ken Burns in ▾] │
│                               │  Transition to next: [ ▾ ] │
│                               │                            │
│                               │                            │
├───────────────────────────────┴────────────────────────────┤
│  ▶︎ 00:00.00 / 00:32.15   ────●──────────────────  🔍  100% │
│  Video   [====][=========][====][====]                     │
│  Audio·V [══════════════════════════════]                  │
│  Music   [═════════════════════════════════════]           │
│  Cap.    [Aa           ][Aa           ][Aa      ]          │
└────────────────────────────────────────────────────────────┘
```

**Three panels:**
- **Preview** (top-left) — HTML `<canvas>` sized to project aspect. Compositor draws current-frame stills + text overlays; a hidden `<audio>` element drives the clock.
- **Inspector** (top-right) — context-sensitive; shows properties of whichever clip is selected on the timeline. Empty state when nothing selected.
- **Timeline** (bottom) — the meat. Tracks as horizontal lanes; clips as draggable colour-coded blocks. Playhead scrubs. Zoom via cmd+scroll. Multi-select for bulk operations.

**Interaction model:**
- Click a clip → inspector shows its properties.
- Drag a clip's edge → trim.
- Drag a clip's body → move (snap to playhead, other clip edges).
- Drop a file onto a track → new clip at drop position.
- Right-click → context menu (split at playhead, duplicate, delete, add transition).
- Cmd+Z / Cmd+Shift+Z → real undo/redo (state via [immer](https://immerjs.github.io/) patches).

## 7. Preview compositor details

- **Runs at project fps** (30 by default). Not stutter-tolerant, but this isn't Netflix.
- **Video clips**: hidden `<video>` element per clip, `preload="metadata"`, seek-and-draw to canvas per frame. Native H.264 decode → we don't need WebCodecs.
- **Still clips**: `<img>` decoded once → drawn each frame with the current keyframe interpolation for Ken Burns / opacity.
- **Text overlays**: `ctx.fillText` with cached font metrics.
- **Audio**: multiple `<audio>` elements, one per audio clip, each `currentTime = playhead - clip.start + clip.trim.in`, `.play()` when playhead enters clip. Clock authority is a single "conductor" `<audio>` (silent, longest duration) — everyone else follows its `currentTime`.

This is a known-good approach — see Motion Canvas, Remotion, and the ffmpeg-adjacent editor projects.

## 8. Third-party alternatives (evaluated + rejected for the core, but worth knowing)

| Tool | What it is | Why not the core |
|---|---|---|
| [Remotion](https://www.remotion.dev) | React components → MP4 via headless Chromium | Requires bundling Chromium (~200 MB extra) and encoding rate is limited by headless framerate. Great for programmatic templates, over-engineered for what our users edit. |
| [ffmpeg.wasm](https://ffmpegwasm.netlify.app) | ffmpeg compiled to WASM | 25 MB payload, ~5× slower than native ffmpeg, no hardware accel. Fine for previews (which we do differently) but bad for exports on local machines that already have native ffmpeg. |
| [Motion Canvas](https://motioncanvas.io) | TypeScript-first motion graphics | Focused on programmatic animation not clip editing. |
| [OpenShot / Shotcut](https://www.openshot.org) | Full desktop NLEs | Overshoots our scope; ships as separate apps. Users who need this should use these directly. |
| [VideoIO / MoviePy](https://zulko.github.io/moviepy/) | Python wrappers over ffmpeg | Nice for scripting, no advantage over shelling out to ffmpeg from Python. |

**Recommendation:** use native ffmpeg for export (already installed), roll our own preview compositor (~1000 LOC). Optionally support "Export as Remotion project" much later as a power-user feature.

## 9. Storage & migration

- **Location:** `data_dir()/reels/projects/<projectId>/`
  - `project.json` — the timeline
  - `assets/` — hard links or copies of every referenced asset
  - `preview.png` — first-frame thumbnail
- **Format version:** every project JSON starts with `{"$schema": "persephone-reel/v1", ...}`. Bumping to v2 triggers `migrate_v1_v2(json)` on load. Never break old projects.
- **Undo history:** in-memory only; if a user wants persistent version history that's a "save named revision" feature (Phase 5+).

## 10. Concrete first PR (Phase 1)

Small enough to ship without scope creep. Enables refinement without introducing tracks yet.

**Backend**
- `POST /api/reels/projects` — create from a plan or blank.
- `GET  /api/reels/projects` — list all persisted projects.
- `GET  /api/reels/projects/{id}` — full project JSON.
- `PATCH /api/reels/projects/{id}` — save partial update (debounced client-side).
- Extend `POST /api/reels/render` to accept a projectId instead of an inline plan.
- Extend `_stream_reels_plan` output — write projectId into result so the UI can PATCH.

**Frontend**
- `useProject(projectId)` hook — SWR-style fetch + auto-save.
- Rewrite `SceneCard` as `<InlineEditableScene>` with:
  - Text field for `script`, blur → PATCH project.
  - Text field for `imagePrompt` + [regenerate] button.
  - Duration input with `min={audioSec}` clamp.
  - Drag handle (framer-motion `Reorder.Item`).
- Add "History" replacement: **Projects** with cards showing thumbnail + last edit time.
- Move the "big Plan / Render" panel to a per-project route.

**Rough LOC estimate:** ~800 lines frontend, ~300 lines backend, ~150 lines shared types.

## 11. Non-goals (say-so up front)

- Real-time collaboration (multi-user). This is a local app.
- Cloud rendering. If a user wants fal.ai-tier motion they use fal.ai and drop the mp4 into the editor.
- Vector animation authoring (Lottie, After Effects). Wrong tool.
- Colour grading. Instagram exists.
- Multi-cam sync. Not a short-form problem.

## 12. Open questions

1. **How much timeline state lives in the zustand store vs. a per-project context?** Leaning: per-project context; the store gets `activeProjectId` only.
2. **Do we compile transitions/effects to `filter_complex` client-side or server-side?** Leaning: server-side (Python has the ffmpeg version detection, safer).
3. **Should we support importing existing `.mp4` files as *whole* videos to trim (no scene structure)?** Yes, but as a "Trim" mode separate from full project mode — Phase 3.
4. **Font management** — bundle 4-5 curated fonts (Inter, Bebas Neue, Playfair, Poppins, monospace) vs. let users upload. Leaning: bundle + upload override.

---

**Total effort estimate:** ~6 weeks for the full stack (Phases 1-5). Phase 1 alone is a real, ship-able improvement over what exists today and takes ~1 week.

**Next decision:** approve this plan → cut Phase 1 into a milestone. Or narrow it further to *just* "inline scene edits" before persisting projects.
