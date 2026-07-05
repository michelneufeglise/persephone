"""
Thin ergonomic wrapper around `ffmpeg-python` for Persephone's video pipeline.

Why this exists:
  The renderer currently builds ffmpeg command lines by hand — long lists of
  strings + a `-filter_complex` graph as a raw f-string. That's fine for the
  hot render path (fastest possible, no abstraction cost) but it's cramped
  for anything that composes N filters conditionally.

  ffmpeg-python builds the same shell command line for us via a fluent API —
  chain `.filter()` calls, describe the graph as Python, then emit the args
  list. Same subprocess, same performance, same output file. Zero runtime
  overhead vs. hand-rolled strings.

Public surface (start small — grow as we need it):
  probe(path)                  → detailed metadata dict (streams, format, ...)
  duration_seconds(path)       → convenience shortcut around probe()
  concat_files(paths, out, ..) → replace _concat_scenes()
  compile(node, out_path, ...) → wrap a fluent graph and run it via _run_ff

Everything here is a *thin* wrapper. If ffmpeg-python's fluent API is
overkill for a call site, keep using the direct subprocess pattern — this
module is a helper, not a mandate.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import ffmpeg  # ffmpeg-python

log = logging.getLogger("ffmpeg_helper")


# ── Probing ───────────────────────────────────────────────────────────────────
def probe(path: Path | str) -> dict[str, Any]:
    """Return ffprobe's full JSON metadata for a media file."""
    try:
        return ffmpeg.probe(str(path))
    except ffmpeg.Error as exc:
        raise RuntimeError(f"ffprobe failed for {path}: {exc.stderr!r}") from exc


def duration_seconds(path: Path | str) -> float:
    """Total duration of the first video/audio stream in a media file."""
    info = probe(path)
    fmt  = info.get("format", {})
    if "duration" in fmt:
        try:
            return float(fmt["duration"])
        except (TypeError, ValueError):
            pass
    for s in info.get("streams", []):
        d = s.get("duration")
        if d:
            try:
                return float(d)
            except (TypeError, ValueError):
                continue
    return 0.0


def video_streams(path: Path | str) -> list[dict[str, Any]]:
    """Return the video streams of a file (usually 0 or 1)."""
    return [s for s in probe(path).get("streams", []) if s.get("codec_type") == "video"]


def audio_streams(path: Path | str) -> list[dict[str, Any]]:
    return [s for s in probe(path).get("streams", []) if s.get("codec_type") == "audio"]


def has_audio(path: Path | str) -> bool:
    return bool(audio_streams(path))


