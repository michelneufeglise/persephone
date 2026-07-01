"""
Kokoro-82M TTS engine (ONNX runtime).

Loads once at startup, stays in-process. Produces 24kHz mono float32 audio
natively, ~10× real-time on M-series. Total model weight: ~360MB.

Model + voice pack are downloaded on first launch to the writable data dir
so builds don't need to bake them in.

Public API:
  VOICES              — curated list of voice dicts (id/name/gender/description)
  VALID_VOICES        — set of valid voice IDs
  preload_pipeline()  — warms the ONNX session (call at startup)
  synthesize(text, voice, speed) → WAV bytes
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve

import numpy as np

from paths import data_dir

log = logging.getLogger("tts_engine")

SAMPLE_RATE  = 24_000
DEFAULT_VOICE = "af_heart"

# ── Curated voice catalog ─────────────────────────────────────────────────────
# All included in the standard Kokoro v1.0 voice pack — no extra downloads.
# Prefix legend: af/am = American female/male, bf/bm = British female/male
VOICES = [
    # American female
    {"id": "af_heart",   "name": "Heart",   "gender": "female", "accent": "US",
     "description": "Warm, inviting default — richest expressive range"},
    {"id": "af_bella",   "name": "Bella",   "gender": "female", "accent": "US",
     "description": "Bright, playful, youthful"},
    {"id": "af_nicole",  "name": "Nicole",  "gender": "female", "accent": "US",
     "description": "Clear, articulate, professional"},
    {"id": "af_sarah",   "name": "Sarah",   "gender": "female", "accent": "US",
     "description": "Calm, measured, thoughtful"},
    {"id": "af_sky",     "name": "Sky",     "gender": "female", "accent": "US",
     "description": "Airy, gentle, dreamlike"},
    {"id": "af_aoede",   "name": "Aoede",   "gender": "female", "accent": "US",
     "description": "Melodic, poetic cadence"},

    # American male
    {"id": "am_adam",    "name": "Adam",    "gender": "male",   "accent": "US",
     "description": "Deep, grounded, resonant"},
    {"id": "am_michael", "name": "Michael", "gender": "male",   "accent": "US",
     "description": "Confident, mid-tone, versatile"},
    {"id": "am_liam",    "name": "Liam",    "gender": "male",   "accent": "US",
     "description": "Smooth, conversational"},
    {"id": "am_puck",    "name": "Puck",    "gender": "male",   "accent": "US",
     "description": "Playful, mischievous edge"},

    # British female
    {"id": "bf_emma",    "name": "Emma",    "gender": "female", "accent": "UK",
     "description": "Refined RP, softly formal"},
    {"id": "bf_alice",   "name": "Alice",   "gender": "female", "accent": "UK",
     "description": "Warm British, storyteller tone"},
    {"id": "bf_isabella","name": "Isabella","gender": "female", "accent": "UK",
     "description": "Elegant, precise, aristocratic"},

    # British male
    {"id": "bm_george",  "name": "George",  "gender": "male",   "accent": "UK",
     "description": "Deep RP, grave & authoritative"},
    {"id": "bm_daniel",  "name": "Daniel",  "gender": "male",   "accent": "UK",
     "description": "Professional, news-anchor timbre"},
    {"id": "bm_fable",   "name": "Fable",   "gender": "male",   "accent": "UK",
     "description": "Warm, narrative — perfect for stories"},
]
VALID_VOICES = {v["id"] for v in VOICES}


# ── Model file locations ──────────────────────────────────────────────────────
_MODEL_URL   = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_VOICES_URL  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"


def _kokoro_dir() -> Path:
    d = data_dir() / "kokoro"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _model_path()  -> Path: return _kokoro_dir() / "kokoro-v1.0.onnx"
def _voices_path() -> Path: return _kokoro_dir() / "voices-v1.0.bin"


def _download_if_missing() -> None:
    for url, path, label in [
        (_MODEL_URL,  _model_path(),  "kokoro-v1.0.onnx"),
        (_VOICES_URL, _voices_path(), "voices-v1.0.bin"),
    ]:
        if path.exists() and path.stat().st_size > 1_000_000:
            continue
        log.info("Downloading Kokoro %s → %s", label, path)
        try:
            urlretrieve(url, path)
        except Exception as exc:
            if path.exists():
                path.unlink(missing_ok=True)
            log.error("Kokoro download failed for %s: %s", label, exc)
            raise


# ── Kokoro singleton ──────────────────────────────────────────────────────────
_kokoro: Any = None
_load_lock = asyncio.Lock()


def _load_kokoro_sync() -> Any:
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    _download_if_missing()
    log.info("Loading Kokoro-82M ONNX runtime…")
    from kokoro_onnx import Kokoro
    _kokoro = Kokoro(str(_model_path()), str(_voices_path()))
    log.info("Kokoro ready ✓  (24kHz, %d voices in pack)", len(VOICES))
    return _kokoro


def preload_pipeline() -> None:
    """Called by the FastAPI lifespan on startup so the first request is instant."""
    _load_kokoro_sync()


# Backwards-compat alias — main.py still calls load_snac()
load_snac = preload_pipeline


# ── WAV helper ────────────────────────────────────────────────────────────────
def _to_wav(pcm: np.ndarray, sr: int = SAMPLE_RATE) -> bytes:
    pcm_i16 = (pcm * 32767.0).clip(-32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm_i16.tobytes())
    return buf.getvalue()


def _lang_for_voice(voice_id: str) -> str:
    """Kokoro's `create()` takes an eSpeak lang code."""
    if voice_id.startswith(("bf_", "bm_")):
        return "en-gb"
    return "en-us"


# ── Main public API ───────────────────────────────────────────────────────────
async def synthesize(text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> bytes:
    """text → 16-bit mono 24kHz WAV bytes."""
    if voice not in VALID_VOICES:
        log.warning("unknown voice %r — falling back to %s", voice, DEFAULT_VOICE)
        voice = DEFAULT_VOICE

    async with _load_lock:
        if _kokoro is None:
            await asyncio.to_thread(_load_kokoro_sync)
    kokoro = _kokoro
    assert kokoro is not None

    lang = _lang_for_voice(voice)
    log.info("TTS: voice=%s speed=%.2f  len=%d chars", voice, speed, len(text))

    def _generate() -> tuple[np.ndarray, int]:
        return kokoro.create(text=text, voice=voice, speed=speed, lang=lang)

    pcm, sr = await asyncio.to_thread(_generate)
    if pcm.size == 0:
        raise RuntimeError("Kokoro returned no audio")

    # Peak safety — a few voices come close to clipping on emphatic syllables
    peak = float(np.abs(pcm).max())
    if peak > 0.98:
        pcm = pcm * (0.95 / peak)

    wav = _to_wav(pcm.astype(np.float32, copy=False), sr or SAMPLE_RATE)
    log.info("TTS: %.2fs audio  %d bytes  peak=%.0f%%",
             len(pcm) / (sr or SAMPLE_RATE), len(wav), 100 * peak)
    return wav
