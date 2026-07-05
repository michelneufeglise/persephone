"""
Reels renderer — image + voiceover + captions → 9:16 (or 1:1 / 16:9) MP4.

Per-scene ffmpeg call:
  loop image  ─┐
               ├─ zoompan (Ken Burns) ─ drawtext (caption) ─ h264 ─┐
  wav voice   ─┴───────────────────────────────────────── aac ────┘─→ scene.mp4

Then all scenes are stitched with ffmpeg's concat demuxer. Optional
background music is mixed in a final pass with sidechain-compressed ducking
so the voiceover always stays legible.

Public API:
  render_reel(plan, checkpoint, on_progress) -> Path
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import subprocess
import uuid
import wave
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable
from urllib.request import Request, urlopen

import comfy_client as _comfy
import tts_engine as _tts
import transcribe as _transcribe
import ffmpeg_helper as _ffh
from paths import data_dir

log = logging.getLogger("reels_render")

# ── Layout math ────────────────────────────────────────────────────────────────
_ASPECT_DIMS = {
    "9:16": (1080, 1920),
    "1:1":  (1080, 1080),
    "16:9": (1920, 1080),
}
_WRAP_CHARS = {
    "9:16": 22,
    "1:1":  26,
    "16:9": 34,
}
_FPS = 30

# ── Font (system font first, otherwise auto-downloaded on first render) ──────
# Two-tier strategy: prefer a widely-installed bold sans that lives on the
# host OS (fast, offline, correct license); fall back to a chain of stable
# download URLs so a headless dev VM still works.
_SYSTEM_FONT_CANDIDATES = [
    # macOS — ships with every install since 10.5.
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    # Linux — Debian/Ubuntu with fonts-dejavu (default on most distros).
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    # Windows — Arial Bold ships with every consumer edition.
    "C:/Windows/Fonts/arialbd.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]
# Ordered fallback URLs — first one that returns a valid font wins.
_FONT_DOWNLOAD_URLS = [
    ("Roboto-Bold.ttf",
     "https://raw.githubusercontent.com/google/fonts/main/apache/roboto/static/Roboto-Bold.ttf"),
    ("OpenSans-Bold.ttf",
     "https://raw.githubusercontent.com/googlefonts/opensans/main/fonts/ttf/OpenSans-Bold.ttf"),
    ("NotoSans-Bold.ttf",
     "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Bold.ttf"),
]
_FONT_LOCAL_NAME = "reels-caption.ttf"


def _reels_dir() -> Path:
    p = data_dir() / "reels"
    (p / "fonts").mkdir(parents=True, exist_ok=True)
    (p / "scenes").mkdir(parents=True, exist_ok=True)
    (p / "out").mkdir(parents=True, exist_ok=True)
    return p


def _font_path() -> Path:
    return _reels_dir() / "fonts" / _FONT_LOCAL_NAME


def _find_system_font() -> Path | None:
    for cand in _SYSTEM_FONT_CANDIDATES:
        try:
            p = Path(cand)
            if p.is_file() and p.stat().st_size > 100_000:
                return p
        except OSError:
            continue
    return None


def _ensure_font() -> Path:
    fp = _font_path()
    if fp.exists() and fp.stat().st_size > 100_000:
        return fp

    # 1. System font: use it directly (no copy — ffmpeg reads the path fine).
    sys_font = _find_system_font()
    if sys_font is not None:
        log.info("Using system font for reel captions: %s", sys_font)
        return sys_font

    # 2. Download fallback — walk the URL chain until one works.
    last_err: Exception | None = None
    for name, url in _FONT_DOWNLOAD_URLS:
        try:
            log.info("Fetching caption font %s from %s", name, url)
            req = Request(url, headers={"User-Agent": "persephone-reels/1.0"})
            with urlopen(req, timeout=30) as r, open(fp, "wb") as w:
                shutil.copyfileobj(r, w)
            if fp.stat().st_size > 100_000:
                log.info("Font ready: %s (%d bytes)", fp, fp.stat().st_size)
                return fp
            # Truncated / bogus payload — try next URL.
            fp.unlink(missing_ok=True)
        except Exception as exc:
            last_err = exc
            log.warning("font source %s failed: %s", url, exc)
            fp.unlink(missing_ok=True)
            continue
    raise RuntimeError(
        f"No caption font available. System search failed and every download URL 404'd. "
        f"Drop any .ttf file at {fp} to force it. Last error: {last_err}"
    )


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# ── Text helpers ───────────────────────────────────────────────────────────────
def _wrap(text: str, max_chars: int) -> str:
    """Word-wrap for burned-in captions. Preserves order, breaks on spaces."""
    words = text.strip().split()
    lines: list[str] = []
    current: list[str] = []
    length = 0
    for w in words:
        add_len = len(w) + (1 if current else 0)
        if length + add_len > max_chars and current:
            lines.append(" ".join(current))
            current, length = [w], len(w)
        else:
            current.append(w)
            length += add_len
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def _audio_seconds(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return w.getnframes() / w.getframerate()


# ── ffmpeg orchestration ──────────────────────────────────────────────────────
async def _run_ff(args: list[str], label: str) -> None:
    """Run ffmpeg; raise RuntimeError with tail of stderr on nonzero exit."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = (stderr or b"").decode("utf-8", errors="replace").splitlines()[-30:]
        raise RuntimeError(f"ffmpeg [{label}] exited {proc.returncode}:\n" + "\n".join(tail))


