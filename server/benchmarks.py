"""
Per-model tok/s estimator for the setup wizard.

Given a hardware profile (from `hardware.get_hardware()`) and a model spec
(from `model_catalog.MODELS`), estimate the tokens-per-second the user
will see. Used to filter the wizard's recommendation list down to models
that actually clear a throughput bar (default: 20 tok/s).

Model tokens-per-second is bottlenecked by memory bandwidth on consumer
hardware — CPU/GPU compute is rarely saturated for the batch-size-1
inference that a chat model does. So the first-order estimate is:

    tok/s ≈ bandwidth_gb_s * efficiency / active_size_gb

Where:
  * `bandwidth_gb_s`  — from `hardware._estimate_bandwidth()`.
  * `active_size_gb`  — model weight bytes that must be READ from memory
                        per token. For dense models this is the full quant
                        size. For MoE models it's the active-params size
                        (Qwen3.6-35B-A3B reads only ~1.9GB per token even
                        though it's 22GB on disk).
  * `efficiency`      — 0.55-0.85, real-world losses vs peak bandwidth.

An override table (`_HARD_OVERRIDES`) captures (chip, model) pairs where
this formula is known to be off — long-context models, quant-specific
performance, community-reported numbers.
"""

from __future__ import annotations

import re
from typing import Any


# ── Per-family tuning knobs ─────────────────────────────────────────────────
# `efficiency`  — how much of peak bandwidth the model realises. Reasoning /
# thinking models tend to be lower because they emit lots of small hidden
# tokens (KV cache pressure).
# `moe_active_fraction` — for models tagged as MoE, the fraction of total
# weights actually read per token. Qwen3.6-A3B → 3/35 ≈ 0.086.

_DEFAULT_EFFICIENCY   = 0.70
_THINKER_EFFICIENCY   = 0.55   # long thinking chains fragment the KV cache
_MOE_EFFICIENCY       = 0.80   # MoE routing is efficient once warm

# Models whose IDs contain any of these substrings are treated as MoE.
_MOE_PATTERNS = (
    "a3b", "moe", "mixtral", "8x7b", "8x22b", "nemotron-3-nano",
)

# Models treated as long-thinking / reasoning families (lower efficiency).
_THINKER_PATTERNS = (
    "deepseek-r1", "qwen3.6", "qwen3", "agentworld", "nemotron",
    "ornith", "gpt-oss",
)


def _is_moe(model_id: str) -> bool:
    low = model_id.lower()
    return any(p in low for p in _MOE_PATTERNS)


def _is_thinker(model_id: str) -> bool:
    low = model_id.lower()
    return any(p in low for p in _THINKER_PATTERNS)


def _parse_moe_active_gb(size_gb: float, params: str) -> float:
    """
    Parse '35B-A3B' → active_gb = size_gb * (3/35).
    Falls back to 15% of size (heuristic) if we can't parse the label.
    """
    m = re.search(r"(\d+)B?-A(\d+)B?", params, re.IGNORECASE)
    if m:
        total, active = float(m.group(1)), float(m.group(2))
        if total > 0:
            return size_gb * (active / total)
    return size_gb * 0.15


