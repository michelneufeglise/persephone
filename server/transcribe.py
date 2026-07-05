"""
Speech-to-text + optional translation via openai-whisper.

Public API:
  is_available()                     — quick check for import/model download
  transcribe(path, translate=False)  — returns [{start, end, text}, ...]

The `translate` flag flips Whisper into translate-to-English mode. Any input
language is transcribed *and* rendered in English in a single pass. The model
is loaded lazily and cached process-wide.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("transcribe")

# Small enough to download quickly (~150 MB) and accurate enough for reels
# where audio is usually spoken, single-language, clean phone footage.
_WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")

_model: Any = None
_lock  = asyncio.Lock()


def is_available() -> bool:
    """True if openai-whisper is importable — model download happens on first use."""
    try:
        import whisper  # noqa: F401
        return True
    except Exception:
        return False


async def _load() -> Any:
    """Load (once, in-process) and return a whisper model instance."""
    global _model
    async with _lock:
        if _model is None:
            import whisper
            log.info("Loading Whisper %s (first run downloads ~150 MB)…", _WHISPER_MODEL_NAME)
            # whisper.load_model is blocking → run in a thread so we don't
            # freeze the FastAPI event loop while it deserialises.
            _model = await asyncio.to_thread(whisper.load_model, _WHISPER_MODEL_NAME)
            log.info("Whisper %s ready", _WHISPER_MODEL_NAME)
    return _model


async def transcribe(
    audio_or_video: Path,
    *,
    translate: bool = False,
    source_language: str | None = None,
) -> list[dict]:
    """
    Return `[{start: float, end: float, text: str}, …]`.

    - `translate=True` → Whisper's built-in translate mode: any source
      language → English text. No extra translation step needed.
    - `source_language` is optional; leaving it None lets Whisper autodetect
      (adds a small overhead but is more forgiving of mixed-language input).
    """
    if not is_available():
        raise RuntimeError(
            "openai-whisper is not installed. `pip install openai-whisper` "
            "(or add it to server/requirements.txt and re-install)."
        )

    model = await _load()

    kwargs: dict = {
        "task":       "translate" if translate else "transcribe",
        "verbose":    False,
        "word_timestamps": False,
        "fp16":       False,   # Consistency across Apple Silicon / CPU.
    }
    if source_language:
        kwargs["language"] = source_language

    # Whisper.transcribe is CPU/GPU-bound; run it in a worker thread.
    result = await asyncio.to_thread(model.transcribe, str(audio_or_video), **kwargs)
    segments = result.get("segments") or []
    return [
        {
            "start": float(s.get("start", 0.0)),
            "end":   float(s.get("end",   0.0)),
            "text":  str(s.get("text", "")).strip(),
        }
        for s in segments
        if str(s.get("text", "")).strip()
    ]
