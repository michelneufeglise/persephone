"""
Rhythm primitives — Euclidean distributions, common templates, swing.

Everything here operates on *step-position lists* (integer positions in a
grid of size `steps_per_bar`), which style_adapters converts into
`song_spec.Note` events with velocities and pitches.

Why step-lists rather than note events directly:
- Same distribution reused across kick / snare / hat with different pitches.
- Cheap to rotate for offset and to combine (union, difference).
- Trivial to swing-quantise before pitch assignment.
"""

from __future__ import annotations

from typing import Sequence


# ── Euclidean distribution ──────────────────────────────────────────────────
def euclidean(k: int, n: int, rotation: int = 0) -> list[int]:
    """
    Distribute `k` pulses as evenly as possible across `n` steps.

    Returns a list of integer step positions in [0, n).
    `rotation` shifts the pattern forwards (mod n) — handy for aligning
    E(2,4) with beats 2 and 4 (backbeat), which is rotation=1.

    This is the closed-form Bresenham-style variant: pulse `i` sits at
    floor(i * n / k). Produces the same distributions as Bjorklund for
    every case we care about, and is trivially rotation-friendly.

    Standard patterns you can derive:
        E(4, 16)          = [0, 4, 8, 12]        four-on-floor kick
        E(2, 4)  rot=1    = [1, 3]               backbeat snare (beats 2 and 4)
        E(3, 8)           = [0, 3, 5]            tresillo
        E(5, 8)           = [0, 1, 3, 4, 6]      cinquillo (Latin son)
        E(4, 8)  rot=1    = [1, 3, 5, 7]         offbeat 8ths
    """
    if k <= 0 or n <= 0:
        return []
    if k >= n:
        return [(i + rotation) % n for i in range(n)]
    return sorted({(i * n // k + rotation) % n for i in range(k)})


# ── Convert step positions to beat-time ──────────────────────────────────────
def positions_to_beats(
    positions: Sequence[int], bars: int, steps_per_bar: int = 16,
    beats_per_bar: float = 4.0,
) -> list[float]:
    """Repeat a bar-length pattern across `bars` bars and turn step indices
    into floating-point beat positions."""
    step_len = beats_per_bar / steps_per_bar
    out: list[float] = []
    for b in range(bars):
        base = b * beats_per_bar
        for p in positions:
            out.append(base + p * step_len)
    return out


# ── Swing quantisation ──────────────────────────────────────────────────────
def swing(beats: Sequence[float], amount: float = 0.55,
          subdivision: int = 8) -> list[float]:
    """
    Shift the offbeat subdivisions later in time to approximate a shuffle
    feel. `amount` is 0.5 → straight, 0.667 → hard triplet swing;
    typical lo-fi is around 0.55, typical jazz 0.60.
    """
    if not (0.5 < amount < 0.85):
        return list(beats)
    period = 1.0 / (subdivision / 4.0)   # length of one subdivision in beats
    offbeat_offset = (amount - 0.5) * period * 2
    out: list[float] = []
    for b in beats:
        # Which subdivision is this? Even = downbeat, odd = offbeat.
        step = round(b / period)
        if step % 2 == 1:
            out.append(b + offbeat_offset)
        else:
            out.append(b)
    return out


# ── Named rhythmic templates ────────────────────────────────────────────────
# Each returns a list of step positions in a bar of `steps` steps.

def four_on_floor(steps: int = 16) -> list[int]:
    return euclidean(4, steps)


def backbeat(steps: int = 16) -> list[int]:
    # Beats 2 and 4 in a 4-beat bar.
    return [steps // 4, 3 * steps // 4]


def offbeat_eights(steps: int = 16) -> list[int]:
    """The 'and' of each beat: positions 2, 6, 10, 14 on a 16-step grid."""
    return list(range(steps // 8, steps, steps // 4))


def tresillo(steps: int = 16) -> list[int]:
    return euclidean(3, 8)   # [0, 3, 6] × (steps / 8 scale)


def cinquillo(steps: int = 8) -> list[int]:
    return euclidean(5, steps)


def sixteenths(steps: int = 16) -> list[int]:
    return list(range(steps))


def eighths(steps: int = 16) -> list[int]:
    return list(range(0, steps, steps // 8))


def quarters(steps: int = 16) -> list[int]:
    return list(range(0, steps, steps // 4))


def boom_bap_kick(steps: int = 16) -> list[int]:
    # Kick on beat 1 and the "and-of-2" (offset 10 out of 16).
    return [0, 10]


def breakbeat_kick(steps: int = 16) -> list[int]:
    return [0, 10, 12]   # 1, 'and-of-2', 3


def sparse_pulse(steps: int = 16) -> list[int]:
    return [0, steps // 2]