# ── Subprocess helper ─────────────────────────────────────────────────────────
async def _spawn(cmd: list[str], label: str) -> None:
    """Run an ffmpeg command async, raising on non-zero exit with a tail
    of stderr (mirrors the semantics of reels_render._run_ff)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = (stderr or b"").decode("utf-8", errors="replace").splitlines()[-30:]
        raise RuntimeError(f"ffmpeg [{label}] exited {proc.returncode}:\n" + "\n".join(tail))


async def compile(
    node: Any,
    out_path: Path,
    *,
    label: str = "run",
    overwrite: bool = True,
    quiet: bool = True,
    extra_output_args: dict[str, Any] | None = None,
) -> None:
    """
    Compile a fluent ffmpeg graph into a subprocess call and await it.

    Example:
        stream = ffmpeg.input("in.mp4").filter("scale", 1080, 1920).output("out.mp4")
        await compile(stream, Path("out.mp4"), label="scale")

    We take a node instead of calling `.run()` ourselves so we get:
      • real async subprocess (ffmpeg-python's `.run()` is blocking)
      • unified error handling with the rest of the render pipeline
      • ability to inject extra output args (`.output()` kwargs are also OK)
    """
    args = ffmpeg.compile(node, cmd="ffmpeg", overwrite_output=overwrite)
    if quiet:
        # ffmpeg-python doesn't add these by default. Suppress the banner and
        # bring the log level down to `error` so subprocess stderr stays clean.
        args[1:1] = ["-hide_banner", "-loglevel", "error"]
    await _spawn(args, label)


# ── Concat helper ─────────────────────────────────────────────────────────────
async def concat_files(
    inputs: list[Path],
    out_path: Path,
    *,
    reencode_audio: bool = True,
) -> None:
    """
    Losslessly concatenate a list of MP4 files by writing a temp list file
    and running ffmpeg's `concat` demuxer.

    Video is copied (fast); audio can be re-encoded to normalise frame
    boundaries — pass `reencode_audio=False` if you really want -c copy
    on both streams and know the inputs already have matching AAC profiles.

    Equivalent to (and replaces) reels_render._concat_scenes.
    """
    if not inputs:
        raise ValueError("concat_files: no inputs")
    list_path = out_path.parent / f"{out_path.stem}.txt"
    with list_path.open("w") as f:
        for p in inputs:
            # ffmpeg's concat demuxer requires POSIX-style single quotes.
            f.write(f"file '{p.as_posix()}'\n")

    try:
        node = ffmpeg.input(str(list_path), format="concat", safe=0)
        # ffmpeg-python's Output kwargs support `c:v` etc via generic passthrough.
        if reencode_audio:
            node = node.output(str(out_path), **{"c:v": "copy", "c:a": "aac", "b:a": "192k"})
        else:
            node = node.output(str(out_path), c="copy")
        await compile(node, out_path, label="concat")
    finally:
        try:
            list_path.unlink()
        except OSError:
            pass


# ── Sidechain-ducked music mix ────────────────────────────────────────────────
async def mix_music_ducked(
    video_path:      Path,
    music_path:      Path,
    out_path:        Path,
    *,
    music_volume:    float = 0.18,
    threshold:       float = 0.05,
    ratio:           float = 8,
    attack_ms:       int   = 15,
    release_ms:      int   = 350,
    makeup:          float = 1.0,
    stream_loop:     bool  = True,
) -> None:
    """
    Mix `music_path` under `video_path`'s audio with sidechain compression.

    The graph:
        [music_in].audio → volume(vol)                       ─┐
                                                              ├─ sidechaincompress ─┐
        [video_in].audio (voice / narration) ─────────────────┘                     │
                                                                                    ├─ amix ─→ output audio
        [video_in].audio (raw voice) ──────────────────────────────────────────────┘

    `sidechaincompress` uses the voice track as the "listener" — as soon as
    speech starts, the music duck; when speech pauses, music comes back up.

    Video track is copied. Audio is re-encoded to AAC 192 kbps.
    """
    vol = max(0.0, min(1.0, float(music_volume)))

    video_in = ffmpeg.input(str(video_path))
    music_kwargs: dict[str, Any] = {"stream_loop": -1} if stream_loop else {}
    music_in = ffmpeg.input(str(music_path), **music_kwargs)

    voice        = video_in.audio                              # source audio
    music_scaled = music_in.audio.filter("volume", vol)         # attenuated bed
    # sidechaincompress: MAIN is the music we want to duck; SIDECHAIN is voice.
    music_ducked = ffmpeg.filter(
        [music_scaled, voice],
        "sidechaincompress",
        threshold=threshold, ratio=ratio,
        attack=attack_ms,    release=release_ms,
        makeup=makeup,
    )
    # amix: layer ducked music + voice into a single output track.
    mixed = ffmpeg.filter(
        [music_ducked, voice],
        "amix",
        inputs=2, duration="first", dropout_transition=0,
    )

    node = ffmpeg.output(
        video_in.video, mixed, str(out_path),
        **{"c:v": "copy", "c:a": "aac", "b:a": "192k"},
        shortest=None,       # emit `-shortest` without a value
    )
    await compile(node, out_path, label="mix-music")