def _render_caption_png(
    text: str,
    font_path: Path,
    width: int,
    height: int,
    out_path: Path,
) -> None:
    """Render the wrapped caption text as a transparent PNG.

    Sidesteps ffmpeg's drawtext filter entirely — only requires an ffmpeg
    build that has the `overlay` filter, which is universal. Also gives us
    proper anti-aliasing and control over the shadow-stroke that drawtext
    can't do without hacks.
    """
    from PIL import Image, ImageDraw, ImageFont
    lines = [ln for ln in text.split("\n") if ln.strip()] or [""]
    fontsize = max(28, round(height / 34))
    font     = ImageFont.truetype(str(font_path), fontsize)

    # Measure each line
    line_metrics = []
    max_line_w   = 0
    for line in lines:
        bbox = font.getbbox(line)
        w    = bbox[2] - bbox[0]
        h    = bbox[3] - bbox[1]
        line_metrics.append((line, w, h, bbox[1]))
        max_line_w = max(max_line_w, w)

    line_gap    = round(fontsize * 0.22)
    total_h     = sum(h for _, _, h, _ in line_metrics) + line_gap * (len(lines) - 1)
    stroke_w    = max(3, fontsize // 12)
    box_pad_x   = round(fontsize * 0.55)
    box_pad_y   = round(fontsize * 0.35)
    box_w       = max_line_w + box_pad_x * 2 + stroke_w * 2
    box_h       = total_h    + box_pad_y * 2 + stroke_w * 2
    img_w       = min(box_w, width - 40)   # margin from canvas edges
    img_h       = box_h

    img  = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded translucent background box for legibility over busy footage.
    box_bg = (0, 0, 0, 90)
    try:
        draw.rounded_rectangle((0, 0, img_w - 1, img_h - 1), radius=20, fill=box_bg)
    except AttributeError:
        # Ancient Pillow — no rounded_rectangle. Plain rect is fine.
        draw.rectangle((0, 0, img_w - 1, img_h - 1), fill=box_bg)

    y = box_pad_y + stroke_w
    for line, lw, lh, top in line_metrics:
        x = (img_w - lw) // 2
        # PIL renders the stroke for us — much cleaner than the manual 8-way
        # blit trick and much faster than ffmpeg drawtext borderw.
        draw.text(
            (x, y - top),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=stroke_w,
            stroke_fill=(0, 0, 0, 220),
        )
        y += lh + line_gap

    img.save(out_path)


def _build_effect_chain(fx: dict) -> str:
    """
    Build a comma-prefixed ffmpeg filter fragment for per-scene look effects.

    Supported keys (all optional, all skippable):
      brightness : float, -1.0 .. 1.0   (default 0)
      contrast   : float,  0.0 .. 3.0   (default 1)
      saturation : float,  0.0 .. 3.0   (default 1)
      speed      : float,  0.5 .. 2.0   (default 1)  — video only for now
      grayscale  : bool                              — one-liner via `hue=s=0`

    Returns a string like ",eq=brightness=0.10:contrast=1.20:saturation=1.10"
    (or "" if no effects are non-default). The leading comma is intentional
    — the chain is spliced *between* zoompan and the [cap] label.
    """
    parts: list[str] = []
    eq_bits: list[str] = []

    b = float(fx.get("brightness", 0.0) or 0.0)
    c = float(fx.get("contrast",   1.0) or 1.0)
    s = float(fx.get("saturation", 1.0) or 1.0)

    if abs(b) > 0.001:              eq_bits.append(f"brightness={b:.3f}")
    if abs(c - 1.0) > 0.001:        eq_bits.append(f"contrast={c:.3f}")
    if abs(s - 1.0) > 0.001:        eq_bits.append(f"saturation={s:.3f}")

    if eq_bits:
        parts.append("eq=" + ":".join(eq_bits))

    if fx.get("grayscale"):
        parts.append("hue=s=0")

    speed = float(fx.get("speed", 1.0) or 1.0)
    if abs(speed - 1.0) > 0.01:
        # setpts affects video only; audio adjusts via `atempo` in the
        # renderer (we keep audio at 1× for now — audio-rate scaling with
        # Kokoro voiceover sounds bad and needs its own UI concept).
        parts.append(f"setpts={1.0/speed:.4f}*PTS")

    if not parts:
        return ""
    return "," + ",".join(parts)


def _caption_overlay_chain(
    height: int,
    captions: list[dict],
    input_label_in: str = "cap",
    input_label_out: str = "v",
) -> str:
    """Build a chain of `overlay` filters, one per caption segment.

    Each segment is a dict with:
        input_idx : int    -- ffmpeg -i input index for that PNG
        start     : float  -- seconds when the caption should appear (t = 0 is scene start)
        end       : float  -- seconds when it should disappear

    An empty list is a no-op that just relabels [in]->[out] via `null`
    so callers can keep their filter graphs uniform.

    Position: horizontally centred, low-third (~11 % up from the bottom).
    """
    y_off = round(height * 0.11)
    if not captions:
        return f"[{input_label_in}]null[{input_label_out}]"

    lines: list[str] = []
    prev = input_label_in
    for i, c in enumerate(captions):
        nxt = input_label_out if i == len(captions) - 1 else f"cap{i+1}"
        # Time-gate with `enable='between(t,start,end)'` — supported by every
        # ffmpeg with overlay (which is universal).
        enable = f":enable='between(t,{max(0.0, float(c['start'])):.3f},{max(0.0, float(c['end'])):.3f})'"
        lines.append(
            f"[{prev}][{c['input_idx']}:v]"
            f"overlay=x=(main_w-overlay_w)/2:y=main_h-overlay_h-{y_off}"
            f"{enable}"
            f"[{nxt}]"
        )
        prev = nxt
    return ";".join(lines)


async def _render_scene(
    *,
    image_path:   Path,
    audio_source: str,               # "kokoro" | "silent" | "master_audio"
    kokoro_wav:   Path | None,
    captions:     list[dict],        # per _caption_overlay_chain; may be empty
    caption_pngs: list[Path],        # in the same order as `captions`
    duration_s:   float,
    width:        int,
    height:       int,
    ken_burns:    str,               # "in" | "out"
    out_path:     Path,
    master_audio_path:  Path | None = None,
    master_audio_start: float = 0.0,
    effects:      dict | None = None,
) -> None:
    """
    Scene background = ComfyUI-generated or user-supplied still.

    Audio options:
      "kokoro"       — Kokoro TTS voiceover.
      "silent"       — anullsrc placeholder track (still valid AAC output).
      "master_audio" — audio ripped from the master video, seeked to the
                       scene's offset. Lets an image scene override the
                       *visual* while a talking-head master keeps speaking.
    """
    total_frames = max(int(duration_s * _FPS), _FPS)

    # zoompan expressions — subtle 1.0 → 1.12 (in) or 1.12 → 1.0 (out) drift.
    if ken_burns == "in":
        zexpr = "z='min(zoom+0.0015,1.12)'"
    else:
        zexpr = "z='if(lte(zoom,1.0),1.12,max(1.0,zoom-0.0015))'"
    xexpr = "x='iw/2-(iw/zoom/2)'"
    yexpr = "y='ih/2-(ih/zoom/2)'"

    inputs: list[str] = [
        "-loop", "1", "-t", f"{duration_s:.3f}", "-i", str(image_path),
    ]

    # Audio input (index 1).
    if audio_source == "kokoro":
        assert kokoro_wav is not None
        inputs += ["-i", str(kokoro_wav)]
        audio_map = "1:a"
    elif audio_source == "master_audio":
        assert master_audio_path is not None
        # Seek into the master video before the -i so we grab audio at the
        # same offset the render's visuals would have used. `-t duration`
        # clamps so we don't over-read. `-vn` skips video decoding for speed.
        inputs += [
            "-ss", f"{max(0.0, float(master_audio_start)):.3f}",
            "-t", f"{duration_s:.3f}",
            "-i", str(master_audio_path),
        ]
        audio_map = "1:a?"
    else:  # silent
        inputs += [
            "-f", "lavfi",
            "-t", f"{duration_s:.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
        audio_map = "1:a"

    # Caption PNG inputs — one -i per segment, at indices 2 .. 2+N-1.
    # We re-write each caption's `input_idx` here so callers can build the
    # list without knowing the ffmpeg indexing convention.
    resolved_captions: list[dict] = []
    for i, (c, png) in enumerate(zip(captions, caption_pngs)):
        # `-loop 1 -t duration` on each PNG makes it a proper video stream
        # for the whole scene. Without this, ffmpeg treats the PNG as a
        # single-frame input and `-shortest` (below) truncates the entire
        # output to ~1 frame — that's the "black screen" bug when voice is
        # off and there's no long TTS stream to hide it.
        inputs += [
            "-loop", "1", "-t", f"{duration_s:.3f}",
            "-i", str(png),
        ]
        resolved_captions.append({**c, "input_idx": 2 + i})

    caption_chain = _caption_overlay_chain(height, resolved_captions,
                                            input_label_in="cap", input_label_out="v")

    # Optional per-scene look effects — brightness/contrast/saturation via `eq`
    # goes AFTER zoompan (so it applies to the composited frame) but BEFORE
    # the caption overlay chain (so we don't tint the caption text).
    fx_chain = _build_effect_chain(effects or {})
    filter_complex = (
        f"[0:v]"
        f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,"
        f"crop={width*2}:{height*2},"
        f"zoompan={zexpr}:d={total_frames}:s={width}x{height}:fps={_FPS}:{xexpr}:{yexpr}"
        f"{fx_chain}"
        f"[cap];"
        f"{caption_chain}"
    )

    args = [
        "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", audio_map,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", str(_FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        # Cap the output to the scene duration explicitly instead of relying
        # on `-shortest`. Safer against short PNG inputs from images-as-video.
        "-t", f"{duration_s:.3f}",
        str(out_path),
    ]
    await _run_ff(args, f"scene→{out_path.name}")


async def _render_scene_from_video(
    *,
    video_path:   Path,
    audio_source: str,               # "kokoro" | "video" | "silent"
    kokoro_wav:   Path | None,
    captions:     list[dict],
    caption_pngs: list[Path],
    duration_s:   float,
    width:        int,
    height:       int,
    out_path:     Path,
    video_start:  float = 0.0,       # seek into the source before decoding
    effects:      dict | None = None,
) -> None:
    """
    Scene background = user-supplied video clip (mp4/mov/webm).

    - Video is cover-scaled to the project canvas.
    - `-stream_loop -1` loops short clips; `-t duration_s` clamps long ones.
    - Audio source:
        "kokoro" - Kokoro voiceover replaces the clip's own audio.
        "video"  - keep the clip's own audio (Spanish speech, ambient, etc).
        "silent" - drop all audio; useful when music will fill.
    - Captions are a chain of time-gated PNG overlays.
    """
    # `-ss` placed BEFORE `-i` uses fast keyframe seek; ffmpeg then decodes
    # from the nearest keyframe at/before video_start. Combined with
    # `-stream_loop -1 -t duration`, this gives each scene its own contiguous
    # slice of the source video (0-4 s, 4-8 s, etc) instead of every scene
    # replaying from t=0 — the "repeating first scene" bug.
    inputs: list[str] = [
        "-stream_loop", "-1",
        "-ss", f"{max(0.0, float(video_start)):.3f}",
        "-t", f"{duration_s:.3f}",
        "-i", str(video_path),
    ]

    if audio_source == "kokoro":
        assert kokoro_wav is not None
        inputs += ["-i", str(kokoro_wav)]
        audio_map: str | None = "1:a"
        first_caption_input = 2
    elif audio_source == "video":
        # Keep the source clip's own audio (input 0's audio stream).
        audio_map = "0:a?"
        first_caption_input = 1
    else:  # silent
        inputs += [
            "-f", "lavfi",
            "-t", f"{duration_s:.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
        audio_map = "1:a"
        first_caption_input = 2

    resolved_captions: list[dict] = []
    for i, (c, png) in enumerate(zip(captions, caption_pngs)):
        # Same PNG-looping fix as the still path — without `-loop 1 -t`, a
        # caption PNG contributes ~1 frame and blows up either `-shortest` or
        # the overlay's enable timing on the video path too.
        inputs += [
            "-loop", "1", "-t", f"{duration_s:.3f}",
            "-i", str(png),
        ]
        resolved_captions.append({**c, "input_idx": first_caption_input + i})

    caption_chain = _caption_overlay_chain(height, resolved_captions,
                                            input_label_in="cap", input_label_out="v")

    fx_chain = _build_effect_chain(effects or {})
    filter_complex = (
        f"[0:v]"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"fps={_FPS},"
        f"setsar=1"
        f"{fx_chain}"
        f"[cap];"
        f"{caption_chain}"
    )

    args = [
        "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", audio_map,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", str(_FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        # Explicit output-duration cap — same reason as the still path.
        "-t", f"{duration_s:.3f}",
        str(out_path),
    ]
    await _run_ff(args, f"scene→{out_path.name} (video)")


async def _concat_scenes(scene_mp4s: list[Path], out_path: Path) -> None:
    """Losslessly stitch per-scene MP4s into the final reel.

    Now backed by `ffmpeg_helper.concat_files` — same demuxer + args as before
    (video `-c copy`, audio re-encoded to AAC to normalise frame boundaries),
    but the graph is expressed via the fluent ffmpeg-python API. Kept as a
    thin alias here so the render pipeline's call sites don't need to change.
    """
    await _ffh.concat_files(scene_mp4s, out_path, reencode_audio=True)


async def _mix_music(video_path: Path, music_path: Path, out_path: Path,
                     music_volume: float = 0.18) -> None:
    """
    Sidechain-ducked music mix so the voiceover never gets buried.

    Delegates to `ffmpeg_helper.mix_music_ducked`, which builds the same
    two-input graph (volume → sidechaincompress → amix) via ffmpeg-python's
    fluent API instead of a hand-rolled `-filter_complex` f-string.
    """
    await _ffh.mix_music_ducked(
        video_path, music_path, out_path,
        music_volume=music_volume,
        # Match the previous hand-rolled parameters exactly so audio output
        # is byte-comparable to the pre-migration renderer:
        threshold=0.05, ratio=8, attack_ms=15, release_ms=350, makeup=1.0,
        stream_loop=True,
    )


# ── Public orchestrator ───────────────────────────────────────────────────────
ProgressCb = Callable[[dict[str, Any]], Awaitable[None]]


async def render_reel(
    plan:         dict[str, Any],
    checkpoint:   str,
    on_progress:  ProgressCb,
    music_path:   Path | None = None,
    music_volume: float = 0.18,
    voiceover:    bool = True,
    captions:     bool = True,
    caption_mode: str  = "script",     # "script" | "transcript"
    translate:    bool = False,        # only meaningful when caption_mode == "transcript"
) -> Path:
    """
    Render a full reel. Returns the finished MP4 path.

    Toggles:
      voiceover     — if False, skip Kokoro TTS. Video scenes use their own
                      audio; image scenes get a silent placeholder track.
      captions      — if False, skip caption PNG generation and overlay.
      caption_mode  — "script" uses the LLM's script line; "transcript" runs
                      Whisper on the scene's source audio (video scenes only).
      translate     — Whisper's translate-to-English mode; ignored unless
                      caption_mode == "transcript".
    """
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH — install ffmpeg to render reels.")

    aspect = plan.get("aspect", "9:16")
    if aspect not in _ASPECT_DIMS:
        raise ValueError(f"unknown aspect {aspect!r}")
    width, height = _ASPECT_DIMS[aspect]
    wrap_chars    = _WRAP_CHARS[aspect]
    voice         = plan.get("voice", _tts.DEFAULT_VOICE)
    scenes        = plan.get("scenes") or []
    if not scenes:
        raise ValueError("plan has no scenes")

    # Guard: transcript mode without any video source is meaningless. Fall
    # back gracefully to script captions and log why.
    any_scene_has_video = any(str(s.get("overrideVideoPath") or "").strip() for s in scenes)
    if caption_mode == "transcript" and not any_scene_has_video:
        log.info("caption_mode=transcript with no video scenes → falling back to script captions")
        caption_mode = "script"

    font_path = _ensure_font()

    reel_id   = uuid.uuid4().hex[:12]
    work_dir  = _reels_dir() / "scenes" / reel_id
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir   = _reels_dir() / "out"

    scene_mp4s: list[Path] = []
    total = len(scenes)

    try:
        for i, s in enumerate(scenes, start=1):
            n            = int(s.get("n", i))
            script       = str(s.get("script", "")).strip()
            image_prompt = str(s.get("imagePrompt") or s.get("image_prompt") or "").strip()
            planned_sec  = int(s.get("seconds", 4))
            ken_burns    = "in" if (n % 2 == 1) else "out"
            img_override = str(s.get("overrideImagePath") or "").strip()
            vid_override = str(s.get("overrideVideoPath") or "").strip()
            vid_start    = float(s.get("overrideVideoStart") or 0.0)
            master_audio_path  = str(s.get("masterAudioPath") or "").strip()
            master_audio_start = float(s.get("masterAudioStart") or 0.0)
            scene_effects      = s.get("effects") or {}

            # Sources: video > image > ComfyUI-generated still.
            use_video = bool(vid_override and Path(vid_override).exists())
            use_image = (not use_video) and bool(img_override and Path(img_override).exists())

            img_path: Path | None = None
            vid_path: Path | None = None

            # 1. Background media — video, override still, or ComfyUI-generated still.
            if use_video:
                await on_progress({"stage": "image", "scene": n, "total": total,
                                    "source": "video_override"})
                vid_path = Path(vid_override)
                log.info("scene %d: using user override video %s", n, vid_override)
            elif use_image:
                await on_progress({"stage": "image", "scene": n, "total": total,
                                    "source": "image_override"})
                img_path = work_dir / f"scene_{n}.png"
                img_path.write_bytes(Path(img_override).read_bytes())
                log.info("scene %d: using user override image %s", n, img_override)
            else:
                await on_progress({"stage": "image", "scene": n, "total": total,
                                    "source": "comfy"})
                png_bytes = await _comfy.generate(
                    image_prompt or "cinematic still, 35mm, editorial photography",
                    checkpoint=checkpoint,
                    width=width, height=height,
                    steps=20,
                )
                img_path = work_dir / f"scene_{n}.png"
                img_path.write_bytes(png_bytes)

            # 2. Voice via Kokoro — only if voiceover is on.
            wav_path: Path | None = None
            audio_dur = 0.0
            if voiceover:
                await on_progress({"stage": "voice", "scene": n, "total": total})
                wav_bytes = await _tts.synthesize(script or "…", voice, 1.0)
                wav_path  = work_dir / f"scene_{n}.wav"
                wav_path.write_bytes(wav_bytes)
                audio_dur = _audio_seconds(wav_bytes)

            # 3. Decide scene duration.
            #    - Voiceover on: extend to fit the spoken line.
            #    - Voiceover off: honour the planned seconds as-is.
            duration_s = max(float(planned_sec), audio_dur + 0.4) if voiceover else float(planned_sec)

            # 4. Captions.
            caption_specs: list[dict] = []
            caption_pngs:  list[Path] = []
            if captions:
                if caption_mode == "transcript" and use_video:
                    # Whisper on the scene's source video audio. Segments come
                    # back with absolute times from the source; since we're
                    # using -stream_loop -1 -t duration inside the scene, the
                    # source plays from t=0 within the scene until duration_s.
                    # We clip segments that spill past duration_s.
                    await on_progress({"stage": "voice", "scene": n, "total": total,
                                       "note": "transcribing…"})
                    try:
                        segs = await _transcribe.transcribe(
                            vid_path,  # type: ignore[arg-type]
                            translate=translate,
                        )
                    except Exception as exc:
                        log.warning("scene %d: transcript failed (%s) — using script line", n, exc)
                        segs = []
                    if segs:
                        for i2, seg in enumerate(segs):
                            start = float(seg["start"])
                            end   = min(float(seg["end"]), duration_s)
                            if end <= start or start >= duration_s:
                                continue
                            png = work_dir / f"scene_{n}_seg_{i2}.png"
                            _render_caption_png(
                                _wrap(seg["text"], wrap_chars),
                                font_path=font_path,
                                width=width, height=height,
                                out_path=png,
                            )
                            caption_specs.append({"start": start, "end": end})
                            caption_pngs.append(png)
                if not caption_specs:
                    # Either script mode, transcript unavailable, or image scene.
                    # One caption visible for the whole scene (from t=0 to duration_s).
                    text = script if script else ""
                    if text:
                        png = work_dir / f"scene_{n}_caption.png"
                        _render_caption_png(
                            _wrap(text, wrap_chars),
                            font_path=font_path,
                            width=width, height=height,
                            out_path=png,
                        )
                        caption_specs.append({"start": 0.0, "end": duration_s})
                        caption_pngs.append(png)

            # 5. Pick the audio source for this scene.
            #    Voice on  → Kokoro.
            #    Voice off + video scene         → source video's audio.
            #    Voice off + image scene + master → master video's audio
            #        (image covers the visual, master continues speaking).
            #    Voice off + image scene, no master → silent.
            has_master_audio = bool(
                master_audio_path
                and Path(master_audio_path).exists()
                and not use_video          # video scenes already carry their own audio
            )
            if voiceover:
                audio_src = "kokoro"
            elif use_video:
                audio_src = "video"
            elif has_master_audio:
                audio_src = "master_audio"
            else:
                audio_src = "silent"

            # 6. Render the scene MP4.
            await on_progress({"stage": "render", "scene": n, "total": total})
            scene_mp4 = work_dir / f"scene_{n}.mp4"
            if use_video:
                assert vid_path is not None
                await _render_scene_from_video(
                    video_path=vid_path,
                    audio_source=audio_src,
                    kokoro_wav=wav_path,
                    captions=caption_specs, caption_pngs=caption_pngs,
                    duration_s=duration_s,
                    width=width, height=height,
                    out_path=scene_mp4,
                    video_start=vid_start,
                    effects=scene_effects,
                )
            else:
                assert img_path is not None
                await _render_scene(
                    image_path=img_path,
                    audio_source=audio_src,
                    kokoro_wav=wav_path,
                    master_audio_path=(Path(master_audio_path) if has_master_audio else None),
                    master_audio_start=master_audio_start,
                    captions=caption_specs, caption_pngs=caption_pngs,
                    duration_s=duration_s,
                    width=width, height=height,
                    ken_burns=ken_burns,
                    out_path=scene_mp4,
                    effects=scene_effects,
                )
            scene_mp4s.append(scene_mp4)

        # 6. Concat
        await on_progress({"stage": "concat"})
        stitched = out_dir / f"{reel_id}.mp4"
        await _concat_scenes(scene_mp4s, stitched)

        # 7. Optional background music with ducking
        if music_path and Path(music_path).exists():
            await on_progress({"stage": "music"})
            mixed = out_dir / f"{reel_id}_music.mp4"
            await _mix_music(stitched, Path(music_path), mixed, music_volume)
            try:
                stitched.unlink()
            except OSError:
                pass
            stitched = mixed.rename(out_dir / f"{reel_id}.mp4")

        # 8. Sidecar metadata for the History tab
        (out_dir / f"{reel_id}.json").write_text(json.dumps({
            "id":       reel_id,
            "topic":    plan.get("topic", ""),
            "aspect":   aspect,
            "voice":    voice,
            "duration": sum(int(s.get("seconds", 4)) for s in scenes),
            "scenes":   [dict(s) for s in scenes],
            "createdAt": int(asyncio.get_running_loop().time() * 1000),
        }, indent=2), encoding="utf-8")

        await on_progress({"stage": "done", "url": f"/api/reels/media/{reel_id}.mp4", "id": reel_id})
        return stitched
    finally:
        # Keep work_dir on disk during dev — makes ffmpeg failures diagnosable.
        # Uncomment when the pipeline is stable:
        # shutil.rmtree(work_dir, ignore_errors=True)
        pass


def list_finished_reels() -> list[dict]:
    """Return sidecar metadata + URL for every rendered reel, newest first."""
    out_dir = _reels_dir() / "out"
    if not out_dir.exists():
        return []
    reels: list[dict] = []
    for j in out_dir.glob("*.json"):
        try:
            meta = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        mp4 = out_dir / f"{meta.get('id')}.mp4"
        if not mp4.exists():
            continue
        meta["url"]   = f"/api/reels/media/{meta['id']}.mp4"
        meta["bytes"] = mp4.stat().st_size
        reels.append(meta)
    reels.sort(key=lambda m: m.get("createdAt", 0), reverse=True)
    return reels


def reel_media_path(name: str) -> Path | None:
    """Resolve a media filename to a path under the reels output dir.

    Path-traversal safe: rejects anything with slashes or ..
    """
    if "/" in name or "\\" in name or ".." in name:
        return None
    p = _reels_dir() / "out" / name
    return p if p.exists() and p.is_file() else None
