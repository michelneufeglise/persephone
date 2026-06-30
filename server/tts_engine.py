"""
Orpheus TTS engine — loads SNAC once at startup, stays in-process forever.
Fully offline after first HuggingFace cache population.

Audio pipeline: SNAC decode → DC removal → silence trim → peak-norm 0.85 → int16 WAV
"""

from __future__ import annotations

import io
import os
import re
import wave
import logging
from pathlib import Path

# ── Block ALL HuggingFace network calls ───────────────────────────────────────
os.environ.setdefault("HF_HUB_OFFLINE",      "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

import numpy as np
import torch

import hardware as _hw

log = logging.getLogger("tts_engine")

OLLAMA_BASE   = os.getenv("OLLAMA_HOST", "http://localhost:11434")
ORPHEUS_MODEL = "legraphista/Orpheus:3b-ft-q4_k_m"
SAMPLE_RATE   = 24_000

# Token frame structure (7 tokens / SNAC frame, 1:2:4 codebook ratio)
# Frame positions: [c0, c1_a, c2_a, c2_b, c1_b, c2_c, c2_d]
#
# From the official Canopy Labs Orpheus reference:
#   codebook_index = raw_token - 10 - ((index % 7) * 4096)
# → per-position offsets are: 10, 4106, 8202, 12298, 16394, 20490, 24586
# Each subtraction maps the raw token into the SNAC codebook range [0, 4095].
_OFFSETS = (10, 4106, 8202, 12298, 16394, 20490, 24586)

VOICES = [
    {"id": "tara",  "name": "Tara",  "gender": "female", "description": "Warm & inviting"},
    {"id": "leo",   "name": "Leo",   "gender": "male",   "description": "Confident & clear"},
    {"id": "leah",  "name": "Leah",  "gender": "female", "description": "Gentle & soft"},
    {"id": "jess",  "name": "Jess",  "gender": "female", "description": "Energetic & bright"},
    {"id": "mia",   "name": "Mia",   "gender": "female", "description": "Smooth & calm"},
    {"id": "zac",   "name": "Zac",   "gender": "male",   "description": "Deep & resonant"},
    {"id": "zoe",   "name": "Zoe",   "gender": "female", "description": "Crisp & expressive"},
    {"id": "zach",  "name": "Zach",  "gender": "male",   "description": "Warm & conversational"},
]
VALID_VOICES = {v["id"] for v in VOICES}

# ── Singleton SNAC model ───────────────────────────────────────────────────────
_snac = None

def load_snac():
    global _snac
    if _snac is not None:
        return _snac
    from snac import SNAC
    log.info("Loading SNAC 24kHz model from local cache …")
    try:
        _snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval()
    except Exception as exc:
        log.error("SNAC load failed. Run: python3 server/download_models.py   (%s)", exc)
        raise
    log.info("SNAC model loaded ✓  (kept in-process for fast TTS)")
    return _snac


# ── Token extraction & frame decoding ─────────────────────────────────────────
def _extract_tokens(text: str) -> list[int]:
    return [int(t) for t in re.findall(r"<custom_token_(\d+)>", text)]


def _find_frame_start(tokens: list[int]) -> int:
    """Find the index of the first valid 7-token SNAC audio frame."""
    for i in range(len(tokens) - 6):
        f = tokens[i:i + 7]
        if all(_OFFSETS[j] <= f[j] <= _OFFSETS[j] + 4095 for j in range(7)):
            return i
    return -1


def _decode_frames(tokens: list[int]) -> tuple[list, list, list]:
    """Convert raw Orpheus token stream → SNAC (layer0, layer1, layer2) index lists."""
    # Remove special tokens (0-3 are BOS/EOS/pad; 4,5 are audio header markers)
    filtered = [t for t in tokens if t >= 4]

    start = _find_frame_start(filtered)
    if start < 0:
        return [], [], []

    audio = filtered[start:]
    l0, l1, l2 = [], [], []

    i = 0
    while i + 6 < len(audio):
        f = audio[i:i + 7]

        c0   = f[0] - _OFFSETS[0]
        c1_a = f[1] - _OFFSETS[1]
        c2_a = f[2] - _OFFSETS[2]
        c2_b = f[3] - _OFFSETS[3]
        c1_b = f[4] - _OFFSETS[4]
        c2_c = f[5] - _OFFSETS[5]
        c2_d = f[6] - _OFFSETS[6]

        if all(0 <= v <= 4095 for v in (c0, c1_a, c2_a, c2_b, c1_b, c2_c, c2_d)):
            l0.append(c0)
            l1.extend([c1_a, c1_b])
            l2.extend([c2_a, c2_b, c2_c, c2_d])
            i += 7
        else:
            i += 1          # re-sync past any stray token

    return l0, l1, l2


# ── Audio post-processing (minimal — avoid distortion) ────────────────────────
def _post_process(pcm: np.ndarray, sr: int = SAMPLE_RATE, speed: float = 1.0) -> np.ndarray:
    """
    Clean up SNAC output without introducing artifacts.
    Deliberately minimal: bad processing is worse than no processing.
    """
    if len(pcm) < 64:
        return pcm

    # 1. Remove DC offset (safe, zero-latency)
    pcm = pcm - float(pcm.mean())

    # 2. Trim leading/trailing near-silence
    peak = float(np.abs(pcm).max())
    if peak > 1e-6:
        thresh = peak * 0.008          # 0.8% of peak as silence floor
        nz = np.where(np.abs(pcm) > thresh)[0]
        if len(nz):
            margin = int(sr * 0.025)   # 25 ms padding each side
            s = max(0, nz[0]  - margin)
            e = min(len(pcm), nz[-1] + margin + 1)
            pcm = pcm[s:e]

    # 3. Speed via polyphase resampling (quality > linear interp)
    if abs(speed - 1.0) > 0.02:
        from scipy.signal import resample_poly
        from fractions import Fraction
        frac = Fraction(1, speed).limit_denominator(200)
        pcm = resample_poly(pcm, frac.numerator, frac.denominator).astype(np.float32)

    # 4. Peak-normalise to 0.85 — stays well below int16 ceiling, zero distortion
    peak = float(np.abs(pcm).max())
    if peak > 1e-6:
        pcm = (pcm / peak * 0.85).astype(np.float32)

    return pcm


def _to_wav(pcm: np.ndarray, sr: int = SAMPLE_RATE) -> bytes:
    """Float32 PCM → 16-bit mono WAV bytes."""
    pcm_i16 = (pcm * 32767.0).clip(-32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm_i16.tobytes())
    return buf.getvalue()


# ── Main public API ────────────────────────────────────────────────────────────
async def synthesize(text: str, voice: str = "tara", speed: float = 1.0) -> bytes:
    """
    Full TTS pipeline: text → Orpheus tokens → SNAC decode → clean → WAV.
    """
    import httpx

    if voice not in VALID_VOICES:
        voice = "tara"

    prompt = f"<|audio|><voice:{voice}>{text}<|eot_id|>"
    log.info("TTS: voice=%s  len=%d chars", voice, len(text))

    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": ORPHEUS_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature":    0.3,      # lower = more stable, fewer artifacts
                    "top_p":          0.9,
                    "repeat_penalty": 1.1,
                    "num_predict":    8192,
                    "num_thread":     _hw.recommended_num_thread(),
                },
            },
        )
        resp.raise_for_status()

    raw_tokens = _extract_tokens(resp.json().get("response", ""))
    log.info("TTS: %d tokens from Orpheus", len(raw_tokens))
    if not raw_tokens:
        raise RuntimeError("Orpheus returned no audio tokens")

    l0, l1, l2 = _decode_frames(raw_tokens)
    if not l0:
        raise RuntimeError("No valid SNAC frames in token stream")

    log.info("TTS: decoding %d frames via SNAC", len(l0))
    snac_model = load_snac()
    codes = [
        torch.tensor([l0], dtype=torch.long),
        torch.tensor([l1], dtype=torch.long),
        torch.tensor([l2], dtype=torch.long),
    ]
    with torch.inference_mode():
        audio_tensor = snac_model.decode(codes)

    pcm = audio_tensor.squeeze().float().numpy()
    pcm = _post_process(pcm, SAMPLE_RATE, speed)

    wav = _to_wav(pcm, SAMPLE_RATE)
    log.info("TTS: %.2fs audio  %d bytes  peak=%.0f%%",
             len(pcm) / SAMPLE_RATE, len(wav),
             100 * float(np.abs(pcm).max()))
    return wav
