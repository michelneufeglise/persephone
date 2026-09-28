"""
Deterministic, local signature comparison for the Documents agent.

Pure functions — no Ollama, no network, no pretrained signature models.
OpenCV (opencv-python-headless), scikit-image and numpy are imported lazily;
when they are missing every entry point raises SignatureEngineUnavailable so
the agent can fall back gracefully (warning in the tile, answer still produced).

Pipeline
  load_gray()           HEIC/HEIF (pillow-heif), EXIF orientation, PDFs (PyMuPDF),
                        downscaled to a working resolution
  binarize()            illumination normalisation (phone photos of paper with
                        shadows), Otsu, speck removal, border artefacts
  segment_card()        reference card: 3–8 signatures written on plain paper
                        (no printed template) — ink components merged into
                        signature blobs, heading / text lines dropped
  letter_candidates()   handwritten letter: the bottom line-blobs that may be the
                        signature (+ merged neighbours) and a heuristic pick
  normalize()           deskew (principal axis), crop to ink, fixed canvas,
                        skeleton, slant, stroke width
  features()            HOG, LBP, skeleton chamfer, grid ink density, projection
                        profiles (DTW / correlation), aspect, slant, stroke width,
                        Hu moments
  compare()             writer-dependent score with N references: leave-one-out
                        reference spread → per-feature z → weighted → logistic 0–100

The score says how well the questioned signature fits the natural variation
between the reference signatures. It is NOT a forensic determination.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

log = logging.getLogger("signature_engine")

# ── Configuration ──────────────────────────────────────────────────────────
WORK_MAX_SIDE = 2000          # pages are analysed at this resolution (px, long side)
NORM_H, NORM_W = 128, 384     # normalised signature canvas
NORM_MARGIN = 6
CROP_PAD = 0.08               # display crops: padding around the ink bbox (fraction)
MAX_CARD_SIGNATURES = 8
MIN_RELIABLE_REFS = 3

# Bands on the 0–100 score
BAND_CONSISTENT = 70
BAND_INCONCLUSIVE = 40

# Logistic mapping of the combined z-score: score = 100 / (1 + exp(K * (z - Z50))).
# Calibrated on synthetic genuine (same hand, elastic/affine/pen variation, extra
# "letter" domain shift) vs different-hand signatures of the same name — see
# server/tests/test_signature_engine.py and the report in the PR.
LOGISTIC_K = 1.0
LOGISTIC_Z50 = 3.2
Z_CLIP_LO, Z_CLIP_HI = -2.0, 6.0
# Spread floor: a reference set that is (almost) identical must not turn a tiny
# difference into a huge z. sd >= max(sample sd, FLOOR_REL * mean, floor_abs).
FLOOR_REL = 0.30

# feature key → (label, weight, absolute spread floor, prior mean, prior sd)
# priors are used when fewer than 2 references exist (no spread to learn from)
FEATURES: dict[str, tuple[str, float, float, float, float]] = {
    "chamfer":    ("Skeleton shape (chamfer)", 0.22, 0.004, 0.035, 0.012),
    "hog":        ("Stroke directions (HOG)",  0.20, 0.010, 0.30, 0.08),
    "grid":       ("Ink distribution (grid)",  0.14, 0.020, 0.30, 0.08),
    "profile":    ("Projection profiles",      0.14, 0.010, 0.25, 0.07),
    "lbp":        ("Stroke texture (LBP)",     0.07, 0.003, 0.03, 0.012),
    "hu":         ("Global shape (Hu moments)", 0.05, 0.600, 0.80, 0.40),
    "aspect":     ("Aspect ratio",             0.07, 0.040, 0.10, 0.06),
    "slant":      ("Slant",                    0.06, 1.500, 4.0, 2.5),
    "stroke":     ("Stroke width",             0.04, 0.050, 0.15, 0.08),
}

VERDICT_CONSISTENT_Z = 1.0
VERDICT_BORDERLINE_Z = 2.5


class SignatureEngineUnavailable(RuntimeError):
    """OpenCV / scikit-image / numpy missing — the engine cannot run."""


class SignatureError(RuntimeError):
    """The images could not be analysed (no ink, no signature found, …)."""


# ── Lazy dependencies ──────────────────────────────────────────────────────

def _cv():
    try:
        import numpy as np  # noqa: F401
        import cv2  # noqa: F401
    except ImportError as e:  # pragma: no cover - environment dependent
        raise SignatureEngineUnavailable(
            f"The local signature engine needs opencv-python-headless and numpy ({e}). "
            "Install them with `python3 -m pip install opencv-python-headless`."
        ) from e
    import numpy as np
    import cv2
    return np, cv2


def _sk():
    try:
        from skimage.feature import hog, local_binary_pattern  # noqa: F401
        from skimage.morphology import skeletonize  # noqa: F401
    except ImportError as e:  # pragma: no cover - environment dependent
        raise SignatureEngineUnavailable(
            f"The local signature engine needs scikit-image ({e}). "
            "Install it with `python3 -m pip install scikit-image`."
        ) from e
    from skimage.feature import hog, local_binary_pattern
    from skimage.morphology import skeletonize
    return hog, local_binary_pattern, skeletonize


def available() -> tuple[bool, str]:
    """(True, "") when the engine can run, else (False, reason)."""
    try:
        _cv()
        _sk()
        return True, ""
    except SignatureEngineUnavailable as e:
        return False, str(e)


_HEIF_REGISTERED = False


def register_heif() -> bool:
    """Register pillow-heif's HEIC/HEIF opener with PIL (idempotent). False when
    pillow-heif is not installed."""
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return True
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        return False
    try:
        register_heif_opener()
        _HEIF_REGISTERED = True
    except Exception as e:  # pragma: no cover
        log.debug(f"pillow-heif registration failed: {e}")
        return False
    return True


# ── Loading ────────────────────────────────────────────────────────────────

def open_pil(path: str | Path):
    """Open an image with PIL: HEIC/HEIF supported (pillow-heif), EXIF
    orientation applied, alpha composited on white. Returns an RGB/L image."""
    from PIL import Image, ImageOps

    register_heif()
    img = Image.open(str(path))
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def load_gray(path: str | Path, page: int = 0, max_side: int = WORK_MAX_SIDE, dpi: int = 200):
    """Load an image (PNG/JPEG/HEIC/…, or one page of a PDF) as a uint8
    grayscale array, EXIF-rotated and downscaled so the long side is at most
    `max_side` pixels."""
    np, cv2 = _cv()
    p = Path(path)
    if not p.is_file():
        raise SignatureError(f"Image not found: {p.name}")
    if p.suffix.lower() == ".pdf":
        try:
            import fitz  # PyMuPDF
        except ImportError as e:  # pragma: no cover
            raise SignatureError("PDF pages need PyMuPDF") from e
        pdf = fitz.open(str(p))
        try:
            pg = pdf[max(0, min(page, len(pdf) - 1))]
            pix = pg.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False,
                                colorspace=fitz.csGRAY)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            gray = arr.copy()
        finally:
            pdf.close()
    else:
        img = open_pil(p).convert("L")
        gray = np.asarray(img, dtype=np.uint8).copy()
    h, w = gray.shape[:2]
    scale = max_side / float(max(h, w))
    if scale < 1.0:
        gray = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    return gray


def normalize_image_file(src: str | Path, dst: str | Path, max_side: int = 3200) -> str:
    """Write `src` (any PIL-readable image incl. HEIC) as an EXIF-rotated PNG at
    `dst`, long side capped at `max_side`. Used at upload so every page image
    is a real, upright PNG. Returns dst."""
    from PIL import Image

    img = open_pil(src)
    w, h = img.size
    scale = max_side / float(max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    img.save(str(dst), "PNG")
    return str(dst)


# ── Binarisation ───────────────────────────────────────────────────────────

def _odd(n: int) -> int:
    n = int(max(1, n))
    return n if n % 2 == 1 else n + 1


@dataclass
class InkPage:
    gray: Any            # illumination-normalised uint8 (paper ≈ 255)
    ink: Any             # bool ink mask
    stroke: float        # typical stroke width (px)
    text_h: float        # typical ink component height (px)
    threshold: float
    warnings: list[str] = field(default_factory=list)

    @property
    def shape(self) -> tuple[int, int]:
        return tuple(self.ink.shape[:2])  # type: ignore[return-value]


def normalize_illumination(gray):
    """Divide by a background estimate (grey closing on a downscaled copy) so
    shadows / uneven light on phone photos become flat white paper."""
    np, cv2 = _cv()
    h, w = gray.shape[:2]
    f = max(1, int(round(max(h, w) / 800.0)))
    small = cv2.resize(gray, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA) if f > 1 else gray
    k = _odd(max(15, min(small.shape[:2]) // 28))
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 4.0)
    if f > 1:
        bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    norm = gray.astype(np.float32) / np.maximum(bg.astype(np.float32), 1.0) * 255.0
    return np.clip(norm, 0, 255).astype(np.uint8)


def paper_mask(gray):
    """Bool mask of the sheet of paper in a phone photo (the largest bright
    region, convex hull, slightly eroded), or None when no darker background
    (table, desk) surrounds it. The illumination normalisation turns the
    paper/table boundary into a thick dark frame; outside-the-paper ink must
    be ignored or the frame swallows every signature."""
    np, cv2 = _cv()
    h, w = gray.shape[:2]
    f = max(1.0, max(h, w) / 480.0)
    small = cv2.resize(gray, (max(1, int(w / f)), max(1, int(h / f))), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 3)
    _t, bright = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    lo, hi = small[bright == 0], small[bright > 0]
    # A real background is much darker than the paper AND meets it at a sharp
    # edge; a soft shadow gradient across the sheet is neither.
    if lo.size == 0 or hi.size == 0 or float(np.median(lo)) > 0.5 * float(np.median(hi)):
        return None
    mag = np.hypot(cv2.Sobel(small, cv2.CV_32F, 1, 0), cv2.Sobel(small, cv2.CV_32F, 0, 1))
    edge = cv2.morphologyEx(bright, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    if not edge.any() or float(np.median(mag[edge])) < 60.0:
        return None
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
    if n <= 1:
        return None
    comp = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
    frac = cv2.contourArea(hull) / float(small.shape[0] * small.shape[1])
    if not (0.2 <= frac <= 0.96):
        return None
    m = np.zeros_like(comp)
    cv2.drawContours(m, [hull], -1, 1, -1)
    er = max(2, int(round(0.012 * max(small.shape))))
    m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * er + 1, 2 * er + 1)))
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)


def binarize(gray) -> InkPage:
    """Grayscale page → InkPage (normalised gray + clean ink mask)."""
    np, cv2 = _cv()
    norm = normalize_illumination(gray)
    blur = cv2.GaussianBlur(norm, (3, 3), 0)
    t, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    t = float(np.clip(t, 110, 215))
    ink = blur < t
    paper = paper_mask(gray)
    if paper is not None:
        ink &= paper
    warnings: list[str] = []
    if not ink.any():
        return InkPage(norm, ink, 2.0, 20.0, t, ["No ink found in the image"])

    ink_u8 = ink.astype(np.uint8)
    dt = cv2.distanceTransform(ink_u8, cv2.DIST_L2, 3)
    stroke = float(max(1.5, 4.0 * float(dt[ink].mean())))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink_u8, connectivity=8)
    h, w = ink.shape
    min_area = max(6.0, 0.5 * stroke * stroke)
    keep = stats[:, cv2.CC_STAT_AREA] >= min_area
    keep[0] = False
    # Border artefacts: paper edges / table in the photo
    x, y, cw, ch = (stats[:, cv2.CC_STAT_LEFT], stats[:, cv2.CC_STAT_TOP],
                    stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT])
    touches = (x <= 1) | (y <= 1) | (x + cw >= w - 1) | (y + ch >= h - 1)
    big = (cw > 0.25 * w) | (ch > 0.25 * h)
    keep &= ~(touches & big)
    # Huge filled areas (a dark table corner, a photo) are not handwriting
    keep &= ~((stats[:, cv2.CC_STAT_AREA] > 0.08 * h * w))
    ink = keep[lab]
    if not ink.any():
        return InkPage(norm, ink, stroke, 20.0, t, ["No handwriting found in the image"])
    areas = stats[keep, cv2.CC_STAT_AREA]
    heights = stats[keep, cv2.CC_STAT_HEIGHT]
    sel = heights[areas >= 2.0 * stroke * stroke]
    text_h = float(np.median(sel)) if len(sel) else float(np.median(heights))
    text_h = max(text_h, 3.0 * stroke, 8.0)
    return InkPage(norm, ink, stroke, text_h, t, warnings)


# ── Blobs ──────────────────────────────────────────────────────────────────

@dataclass
class Blob:
    x0: int
    y0: int
    x1: int
    y1: int
    area: int = 0
    ncomp: int = 0

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    @property
    def aspect(self) -> float:
        return self.w / float(max(1, self.h))

    def merge(self, o: "Blob") -> "Blob":
        return Blob(min(self.x0, o.x0), min(self.y0, o.y0), max(self.x1, o.x1), max(self.y1, o.y1),
                    self.area + o.area, self.ncomp + o.ncomp)

    def bbox(self) -> tuple[int, int, int, int]:
        return (int(self.x0), int(self.y0), int(self.w), int(self.h))


def _group_components(ink, kx: int, ky: int) -> list[Blob]:
    """Ink components grouped by a rectangular dilation (kx × ky)."""
    np, cv2 = _cv()
    ink_u8 = ink.astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink_u8, connectivity=8)
    if n <= 1:
        return []
    dil = cv2.dilate(ink_u8, cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, kx), max(1, ky))))
    _nd, dlab = cv2.connectedComponents(dil, connectivity=8)
    uniq, first = np.unique(lab.ravel(), return_index=True)
    dflat = dlab.ravel()
    groups: dict[int, Blob] = {}
    for u, idx in zip(uniq.tolist(), first.tolist()):
        if u == 0:
            continue
        g = int(dflat[idx])
        x, y, w, h, a = (int(v) for v in stats[u])
        b = Blob(x, y, x + w, y + h, a, 1)
        groups[g] = groups[g].merge(b) if g in groups else b
    return list(groups.values())


def _merge_fragments(blobs: list[Blob], text_h: float) -> list[Blob]:
    """Merge same-row fragments (initials + surname) and small blobs stacked on
    a bigger one (accents, a flourish just below)."""
    blobs = list(blobs)
    changed = True
    while changed and len(blobs) > 1:
        changed = False
        for i in range(len(blobs)):
            for j in range(i + 1, len(blobs)):
                a, b = blobs[i], blobs[j]
                v_ov = min(a.y1, b.y1) - max(a.y0, b.y0)
                h_gap = max(a.x0, b.x0) - min(a.x1, b.x1)
                same_row = v_ov >= 0.5 * min(a.h, b.h) and h_gap <= 1.5 * text_h
                h_ov = min(a.x1, b.x1) - max(a.x0, b.x0)
                v_gap = max(a.y0, b.y0) - min(a.y1, b.y1)
                small, large = (a, b) if a.area < b.area else (b, a)
                stacked = (
                    h_ov >= 0.5 * min(a.w, b.w) and v_gap <= 0.3 * text_h
                    and small.area < 0.25 * large.area
                )
                if same_row or stacked:
                    blobs[i] = a.merge(b)
                    del blobs[j]
                    changed = True
                    break
            if changed:
                break
    return blobs


def _median(vals: Sequence[float]) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else 0.5 * (s[m - 1] + s[m])


def _reading_order(blobs: list[Blob]) -> list[Blob]:
    """Rows (by vertical overlap) top→bottom, then left→right."""
    rows: list[list[Blob]] = []
    for b in sorted(blobs, key=lambda b: (b.y0 + b.y1) / 2):
        cy = (b.y0 + b.y1) / 2
        for row in rows:
            ry0 = min(r.y0 for r in row)
            ry1 = max(r.y1 for r in row)
            if ry0 <= cy <= ry1:
                row.append(b)
                break
        else:
            rows.append([b])
    out: list[Blob] = []
    for row in rows:
        out.extend(sorted(row, key=lambda b: b.x0))
    return out


def segment_card(page: InkPage, max_signatures: int = MAX_CARD_SIGNATURES) -> list[Blob]:
    """Signature blobs on a reference card: plain paper, 3–8 signatures in rows
    (possibly 2 columns), optionally a hand-written heading at the top."""
    th = page.text_h
    blobs = _group_components(page.ink, int(0.9 * th), int(0.45 * th))
    blobs = _merge_fragments(blobs, th)
    if not blobs:
        return []
    max_area = max(b.area for b in blobs)
    blobs = [b for b in blobs if b.area >= 0.06 * max_area and b.h >= 0.3 * th]
    # Text lines (a heading such as "Signature card – Name") are much wider /
    # flatter than signatures.
    if len(blobs) >= 4:
        med_w = _median([b.w for b in blobs])
        med_ar = _median([b.aspect for b in blobs])
        text_lines = [
            b for b in blobs
            if b.aspect > 2.0 * med_ar or (b.w > 1.7 * med_w and b.aspect > 1.5 * med_ar)
        ]
        if text_lines and len(blobs) - len(text_lines) >= 3:
            blobs = [b for b in blobs if b not in text_lines]
    # A heading line at the top when there are more blobs than expected
    if len(blobs) > 5:
        top = min(blobs, key=lambda b: b.y0)
        others = [b for b in blobs if b is not top]
        med_h = _median([b.h for b in others])
        med_ar = _median([b.aspect for b in others])
        above_all = top.y1 <= min(o.y0 for o in others) + 0.1 * med_h
        if above_all and (top.h < 0.75 * med_h or top.aspect > 1.4 * med_ar):
            blobs = others
    elif len(blobs) >= 3:
        # Few signatures (2–4) under a heading: only a clearly flatter / wider
        # top line is a heading (it used to be counted as a reference).
        top = min(blobs, key=lambda b: b.y0)
        others = [b for b in blobs if b is not top]
        med_h = _median([b.h for b in others])
        med_ar = _median([b.aspect for b in others])
        above_all = top.y1 <= min(o.y0 for o in others) + 0.1 * med_h
        if above_all and top.aspect > 1.8 * med_ar:
            blobs = others
    if len(blobs) > max_signatures:
        med_area = _median([b.area for b in blobs])
        blobs = sorted(blobs, key=lambda b: abs(math.log(max(b.area, 1) / max(med_area, 1))))[:max_signatures]
    return _reading_order(blobs)


def _line_bands(page: InkPage) -> list[tuple[int, int]]:
    """Text lines as (y0, y1) row bands from the horizontal ink projection."""
    np, _cv2 = _cv()
    h, w = page.ink.shape
    prof = page.ink.sum(axis=1)
    thr = max(2.0, 0.002 * w)
    rows = prof >= thr
    bands: list[list[int]] = []
    y = 0
    while y < h:
        if rows[y]:
            y0 = y
            while y < h and rows[y]:
                y += 1
            bands.append([y0, y])
        else:
            y += 1
    if not bands:
        return []
    gap_merge = 0.3 * page.text_h
    merged = [bands[0]]
    for b in bands[1:]:
        if b[0] - merged[-1][1] <= gap_merge:
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    out = []
    for y0, y1 in merged:
        ink_sum = int(prof[y0:y1].sum())
        if (y1 - y0) < 0.3 * page.text_h and ink_sum < 3 * page.text_h * page.stroke:
            continue
        out.append((y0, y1))
    return out


def _band_blob(page: InkPage, y0: int, y1: int) -> Optional[Blob]:
    np, cv2 = _cv()
    band = page.ink[y0:y1]
    cols = np.nonzero(band.any(axis=0))[0]
    if len(cols) == 0:
        return None
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(band.astype(np.uint8), connectivity=8)
    return Blob(int(cols[0]), int(y0), int(cols[-1]) + 1, int(y1), int(band.sum()), max(0, n - 1))


def _word_count(page: InkPage, b: Blob) -> int:
    np, _cv2 = _cv()
    band = page.ink[b.y0:b.y1, b.x0:b.x1]
    cols = band.any(axis=0)
    gap_min = max(3, int(0.55 * page.text_h))
    words, run, in_word = 0, 0, False
    for c in cols:
        if c:
            if not in_word:
                words += 1
                in_word = True
            run = 0
        else:
            run += 1
            if run >= gap_min:
                in_word = False
    return max(1, words)


@dataclass
class Candidate:
    blob: Blob
    lines: tuple[int, ...]            # band indices it covers
    score: float = 0.0
    features: dict[str, float] = field(default_factory=dict)


@dataclass
class LetterAnalysis:
    candidates: list[Candidate]
    pick: int                          # index into candidates (heuristic)
    margin: float                      # score gap to the runner-up
    standalone: bool
    n_lines: int
    warnings: list[str] = field(default_factory=list)


def letter_candidates(page: InkPage, max_lines: int = 4) -> LetterAnalysis:
    """Candidate signature regions at the bottom of a (fully handwritten)
    letter: the last `max_lines` text-line blobs plus merged neighbours, with a
    heuristic pick (tall, cursive-like, surrounded by whitespace; prefers the
    last or second-to-last line — the printed name usually follows the
    signature)."""
    bands = _line_bands(page)
    lines = []
    for i, (y0, y1) in enumerate(bands):
        b = _band_blob(page, y0, y1)
        if b is not None and b.area > 0:
            lines.append((i, b))
    if not lines:
        raise SignatureError("No handwriting found on the page")

    # A standalone signature image: one or two line bands only
    if len(lines) <= 2:
        allb = lines[0][1]
        for _i, b in lines[1:]:
            allb = allb.merge(b)
        cand = Candidate(allb, tuple(i for i, _ in lines), 1.0, {"standalone": 1.0})
        return LetterAnalysis([cand], 0, 1.0, True, len(lines))

    body_h = _median([b.h for _i, b in lines]) or page.text_h
    tail = lines[-max_lines:]
    cands: list[Candidate] = []
    for k, (i, b) in enumerate(tail):
        pos = len(tail) - 1 - k  # 0 = last line
        prev_y1 = lines[lines.index((i, b)) - 1][1].y1 if lines.index((i, b)) > 0 else b.y0
        nxt = lines.index((i, b)) + 1
        next_y0 = lines[nxt][1].y0 if nxt < len(lines) else page.ink.shape[0]
        gap_above = max(0, b.y0 - prev_y1)
        # the last line's "gap below" is the page margin — not informative
        gap_below = max(0, next_y0 - b.y1) if nxt < len(lines) else 0
        words = _word_count(page, b)
        conn = (b.w / float(max(1, b.ncomp))) / float(max(1, b.h))
        feats = {
            "height": b.h / body_h,
            "gap_above": gap_above / body_h,
            "gap_below": gap_below / body_h,
            "connectivity": conn,
            "words": float(words),
            "position": float(pos),
        }
        s = (
            1.3 * math.log(max(0.2, b.h / body_h))
            + 0.45 * min(gap_above / body_h, 3.0)
            + 0.30 * min(gap_below / body_h, 3.0)
            + 0.45 * math.log(max(0.05, conn))
            - 0.30 * max(0, words - 2)
            + {0: 0.10, 1: 0.25, 2: 0.0}.get(pos, -0.2)
        )
        # A short single word as the last line under a signature-like line is the name
        cands.append(Candidate(b, (i,), s, feats))
    # merged neighbours (a signature split into two bands by a flourish)
    for k in range(len(tail) - 1):
        (i, a), (j, b) = tail[k], tail[k + 1]
        if b.y0 - a.y1 <= 0.6 * body_h:
            m = a.merge(b)
            feats = {"height": m.h / body_h, "merged": 1.0}
            conn = (m.w / float(max(1, m.ncomp))) / float(max(1, m.h))
            s = 1.3 * math.log(max(0.2, m.h / body_h)) + 0.45 * math.log(max(0.05, conn)) - 0.6
            cands.append(Candidate(m, (i, j), s, feats))
    order = sorted(range(len(cands)), key=lambda c: cands[c].score, reverse=True)
    pick = order[0]
    margin = cands[order[0]].score - cands[order[1]].score if len(order) > 1 else 1.0
    warnings = []
    if margin < 0.25:
        warnings.append("The signature's position on the page was ambiguous")
    # present candidates top→bottom (stable numbering for the vision model)
    ordered = sorted(range(len(cands)), key=lambda c: (cands[c].blob.y0, len(cands[c].lines)))
    new_cands = [cands[c] for c in ordered]
    return LetterAnalysis(new_cands, ordered.index(pick), float(margin), False, len(lines), warnings)


# ── Crops ──────────────────────────────────────────────────────────────────

@dataclass
class SigCrop:
    bbox: tuple[int, int, int, int]     # x, y, w, h in working-resolution page px
    gray: Any                            # uint8 display crop (normalised page, padded)
    mask: Any                            # bool ink mask, tight to bbox
    source: str = ""
    index: int = 0
    info: dict[str, Any] = field(default_factory=dict)
    _feats: Optional[dict] = None

    def features(self) -> dict:
        if self._feats is None:
            norm = normalize(self.mask)
            if norm is None:
                raise SignatureError("Signature crop has too little ink")
            self._feats = extract_features(norm)
        return self._feats


def make_crop(page: InkPage, blob: Blob, source: str = "", index: int = 0) -> SigCrop:
    np, cv2 = _cv()
    h, w = page.ink.shape
    pad = int(round(CROP_PAD * max(blob.w, blob.h))) + 4
    x0, y0 = max(0, blob.x0 - pad), max(0, blob.y0 - pad)
    x1, y1 = min(w, blob.x1 + pad), min(h, blob.y1 + pad)
    gray = page.gray[y0:y1, x0:x1].copy()
    mask = page.ink[blob.y0:blob.y1, blob.x0:blob.x1].copy()
    info = {
        "ink_height_px": int(blob.h),
        "ink_width_px": int(blob.w),
        "blur_var": _blur_var(page.gray[blob.y0:blob.y1, blob.x0:blob.x1]),
    }
    return SigCrop(blob.bbox(), gray, mask, source, index, info)


def _blur_var(gray) -> float:
    np, cv2 = _cv()
    if gray.size == 0:
        return 0.0
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# ── Normalisation & features ───────────────────────────────────────────────

def _tight(mask):
    np, _ = _cv()
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return mask[:0, :0]
    return mask[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]


def deskew_angle(mask) -> float:
    """Principal-axis angle (degrees) of the ink; 0 when unreliable (> 30°)."""
    np, _ = _cv()
    ys, xs = np.nonzero(mask)
    if len(xs) < 10:
        return 0.0
    cx = xs - xs.mean()
    cy = ys - ys.mean()
    mu20, mu02, mu11 = float((cx * cx).mean()), float((cy * cy).mean()), float((cx * cy).mean())
    ang = 0.5 * math.degrees(math.atan2(2 * mu11, mu20 - mu02))
    if mu20 < 1.2 * mu02 or abs(ang) > 30:
        return 0.0
    return ang


def _rotate(mask, deg: float):
    np, cv2 = _cv()
    if abs(deg) < 0.2:
        return mask
    h, w = mask.shape
    pad = int(0.3 * max(h, w)) + 2
    m = cv2.copyMakeBorder(mask.astype(np.float32), pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    ch, cw = m.shape
    M = cv2.getRotationMatrix2D((cw / 2.0, ch / 2.0), deg, 1.0)
    r = cv2.warpAffine(m, M, (cw, ch), flags=cv2.INTER_LINEAR, borderValue=0)
    return _tight(r > 0.5)


def _shear(img, s: float):
    """Horizontal shear x' = x + s * (y - h/2) of a float image (same size)."""
    np, cv2 = _cv()
    h, w = img.shape
    M = np.float32([[1, s, -s * h / 2.0], [0, 1, 0]])
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)