# ── Hard-coded overrides ────────────────────────────────────────────────────
# (chip_family, chip_variant, model_id_substring) → measured tok/s.
# Numbers from community reports (Reddit r/LocalLLaMA, GitHub issues,
# HuggingFace discussions). Only used to correct the formula where it's
# known to be off — most model/chip combos use the formula directly.
_HARD_OVERRIDES: dict[tuple[str, str, str], float] = {
    # DeepSeek-R1 70B is a beast — well-benchmarked on M-series
    ("apple_silicon_m1",  "max",   "deepseek-r1:70b"): 5.0,
    ("apple_silicon_m2",  "max",   "deepseek-r1:70b"): 6.0,
    ("apple_silicon_m3",  "max",   "deepseek-r1:70b"): 6.5,
    ("apple_silicon_m4",  "max",   "deepseek-r1:70b"): 9.5,
    # Llama 3.3 70B
    ("apple_silicon_m1",  "max",   "llama3.3:70b"):    5.5,
    ("apple_silicon_m2",  "max",   "llama3.3:70b"):    6.5,
    ("apple_silicon_m3",  "max",   "llama3.3:70b"):    7.5,
    ("apple_silicon_m4",  "max",   "llama3.3:70b"):    11.0,
    # Euryale — same base as Llama 3.3 70B
    ("apple_silicon_m1",  "max",   "euryale"):         5.0,
    ("apple_silicon_m2",  "max",   "euryale"):         6.0,
    ("apple_silicon_m3",  "max",   "euryale"):         7.0,
    ("apple_silicon_m4",  "max",   "euryale"):         10.5,
    # Qwen 3.6 35B-A3B — the MoE star. Very fast for its size.
    ("apple_silicon_m1",  "max",   "qwen3.6:35b-a3b"): 45.0,
    ("apple_silicon_m2",  "max",   "qwen3.6:35b-a3b"): 55.0,
    ("apple_silicon_m3",  "max",   "qwen3.6:35b-a3b"): 60.0,
    ("apple_silicon_m4",  "max",   "qwen3.6:35b-a3b"): 80.0,
    ("apple_silicon_m1",  "pro",   "qwen3.6:35b-a3b"): 30.0,
    ("apple_silicon_m2",  "pro",   "qwen3.6:35b-a3b"): 35.0,
    # Ornith (9B Qwen3 dense) — surprisingly fast because native tools + thinking
    ("apple_silicon_m1",  "max",   "ornith"):          40.0,
    ("apple_silicon_m1",  "pro",   "ornith"):          32.0,
}


def _override_lookup(chip_family: str, chip_variant: str, model_id: str) -> float | None:
    low = model_id.lower()
    for (fam, var, needle), val in _HARD_OVERRIDES.items():
        if fam == chip_family and var == chip_variant and needle in low:
            return val
    return None


def estimate_tok_per_s(model: dict[str, Any], profile: dict[str, Any]) -> float:
    """
    Return estimated tokens-per-second for `model` running on `profile`.

    `model` shape (from model_catalog.MODELS):
        {"id", "params", "size_gb", "ram_min_gb", ...}
    `profile` shape (from hardware.get_hardware()):
        {"chip_family", "chip_variant", "mem_bandwidth_gb_s", "ram_gb", ...}

    Returns 0.0 if the model definitively can't run (ram_min_gb > available).
    """
    ram_gb        = float(profile.get("ram_gb") or 0)
    ram_min       = float(model.get("ram_min_gb") or 0)
    size_gb       = float(model.get("size_gb") or 0)
    if ram_gb and ram_min and ram_min > ram_gb:
        return 0.0
    if size_gb <= 0:
        return 0.0

    # Explicit override wins if we have one.
    ovr = _override_lookup(
        profile.get("chip_family", "unknown"),
        profile.get("chip_variant", "n/a"),
        model.get("id", ""),
    )
    if ovr is not None:
        return ovr

    bw = float(profile.get("mem_bandwidth_gb_s") or 40)
    active_gb = _parse_moe_active_gb(size_gb, str(model.get("params", ""))) \
        if _is_moe(model.get("id", "")) else size_gb

    if _is_moe(model.get("id", "")):
        eff = _MOE_EFFICIENCY
    elif _is_thinker(model.get("id", "")):
        eff = _THINKER_EFFICIENCY
    else:
        eff = _DEFAULT_EFFICIENCY

    est = (bw * eff) / max(0.5, active_gb)

    # Sanity floor + ceiling. Nothing on consumer hardware runs >250 tok/s
    # for a chat model; if the formula suggests it, we're wrong about the
    # active-params calc.
    return max(0.5, min(250.0, est))


def fit_rating(tok_per_s: float, min_target: float = 20.0) -> str:
    """
    Human-friendly rating string:
      'top'        — 2× the target or better
      'good'       — meets target
      'acceptable' — half target to target
      'slow'       — below half target
      'unsupported'— can't run at all
    """
    if tok_per_s <= 0:                     return "unsupported"
    if tok_per_s >= 2 * min_target:        return "top"
    if tok_per_s >= min_target:            return "good"
    if tok_per_s >= min_target / 2:        return "acceptable"
    return "slow"
