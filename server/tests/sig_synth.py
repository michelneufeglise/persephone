"""
Synthetic signatures / signature cards / handwritten letters for the
signature-engine tests. Uses the macOS handwriting fonts when present, else
draws per-writer bezier strokes. Nothing is written outside tmp dirs.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = [
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),
]

# Signature "writers" (fonts) and body-text handwriting fonts, in preference order
SIGNATURE_FONTS = ["SnellRoundhand.ttc", "Savoye LET.ttc", "Apple Chancery.ttf", "Zapfino.ttf", "Brush Script.ttf"]
BODY_FONTS = ["Noteworthy.ttc", "Bradley Hand Bold.ttf", "ChalkboardSE.ttc", "MarkerFelt.ttc"]


def find_font(name: str) -> Optional[str]:
    for d in FONT_DIRS:
        p = d / name
        if p.is_file():
            return str(p)
    return None


def first_font(names: list[str], skip: int = 0) -> Optional[str]:
    found = [p for p in (find_font(n) for n in names) if p]
    return found[skip] if len(found) > skip else None


# ── Rendering ──────────────────────────────────────────────────────────────

def _elastic(arr: np.ndarray, rng: np.random.Generator, alpha: float, sigma: float) -> np.ndarray:
    import cv2
    h, w = arr.shape
    dx = cv2.GaussianBlur((rng.random((h, w)) * 2 - 1).astype(np.float32), (0, 0), sigma) * alpha
    dy = cv2.GaussianBlur((rng.random((h, w)) * 2 - 1).astype(np.float32), (0, 0), sigma) * alpha
    x, y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    return cv2.remap(arr, x + dx, y + dy, interpolation=cv2.INTER_LINEAR, borderValue=255)


def _bezier_writer(text: str, writer_seed: int, size: int, rng: np.random.Generator, jitter: float) -> Image.Image:
    """Fallback without fonts: a writer = a fixed random stroke path; genuine
    samples jitter its control points."""
    wr = random.Random(writer_seed)
    n = 6 + len(text) // 2
    pts = []
    x = 0.0
    for i in range(n):
        x += wr.uniform(0.4, 1.1)
        pts.append((x, wr.uniform(-1.0, 1.0)))
    w = int(size * (x + 2) * 0.9)
    h = int(size * 3)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    prev = None
    for (px, py) in pts:
        jx, jy = rng.normal(0, jitter, 2)
        p = (int((px + 0.5 + jx) * size * 0.9), int(h / 2 + (py + jy) * size))
        if prev is not None:
            d.line([prev, p], fill=0, width=max(2, size // 12))
        prev = p
    return img


def render_signature(
    text: str,
    font_path: Optional[str],
    size: int = 72,
    seed: int = 0,
    variation: float = 1.0,
    writer_seed: int = 1,
    stroke_delta: int = 0,
) -> np.ndarray:
    """Render `text` as a signature-like grayscale image (ink 0, paper 255).
    `variation` scales the natural variation (rotation, shear, x-scale,
    elastic jitter); 0 = the exact template."""
    import cv2
    rng = np.random.default_rng(seed)
    if font_path:
        font = ImageFont.truetype(font_path, size)
        l, t, r, b = font.getbbox(text)
        W, H = (r - l) + size * 2, (b - t) + size * 2
        img = Image.new("L", (W, H), 255)
        ImageDraw.Draw(img).text((size - l, size - t), text, font=font, fill=0)
    else:
        img = _bezier_writer(text, writer_seed, size, rng, 0.06 * variation)
    arr = np.asarray(img, dtype=np.uint8).copy()
    if variation > 0:
        h, w = arr.shape
        ang = rng.normal(0, 2.5 * variation)
        shear = rng.normal(0, 0.06 * variation)
        sx = 1.0 + rng.normal(0, 0.05 * variation)
        sy = 1.0 + rng.normal(0, 0.04 * variation)
        M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
        A = np.array([[sx, shear, 0], [0, sy, 0]], dtype=np.float64)
        A3 = np.vstack([A, [0, 0, 1]])
        M3 = np.vstack([M, [0, 0, 1]])
        C = (M3 @ A3)[:2]
        # keep centred
        c = np.array([w / 2, h / 2, 1.0])
        C[:, 2] += np.array([w / 2, h / 2]) - C @ c
        arr = cv2.warpAffine(arr, C, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
        arr = _elastic(arr.astype(np.float32), rng, alpha=2.2 * variation * size / 72, sigma=size / 5.0)
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if stroke_delta:
        k = np.ones((abs(stroke_delta) + 1, abs(stroke_delta) + 1), np.uint8)
        arr = cv2.erode(arr, k) if stroke_delta > 0 else cv2.dilate(arr, k)
    return _crop_ink(arr, pad=int(size * 0.25))


def _crop_ink(arr: np.ndarray, pad: int = 10) -> np.ndarray:
    ys, xs = np.nonzero(arr < 128)
    if len(xs) == 0:
        return arr
    y0, y1 = max(0, ys.min() - pad), min(arr.shape[0], ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(arr.shape[1], xs.max() + pad + 1)
    return arr[y0:y1, x0:x1]


def paste(page: np.ndarray, sig: np.ndarray, x: int, y: int) -> None:
    h, w = sig.shape
    region = page[y:y + h, x:x + w]
    page[y:y + h, x:x + w] = np.minimum(region, sig[: region.shape[0], : region.shape[1]])


def text_image(text: str, font_path: Optional[str], size: int) -> np.ndarray:
    if font_path:
        font = ImageFont.truetype(font_path, size)
    else:
        font = ImageFont.load_default()
    l, t, r, b = font.getbbox(text)
    img = Image.new("L", ((r - l) + 8, (b - t) + 8), 255)
    ImageDraw.Draw(img).text((4 - l, 4 - t), text, font=font, fill=0)
    return np.asarray(img, dtype=np.uint8).copy()


def photo_effects(page: np.ndarray, seed: int = 0, shadow: float = 0.35, noise: float = 6.0) -> np.ndarray:
    """Uneven illumination (a shadow gradient), slight blur and sensor noise."""
    import cv2
    rng = np.random.default_rng(seed)
    h, w = page.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    grad = 1.0 - shadow * (xx / w) * (0.6 + 0.4 * yy / h)
    out = page.astype(np.float32) * grad
    out = cv2.GaussianBlur(out, (3, 3), 0.8)
    out += rng.normal(0, noise, out.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


# ── Documents ──────────────────────────────────────────────────────────────

def make_card(
    sigs: list[np.ndarray],
    heading: Optional[str] = None,
    heading_font: Optional[str] = None,
    cols: int = 1,
    width: int = 1500,
    row_gap: int = 90,
) -> np.ndarray:
    heading_img = text_image(heading, heading_font, 40) if heading else None
    rows = math.ceil(len(sigs) / cols)
    row_h = max(s.shape[0] for s in sigs)
    top = 80 + (heading_img.shape[0] + 70 if heading_img is not None else 0)
    height = top + rows * (row_h + row_gap) + 60
    page = np.full((height, width), 250, dtype=np.uint8)
    if heading_img is not None:
        paste(page, heading_img, 90, 70)
    col_w = (width - 160) // cols
    for i, s in enumerate(sigs):
        r, c = divmod(i, cols)
        s = s[:, : col_w - 20] if s.shape[1] > col_w - 20 else s
        x = 90 + c * col_w + 10
        y = top + r * (row_h + row_gap) + (row_h - s.shape[0]) // 2
        paste(page, s, x, y)
    return page


def make_letter(
    body_lines: list[str],
    signature: np.ndarray,
    name_line: str,
    body_font: Optional[str],
    width: int = 1500,
    size: int = 42,
    closing: str = "Met vriendelijke groet,",
) -> np.ndarray:
    line_imgs = [text_image(t, body_font, size) if t else None for t in body_lines]
    closing_img = text_image(closing, body_font, size)
    name_img = text_image(name_line, body_font, size)
    lh = int(size * 1.7)
    height = 120 + len(body_lines) * lh + closing_img.shape[0] + 80 + signature.shape[0] + 80 + name_img.shape[0] + 160
    page = np.full((height, width), 250, dtype=np.uint8)
    y = 100
    for im in line_imgs:
        if im is not None:
            paste(page, im[:, : width - 200], 100, y)
        y += lh
    y += 30
    paste(page, closing_img, 100, y)
    y += closing_img.shape[0] + 80
    paste(page, signature[:, : width - 200], 100, y)
    y += signature.shape[0] + 70
    paste(page, name_img, 100, y)
    return page


def save(arr: np.ndarray, path: Path) -> Path:
    Image.fromarray(arr).save(str(path))
    return path
