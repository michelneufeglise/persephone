#!/usr/bin/env python3
"""
Orpheus TTS decoder for Persephone.
Calls legraphista/Orpheus:3b-ft-q4_k_m via Ollama, decodes SNAC tokens to WAV.

Token frame structure (7 tokens per audio frame, SNAC 24kHz):
  [c0, c1_a, c2_a, c2_b, c1_b, c2_c, c2_d]
  Layer 0 offset: 4       (codebook 0, 4096 codes)
  Layer 1 offset: 4100    (codebook 1, 1st slot)
  Layer 2 offset: 8196    (codebook 2, 1st slot)
  Layer 2 offset: 12292   (codebook 2, 2nd slot)
  Layer 1 offset: 16388   (codebook 1, 2nd slot)
  Layer 2 offset: 20484   (codebook 2, 3rd slot)
  Layer 2 offset: 24580   (codebook 2, 4th slot)
"""

import sys
import json
import re
import io
import wave
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434"
MODEL = "legraphista/Orpheus:3b-ft-q4_k_m"
SAMPLE_RATE = 24000
_snac_model = None


def get_snac():
    global _snac_model
    if _snac_model is None:
        import torch
        from snac import SNAC
        _snac_model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval()
    return _snac_model


def generate_orpheus_tokens(text: str, voice: str) -> list[int]:
    prompt = f"<|audio|><voice:{voice}>{text}<|eot_id|>"
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.6,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "num_predict": 8192,
        },
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())

    response_text = result.get("response", "")
    tokens = [int(t) for t in re.findall(r"<custom_token_(\d+)>", response_text)]
    print(f"[tts] generated {len(tokens)} tokens", file=sys.stderr)
    return tokens


def find_audio_start(tokens: list[int]) -> tuple[int, list[int]]:
    """Skip header tokens; find first valid 7-token SNAC frame."""
    filtered = [t for t in tokens if t not in (0, 1, 2, 3)]
    for i in range(len(filtered) - 6):
        f = filtered[i:i + 7]
        if (4     <= f[0] <= 4099  and
            4100  <= f[1] <= 8195  and
            8196  <= f[2] <= 12291 and
            12292 <= f[3] <= 16387 and
            16388 <= f[4] <= 20483 and
            20484 <= f[5] <= 24579 and
            24580 <= f[6] <= 28675):
            return i, filtered
    return -1, filtered


def decode_to_pcm(tokens: list[int]) -> "np.ndarray":
    import numpy as np
    import torch

    start, filtered = find_audio_start(tokens)
    if start < 0:
        print("[tts] no valid audio frames found", file=sys.stderr)
        return np.zeros(SAMPLE_RATE // 4, dtype=np.float32)

    audio_tokens = filtered[start:]
    layer0, layer1, layer2 = [], [], []

    for i in range(0, len(audio_tokens) - 6, 7):
        f = audio_tokens[i:i + 7]
        if len(f) < 7:
            break
        c0   = f[0] - 4
        c1_a = f[1] - 4100
        c2_a = f[2] - 8196
        c2_b = f[3] - 12292
        c1_b = f[4] - 16388
        c2_c = f[5] - 20484
        c2_d = f[6] - 24580

        if not all(0 <= v <= 4095 for v in (c0, c1_a, c2_a, c2_b, c1_b, c2_c, c2_d)):
            continue

        layer0.append(c0)
        layer1.extend([c1_a, c1_b])
        layer2.extend([c2_a, c2_b, c2_c, c2_d])

    if not layer0:
        return np.zeros(SAMPLE_RATE // 4, dtype=np.float32)

    print(f"[tts] decoding {len(layer0)} frames via SNAC", file=sys.stderr)

    snac = get_snac()
    codes = [
        torch.tensor([layer0], dtype=torch.long),
        torch.tensor([layer1], dtype=torch.long),
        torch.tensor([layer2], dtype=torch.long),
    ]
    with torch.inference_mode():
        audio = snac.decode(codes)

    return audio.squeeze().float().numpy()


def to_wav_bytes(pcm, sample_rate: int = SAMPLE_RATE, speed: float = 1.0) -> bytes:
    import numpy as np

    # Speed adjustment via resampling (simple linear interp)
    if speed != 1.0 and speed > 0:
        new_len = int(len(pcm) / speed)
        pcm = np.interp(
            np.linspace(0, len(pcm) - 1, new_len),
            np.arange(len(pcm)),
            pcm,
        ).astype(np.float32)

    peak = np.abs(pcm).max()
    if peak > 0:
        pcm = pcm / peak * 0.95

    pcm_i16 = (pcm * 32767).clip(-32768, 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16.tobytes())
    return buf.getvalue()


def main():
    text  = sys.argv[1] if len(sys.argv) > 1 else "Hello, I am Persephone."
    voice = sys.argv[2] if len(sys.argv) > 2 else "tara"
    speed = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0

    valid_voices = {"tara", "leo", "leah", "jess", "mia", "zac", "zoe", "zach"}
    if voice not in valid_voices:
        voice = "tara"

    try:
        tokens = generate_orpheus_tokens(text, voice)
        pcm    = decode_to_pcm(tokens)
        wav    = to_wav_bytes(pcm, speed=speed)
        sys.stdout.buffer.write(wav)
    except Exception as e:
        print(f"[tts] error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