def estimate_slant(mask) -> float:
    """Dominant slant (degrees, + = leaning right) — the shear that makes the
    vertical projection most peaked (strokes line up)."""
    np, cv2 = _cv()
    t = _tight(mask).astype(np.float32)
    if t.size == 0:
        return 0.0
    h, w = t.shape
    sc = 64.0 / max(1, h)
    small = cv2.resize(t, (max(8, int(w * sc)), 64), interpolation=cv2.INTER_AREA)
    pad = 40
    small = cv2.copyMakeBorder(small, 0, 0, pad, pad, cv2.BORDER_CONSTANT, value=0)
    best, best_s = -1.0, 0.0
    for deg in range(-45, 46, 3):
        s = math.tan(math.radians(deg))
        prof = _shear(small, s).sum(axis=0)
        v = float((prof ** 2).sum())
        if v > best:
            best, best_s = v, deg
    # shear +s moves the bottom right → corrects a LEFT lean; report the lean
    return float(-best_s)


@dataclass
class NormSig:
    img: Any          # float32 NORM_H × NORM_W stroke image (0/1, width-normalised)
    skel: Any         # bool skeleton on the canvas
    dist: Any         # float32 distance transform to the skeleton (truncated)
    aspect: float
    slant: float
    stroke: float     # stroke width relative to ink height
    angle: float


def normalize(mask) -> Optional[NormSig]:
    """Deskew, crop to ink, stretch to the fixed canvas, skeletonise."""
    np, cv2 = _cv()
    _hog, _lbp, skeletonize = _sk()
    t = _tight(mask)
    if t.size == 0 or int(t.sum()) < 20:
        return None
    ang = deskew_angle(t)
    t = _rotate(t, ang)
    if t.size == 0:
        return None
    th, tw = t.shape
    aspect = tw / float(max(1, th))
    slant = estimate_slant(t)
    # stroke width relative to the ink height (pen independent-ish)
    dt = cv2.distanceTransform(t.astype(np.uint8), cv2.DIST_L2, 3)
    sk_t = skeletonize(t)
    sw = 2.0 * float(np.median(dt[sk_t])) if sk_t.any() else 1.0
    stroke = sw / float(max(1, th))
    inner_w, inner_h = NORM_W - 2 * NORM_MARGIN, NORM_H - 2 * NORM_MARGIN
    rs = cv2.resize(t.astype(np.float32), (inner_w, inner_h), interpolation=cv2.INTER_AREA)
    b = rs > 0.25
    canvas = np.zeros((NORM_H, NORM_W), dtype=bool)
    canvas[NORM_MARGIN:NORM_MARGIN + inner_h, NORM_MARGIN:NORM_MARGIN + inner_w] = b
    skel = skeletonize(canvas)
    img = cv2.dilate(skel.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))).astype(np.float32)
    dist = cv2.distanceTransform((~skel).astype(np.uint8), cv2.DIST_L2, 3)
    dist = np.minimum(dist, 30.0).astype(np.float32)
    return NormSig(img, skel, dist, aspect, slant, stroke, ang)


def extract_features(n: NormSig) -> dict[str, Any]:
    np, cv2 = _cv()
    hog, local_binary_pattern, _sk_ = _sk()
    smooth = cv2.GaussianBlur(n.img, (0, 0), 1.5)
    hog_v = hog(smooth, orientations=9, pixels_per_cell=(16, 16), cells_per_block=(2, 2),
                feature_vector=True).astype(np.float32)
    nrm = float(np.linalg.norm(hog_v)) or 1.0
    hog_v = hog_v / nrm
    # LBP texture over the stroke neighbourhood
    lbp_img = (np.clip(smooth / max(float(smooth.max()), 1e-6), 0, 1) * 255).astype(np.uint8)
    lbp = local_binary_pattern(lbp_img, P=8, R=1.5, method="uniform")
    region = cv2.dilate(n.img, np.ones((5, 5), np.uint8)) > 0
    hist, _ = np.histogram(lbp[region], bins=10, range=(0, 10))
    hist = hist.astype(np.float32)
    hist /= float(hist.sum()) or 1.0
    # grid ink density 4 × 12
    g = n.img.reshape(4, NORM_H // 4, 12, NORM_W // 12).sum(axis=(1, 3)).astype(np.float32)
    g /= float(g.sum()) or 1.0
    # projection profiles
    pv = n.img.sum(axis=0).reshape(96, NORM_W // 96).sum(axis=1).astype(np.float32)
    pv /= float(pv.mean()) or 1.0
    ph = n.img.sum(axis=1).reshape(32, NORM_H // 32).sum(axis=1).astype(np.float32)
    # Hu moments (log scale)
    hu = cv2.HuMoments(cv2.moments(n.img)).ravel()
    hu = -np.sign(hu) * np.log10(np.abs(hu) + 1e-30)
    hu = np.clip(hu[:4], -30, 30).astype(np.float32)
    ys, xs = np.nonzero(n.skel)
    return {
        "hog": hog_v, "lbp": hist, "grid": g.ravel(), "pv": pv, "ph": ph, "hu": hu,
        "skel_pts": (ys.astype(np.int32), xs.astype(np.int32)), "dist": n.dist,
        "aspect": n.aspect, "slant": n.slant, "stroke": n.stroke,
    }


# ── Distances ──────────────────────────────────────────────────────────────

def _dtw(a, b, window: int = 8) -> float:
    n, m = len(a), len(b)
    inf = float("inf")
    prev = [inf] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur = [inf] * (m + 1)
        lo, hi = max(1, i - window), min(m, i + window)
        ai = float(a[i - 1])
        for j in range(lo, hi + 1):
            c = abs(ai - float(b[j - 1]))
            best = prev[j]
            if cur[j - 1] < best:
                best = cur[j - 1]
            if prev[j - 1] < best:
                best = prev[j - 1]
            cur[j] = c + best
        prev = cur
    return prev[m] / float(n + m)


def _chamfer(fa: dict, fb: dict) -> float:
    np, _ = _cv()
    ya, xa = fa["skel_pts"]
    yb, xb = fb["skel_pts"]
    if len(ya) == 0 or len(yb) == 0:
        return 1.0
    da, db = fa["dist"], fb["dist"]
    H, W = da.shape
    best = float("inf")
    for dy in (-6, -3, 0, 3, 6):
        for dx in (-12, -8, -4, 0, 4, 8, 12):
            ab = db[np.clip(ya + dy, 0, H - 1), np.clip(xa + dx, 0, W - 1)].mean()
            ba = da[np.clip(yb - dy, 0, H - 1), np.clip(xb - dx, 0, W - 1)].mean()
            v = 0.5 * float(ab + ba)
            if v < best:
                best = v
    return best / float(NORM_H)


def feature_distances(fa: dict, fb: dict) -> dict[str, float]:
    """Per-feature distances between two signatures' features."""
    np, _ = _cv()
    out: dict[str, float] = {}
    out["hog"] = float(1.0 - float(np.dot(fa["hog"], fb["hog"])))
    a, b = fa["lbp"], fb["lbp"]
    out["lbp"] = float(0.5 * np.sum((a - b) ** 2 / (a + b + 1e-9)))
    out["grid"] = float(np.abs(fa["grid"] - fb["grid"]).sum())
    dv = _dtw(fa["pv"], fb["pv"], window=8)
    ph_a, ph_b = fa["ph"], fb["ph"]
    if float(ph_a.std()) > 1e-9 and float(ph_b.std()) > 1e-9:
        corr = float(np.corrcoef(ph_a, ph_b)[0, 1])
    else:
        corr = 0.0
    out["profile"] = float(dv + 0.5 * (1.0 - corr))
    out["chamfer"] = _chamfer(fa, fb)
    out["hu"] = float(np.abs(fa["hu"] - fb["hu"]).sum())
    out["aspect"] = float(abs(math.log(max(fa["aspect"], 1e-3) / max(fb["aspect"], 1e-3))))
    out["slant"] = float(abs(fa["slant"] - fb["slant"]))
    out["stroke"] = float(abs(math.log(max(fa["stroke"], 1e-3) / max(fb["stroke"], 1e-3))))
    return out


# ── Scoring ────────────────────────────────────────────────────────────────

def score_from_z(z: float) -> int:
    """Combined z → confidence 0–100 (logistic)."""
    try:
        s = 100.0 / (1.0 + math.exp(LOGISTIC_K * (z - LOGISTIC_Z50)))
    except OverflowError:
        s = 0.0
    return int(round(max(0.0, min(100.0, s))))


def band_of(score: float) -> str:
    if score >= BAND_CONSISTENT:
        return "consistent"
    if score >= BAND_INCONCLUSIVE:
        return "inconclusive"
    return "inconsistent"


def band_label(band: str, n_refs: int) -> str:
    refs = f"{n_refs} reference signature{'s' if n_refs != 1 else ''}"
    return {
        "consistent": f"consistent with {refs}",
        "inconclusive": f"inconclusive against {refs}",
        "inconsistent": f"inconsistent with {refs}",
    }.get(band, band)


def _trimmed(vals: list[float], k: int) -> float:
    s = sorted(vals)
    k = max(1, min(k, len(s)))
    return float(sum(s[:k]) / k)


def compare_features(q: dict, refs: list[dict]) -> dict[str, Any]:
    """Writer-dependent comparison of questioned features `q` against N
    reference feature dicts. Returns score / band / per-feature breakdown."""
    n = len(refs)
    if n == 0:
        raise SignatureError("No reference signatures")
    keys = list(FEATURES.keys())
    k_best = max(1, int(math.ceil(0.75 * max(1, n - 1))))
    # pairwise reference distances
    pair: dict[tuple[int, int], dict[str, float]] = {}
    for i in range(n):
        for j in range(i + 1, n):
            pair[(i, j)] = feature_distances(refs[i], refs[j])
    q_d = [feature_distances(q, r) for r in refs]
    breakdown = []
    zsum, wsum = 0.0, 0.0
    for f in keys:
        label, weight, floor_abs, prior_mu, prior_sd = FEATURES[f]
        if n >= 2:
            loo = []
            for i in range(n):
                ds = [pair[(min(i, j), max(i, j))][f] for j in range(n) if j != i]
                loo.append(_trimmed(ds, k_best))
            mu = sum(loo) / len(loo)
            if len(loo) > 1:
                var = sum((x - mu) ** 2 for x in loo) / (len(loo) - 1)
                sd = math.sqrt(var)
            else:
                sd = 0.0
            sd = max(sd, FLOOR_REL * mu, floor_abs)
        else:
            mu, sd = prior_mu, prior_sd
        dq = _trimmed([d[f] for d in q_d], k_best)
        z = (dq - mu) / sd if sd > 0 else 0.0
        zc = max(Z_CLIP_LO, min(Z_CLIP_HI, z))
        zsum += weight * zc
        wsum += weight
        verdict = "consistent" if z < VERDICT_CONSISTENT_Z else "borderline" if z < VERDICT_BORDERLINE_Z else "different"
        breakdown.append({
            "feature": f,
            "label": label,
            "questioned_distance": round(dq, 4),
            "reference_spread": round(mu, 4),
            "reference_sd": round(sd, 4),
            "z": round(z, 2),
            "weight": weight,
            "verdict": verdict,
        })
    combined = zsum / wsum if wsum else 0.0
    score = score_from_z(combined)
    band = band_of(score)
    return {
        "score": score,
        "band": band,
        "band_label": band_label(band, n),
        "combined_z": round(combined, 3),
        "n_references": n,
        "breakdown": breakdown,
        "reasons": key_reasons(breakdown, band),
    }


_REASON_GOOD = {
    "chamfer": "the stroke skeleton follows the same path as the references",
    "hog": "stroke directions match the references",
    "grid": "ink is distributed over the signature the same way",
    "profile": "letter spacing and height profile line up with the references",
    "lbp": "stroke texture is similar",
    "hu": "the overall shape matches",
    "aspect": "the width-to-height proportion matches",
    "slant": "the slant matches",
    "stroke": "the stroke width is comparable",
}
_REASON_BAD = {
    "chamfer": "the stroke skeleton takes a different path than in any reference",
    "hog": "stroke directions differ from the references",
    "grid": "ink is distributed differently over the signature",
    "profile": "letter spacing / height profile differ from the references",
    "lbp": "stroke texture differs",
    "hu": "the overall shape differs",
    "aspect": "the width-to-height proportion differs",
    "slant": "the slant differs",
    "stroke": "the stroke width differs",
}


def key_reasons(breakdown: list[dict], band: str, k: int = 3) -> list[str]:
    """2–4 plain-language reasons, driven by the per-feature z-scores."""
    by_z = sorted(breakdown, key=lambda b: b["z"])
    out: list[str] = []
    if band == "consistent":
        for b in by_z[:k]:
            out.append(f"{_REASON_GOOD[b['feature']].capitalize()} (within the natural variation of the references)")
        worst = max((b for b in breakdown if b.get("weight", 0) >= 0.1), key=lambda b: b["z"], default=by_z[-1])
        if worst["z"] >= VERDICT_BORDERLINE_Z:
            out.append(f"Only difference: {_REASON_BAD[worst['feature']]} ({worst['z']:.1f}σ)")
    elif band == "inconsistent":
        for b in list(reversed(by_z))[:k]:
            ratio = b["questioned_distance"] / b["reference_spread"] if b["reference_spread"] else 0
            extra = f" — {ratio:.1f}× the normal variation between the references" if ratio >= 1.5 else ""
            out.append(f"{_REASON_BAD[b['feature']].capitalize()}{extra}")
    else:
        good = [b for b in by_z if b["z"] < VERDICT_CONSISTENT_Z][:2]
        bad = [b for b in reversed(by_z) if b["z"] >= VERDICT_BORDERLINE_Z][:2]
        for b in good:
            out.append(f"{_REASON_GOOD[b['feature']].capitalize()}")
        for b in bad:
            out.append(f"But {_REASON_BAD[b['feature']]} ({b['z']:.1f}σ)")
        if not out:
            out.append("Features are partly similar, partly outside the reference variation")
    return out[:4]


def quality_warnings(questioned: SigCrop, refs: list[SigCrop]) -> list[str]:
    w: list[str] = []
    n = len(refs)
    if n < MIN_RELIABLE_REFS:
        w.append(
            f"Only {n} reference signature{'s' if n != 1 else ''} — the score is less reliable "
            f"(5 are recommended)"
        )
    if questioned.info.get("ink_height_px", 99) < 35:
        w.append("Low resolution: the questioned signature is very small in the image")
    small_refs = sum(1 for r in refs if r.info.get("ink_height_px", 99) < 35)
    if small_refs:
        w.append(f"Low resolution: {small_refs} reference signature(s) are very small in the image")
    if questioned.info.get("blur_var", 999.0) < 40.0:
        w.append("The questioned signature looks blurry")
    return w


def compare(questioned: SigCrop, refs: list[SigCrop]) -> dict[str, Any]:
    """Score `questioned` against the reference crops."""
    if not refs:
        raise SignatureError("No reference signatures found")
    qf = questioned.features()
    rfs = []
    for r in refs:
        try:
            rfs.append(r.features())
        except SignatureError:
            continue
    if not rfs:
        raise SignatureError("The reference signatures have too little ink")
    res = compare_features(qf, rfs)
    res["warnings"] = quality_warnings(questioned, refs)
    if len(rfs) < 2:
        res["warnings"].append("Spread learned from built-in defaults (a single reference has no variation)")
    return res


# ── High-level helpers (files) ─────────────────────────────────────────────

def analyze_card(path: str | Path, page: int = 0, source: str = "") -> tuple[list[SigCrop], list[str]]:
    """Reference card image/PDF → signature crops (reading order) + warnings."""
    gray = load_gray(path, page=page)
    ink = binarize(gray)
    blobs = segment_card(ink)
    crops = [make_crop(ink, b, source or Path(path).name, i) for i, b in enumerate(blobs)]
    return crops, list(ink.warnings)


def analyze_letter(path: str | Path, page: int = 0) -> tuple[InkPage, LetterAnalysis]:
    gray = load_gray(path, page=page)
    ink = binarize(gray)
    return ink, letter_candidates(ink)


def looks_like_signature_card(path: str | Path) -> dict[str, Any]:
    """Cheap probe: an image with 3–8 similar signature-like blobs and little
    else looks like a signature reference card."""
    try:
        gray = load_gray(path, max_side=1200)
        ink = binarize(gray)
        blobs = segment_card(ink)
        lines = _line_bands(ink)
    except (SignatureError, SignatureEngineUnavailable) as e:
        return {"is_card": False, "count": 0, "reason": str(e)}
    n = len(blobs)
    if n < 3:
        return {"is_card": False, "count": n, "reason": "fewer than 3 signature-like blobs"}
    ws = [b.w for b in blobs]
    mean_w = sum(ws) / n
    cv = math.sqrt(sum((x - mean_w) ** 2 for x in ws) / n) / (mean_w or 1)
    ars = [b.aspect for b in blobs]
    sig_like = sum(1 for a in ars if 1.2 <= a <= 9.0)
    # rows of a card are separated by generous whitespace; lines of a letter are not
    by_y = sorted(blobs, key=lambda b: b.y0)
    gaps = [max(0, b.y0 - a.y1) for a, b in zip(by_y, by_y[1:]) if b.y0 >= a.y1 - 0.2 * a.h]
    med_h = _median([b.h for b in blobs]) or 1.0
    gap_ratio = (_median(gaps) / med_h) if gaps else 0.0
    med_ar = _median(ars)
    is_card = (
        sig_like >= max(3, int(0.7 * n)) and len(lines) <= n + 3 and med_ar <= 7.5
        and ((cv < 0.2 and gap_ratio >= 0.9) or (cv < 0.12 and gap_ratio >= 0.5))
    )
    return {"is_card": bool(is_card), "count": n, "lines": len(lines), "width_cv": round(cv, 2),
            "gap_ratio": round(gap_ratio, 2), "median_aspect": round(med_ar, 2)}


def save_png(arr, path: str | Path) -> str:
    np, cv2 = _cv()
    a = arr
    if a.dtype == bool:
        a = np.where(a, 0, 255).astype(np.uint8)
    cv2.imwrite(str(path), a)
    return str(path)


# ── Crop storage / serving ─────────────────────────────────────────────────

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CROP_NAME_RE = re.compile(r"^(?:questioned|compare|candidates|ref_\d{1,2}|cand_\d{1,2})\.png$")


def safe_crop_path(base_dir: str | Path, run_id: str, name: str) -> Optional[Path]:
    """Path of a stored crop PNG, or None when the ids are invalid (path
    traversal, unknown names) or the file does not exist."""
    if not isinstance(run_id, str) or not isinstance(name, str):
        return None
    if not RUN_ID_RE.match(run_id) or not CROP_NAME_RE.match(name):
        return None
    base = Path(base_dir).resolve()
    p = (base / run_id / name).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        return None
    return p if p.is_file() else None
