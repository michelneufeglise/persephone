"""
signature_engine: loading (EXIF / HEIC / PDF), card segmentation, letter
candidates, scoring (genuine vs different hand), crop-path safety.
Synthetic signatures only (tests/sig_synth.py); every file lives in tmp_path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
pytest.importorskip("skimage")

import signature_engine as se  # noqa: E402
import sig_synth as ss  # noqa: E402

NAME = "M. Neuféglise"
SIG_FONT = ss.first_font(ss.SIGNATURE_FONTS)            # the "writer"
OTHER_FONT = ss.first_font(ss.SIGNATURE_FONTS, skip=2)  # a different hand
BODY_FONT = ss.first_font(ss.BODY_FONTS)
BODY_LINES = [
    "Utrecht, 28 september 2026",
    "",
    "Geachte heer, mevrouw,",
    "Hierbij bevestig ik dat ik eigenaar ben",
    "van Neuféglise Digital Solutions.",
]


def _refs(n=5, font=SIG_FONT, writer_seed=1):
    return [ss.render_signature(NAME, font, 72, seed=s, variation=1.0, writer_seed=writer_seed) for s in range(1, n + 1)]


@pytest.fixture(scope="module")
def card_path(tmp_path_factory):
    d = tmp_path_factory.mktemp("sigcard")
    card = ss.photo_effects(ss.make_card(_refs(), heading="Signature card - Michel Neuféglise (DEMO)",
                                         heading_font=BODY_FONT), seed=3)
    return ss.save(card, d / "card.png")


@pytest.fixture(scope="module")
def card_crops(card_path):
    crops, _w = se.analyze_card(card_path)
    return crops


def _letter(tmp: Path, sig, name="letter.png"):
    page = ss.photo_effects(ss.make_letter(BODY_LINES, sig, "Michel Neuféglise", BODY_FONT), seed=7)
    return ss.save(page, tmp / name)


def _questioned(path):
    ink, la = se.analyze_letter(path)
    return se.make_crop(ink, la.candidates[la.pick].blob, "letter"), la


# ── loading ────────────────────────────────────────────────────────────────

def test_available():
    ok, why = se.available()
    assert ok, why


def test_exif_rotation_is_applied(tmp_path):
    from PIL import Image
    arr = np.full((200, 600), 255, np.uint8)
    arr[80:120, 50:550] = 0  # a wide horizontal bar
    img = Image.fromarray(arr).rotate(90, expand=True)  # stored sideways (600 × 200 → 200 × 600)
    exif = img.getexif()
    exif[0x0112] = 6  # "rotate 90 CW to display"
    p = tmp_path / "rot.jpg"
    img.convert("RGB").save(p, exif=exif.tobytes())
    g = se.load_gray(p)
    assert g.shape[1] > g.shape[0], "EXIF orientation not applied (image still portrait)"


def test_heic_round_trip(tmp_path):
    if not se.register_heif():
        pytest.skip("pillow-heif not installed")
    from PIL import Image
    arr = np.full((240, 640), 255, np.uint8)
    arr[100:140, 60:580] = 0
    p = tmp_path / "photo.heic"
    Image.fromarray(arr).convert("RGB").save(p, format="HEIF")
    g = se.load_gray(p)
    assert g.shape == (240, 640)
    assert g[120, 300] < 100 and g[20, 20] > 200
    out = se.normalize_image_file(p, tmp_path / "page.png")
    assert Image.open(out).format == "PNG"


def test_pdf_page_is_rendered(tmp_path):
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    page = pdf.new_page(width=400, height=300)
    page.draw_rect(fitz.Rect(50, 120, 350, 150), color=(0, 0, 0), fill=(0, 0, 0))
    p = tmp_path / "doc.pdf"
    pdf.save(str(p))
    pdf.close()
    g = se.load_gray(p)
    assert g.ndim == 2 and g.min() < 50 and g.max() > 200


def test_illumination_normalisation_flattens_shadow():
    page = np.full((600, 900), 245, np.uint8)
    page[280:300, 100:800] = 30
    shaded = ss.photo_effects(page, seed=1, shadow=0.55, noise=2)
    ink = se.binarize(shaded)
    # only the bar is ink — the dark (shadowed) right side is not
    assert ink.ink[:, 820:].sum() < 50
    assert ink.ink[285:295, 150:750].mean() > 0.9


# ── segmentation ───────────────────────────────────────────────────────────

def test_card_segmentation_finds_five_and_drops_heading(card_crops):
    assert len(card_crops) == 5
    ys = [c.bbox[1] for c in card_crops]
    assert ys == sorted(ys), "reading order"


def test_card_two_columns(tmp_path):
    card = ss.photo_effects(ss.make_card(_refs(6), cols=2, width=1900), seed=5)
    crops, _ = se.analyze_card(ss.save(card, tmp_path / "card2.png"))
    assert len(crops) == 6


def test_card_probe(card_path, tmp_path):
    assert se.looks_like_signature_card(card_path)["is_card"]
    letter = _letter(tmp_path, ss.render_signature(NAME, SIG_FONT, 72, seed=50))
    assert not se.looks_like_signature_card(letter)["is_card"]


def test_letter_candidates_find_the_signature(tmp_path):
    sig = ss.render_signature(NAME, SIG_FONT, 72, seed=40, variation=1.0)
    p = _letter(tmp_path, sig)
    q, la = _questioned(p)
    assert not la.standalone
    assert 2 <= len(la.candidates) <= 8
    # the signature was pasted below "Met vriendelijke groet," and above the name
    sig_h = sig.shape[0]
    assert q.bbox[3] >= 0.6 * (sig_h - 2 * int(72 * 0.25))
    # the printed name (last line) is below the pick
    lowest = max(c.blob.y1 for c in la.candidates)
    assert q.bbox[1] + q.bbox[3] < lowest


def test_standalone_signature_image(tmp_path):
    sig = ss.render_signature(NAME, SIG_FONT, 72, seed=41)
    page = np.full((sig.shape[0] + 200, sig.shape[1] + 200), 250, np.uint8)
    ss.paste(page, sig, 100, 100)
    ink, la = se.analyze_letter(ss.save(page, tmp_path / "sig.png"))
    assert la.standalone and len(la.candidates) == 1


# ── normalisation ──────────────────────────────────────────────────────────

def test_deskew_rotated_signature():
    sig = ss.render_signature(NAME, SIG_FONT, 72, seed=3, variation=0.0)
    import cv2
    h, w = sig.shape
    pad = cv2.copyMakeBorder(sig, 200, 200, 200, 200, cv2.BORDER_CONSTANT, value=255)
    M = cv2.getRotationMatrix2D((pad.shape[1] / 2, pad.shape[0] / 2), 12, 1.0)
    rot = cv2.warpAffine(pad, M, (pad.shape[1], pad.shape[0]), borderValue=255)
    a0 = se.deskew_angle(sig < 128)
    a1 = se.deskew_angle(rot < 128)
    assert abs((a1 - a0) - (-12)) < 4 or abs((a1 - a0) - 12) < 4
    n = se.normalize(rot < 128)
    assert n is not None and n.img.shape == (se.NORM_H, se.NORM_W)


# ── scoring ────────────────────────────────────────────────────────────────

def test_genuine_scores_higher_than_different_hand(tmp_path, card_crops):
    genuine = ss.render_signature(NAME, SIG_FONT, 70, seed=101, variation=1.1)
    forged = ss.render_signature(NAME, OTHER_FONT, 72, seed=201, variation=1.0)
    qg, _ = _questioned(_letter(tmp_path, genuine, "g.png"))
    qf, _ = _questioned(_letter(tmp_path, forged, "f.png"))
    rg = se.compare(qg, card_crops)
    rf = se.compare(qf, card_crops)
    assert rg["score"] > rf["score"] + 30
    assert rg["band"] == "consistent", rg
    assert rf["band"] == "inconsistent", rf
    assert rg["n_references"] == 5
    assert {b["feature"] for b in rg["breakdown"]} == set(se.FEATURES)
    for b in rg["breakdown"]:
        assert {"questioned_distance", "reference_spread", "z", "verdict"} <= set(b)
    assert 2 <= len(rf["reasons"]) <= 4


def test_several_genuine_and_forged_bands(tmp_path, card_crops):
    fonts = [f for f in (ss.first_font(ss.SIGNATURE_FONTS, skip=k) for k in (1, 2, 3)) if f and f != SIG_FONT]
    genuine_scores = []
    for s in (111, 112, 113):
        q, _ = _questioned(_letter(tmp_path, ss.render_signature(NAME, SIG_FONT, 72, seed=s, variation=1.0), f"g{s}.png"))
        genuine_scores.append(se.compare(q, card_crops)["score"])
    forged_scores = []
    for i, f in enumerate(fonts):
        q, _ = _questioned(_letter(tmp_path, ss.render_signature(NAME, f, 72, seed=300 + i), f"f{i}.png"))
        forged_scores.append(se.compare(q, card_crops)["score"])
    assert min(genuine_scores) >= 70, genuine_scores
    assert max(forged_scores) < 40, forged_scores


def test_score_mapping_and_bands():
    assert se.score_from_z(0.0) >= 90
    assert se.score_from_z(se.LOGISTIC_Z50) == 50
    assert se.score_from_z(6.0) < 10
    assert se.band_of(82) == "consistent"
    assert se.band_of(55) == "inconclusive"
    assert se.band_of(12) == "inconsistent"
    assert se.band_label("consistent", 5) == "consistent with 5 reference signatures"


def test_single_reference_uses_priors_and_warns(tmp_path, card_crops):
    q, _ = _questioned(_letter(tmp_path, ss.render_signature(NAME, SIG_FONT, 72, seed=120), "one.png"))
    r = se.compare(q, card_crops[:1])
    assert r["n_references"] == 1
    assert any("reference" in w.lower() for w in r["warnings"])


def test_identical_references_do_not_explode():
    sig = ss.render_signature(NAME, SIG_FONT, 72, seed=5, variation=0.0)
    page = se.InkPage(sig, sig < 128, 3.0, 30.0, 128.0)
    blob = se.Blob(0, 0, sig.shape[1], sig.shape[0], int((sig < 128).sum()), 1)
    crop = se.make_crop(page, blob)
    refs = [se.make_crop(page, blob) for _ in range(3)]
    r = se.compare(crop, refs)
    assert r["score"] >= 90


# ── crop paths ─────────────────────────────────────────────────────────────

def test_safe_crop_path(tmp_path):
    run = tmp_path / "abc123"
    run.mkdir()
    (run / "questioned.png").write_bytes(b"x")
    (run / "ref_1.png").write_bytes(b"x")
    (tmp_path / "secret.png").write_bytes(b"x")
    assert se.safe_crop_path(tmp_path, "abc123", "questioned.png") == (run / "questioned.png").resolve()
    assert se.safe_crop_path(tmp_path, "abc123", "ref_1.png") is not None
    for run_id, name in [
        ("..", "secret.png"), ("abc123", "../secret.png"), ("abc123", "..%2Fsecret.png"),
        ("abc/../..", "questioned.png"), ("abc123", "questioned.png/../../secret.png"),
        ("abc123", "evil.png"), ("abc123", "ref_1.PNG.exe"), ("", "questioned.png"),
        ("abc123", "ref_2.png"),  # does not exist
    ]:
        assert se.safe_crop_path(tmp_path, run_id, name) is None, (run_id, name)


# ── phone photo of the sheet on a dark table (test-phase regression) ──────

def _on_table(page, seed=3):
    """The sheet photographed on a dark table: perspective tilt, dark border."""
    import cv2
    h, w = page.shape
    rng = np.random.default_rng(seed)
    bw, bh = int(w * 1.16), int(h * 1.16)
    ox, oy = (bw - w) / 2, (bh - h) / 2
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[ox, oy], [ox + w, oy], [ox + w, oy + h], [ox, oy + h]])
    dst += rng.uniform(-0.03, 0.03, dst.shape).astype(np.float32) * np.float32([w, h])
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(page, M, (bw, bh), borderMode=cv2.BORDER_CONSTANT, borderValue=55)


def test_card_on_dark_table_finds_all_signatures(tmp_path):
    """The paper/table boundary used to become a thick 'ink' frame that
    swallowed every signature (1 reference found instead of 5)."""
    card = ss.make_card(_refs(5), heading="Signature card - Michel Neuféglise (DEMO)",
                        heading_font=ss.first_font(ss.BODY_FONTS))
    p = ss.save(ss.photo_effects(_on_table(card), seed=5), tmp_path / "card_table.png")
    crops, _w = se.analyze_card(p)
    assert len(crops) == 5
    g = se.load_gray(p)
    assert se.paper_mask(g) is not None


def test_letter_on_dark_table_picks_signature(tmp_path):
    body = ["Utrecht, 28 september 2026", "", "Geachte heer, mevrouw,", "Hierbij bevestig ik dat ik eigenaar ben"]
    sig = ss.render_signature(NAME, SIG_FONT, 72, seed=77)
    page = ss.make_letter(body, sig, "Michel Neuféglise", ss.first_font(ss.BODY_FONTS))
    p = ss.save(ss.photo_effects(_on_table(page), seed=6), tmp_path / "letter_table.png")
    ink, la = se.analyze_letter(p)
    assert not la.standalone and len(la.candidates) >= 2
    q = se.make_crop(ink, la.candidates[la.pick].blob)
    # the pick is the signature line: not the whole page frame
    assert q.bbox[3] < 0.3 * ink.shape[0]


def test_soft_shadow_is_not_a_table():
    page = np.full((600, 900), 245, np.uint8)
    page[280:300, 100:800] = 30
    assert se.paper_mask(ss.photo_effects(page, seed=1, shadow=0.55, noise=2)) is None


def test_heading_not_counted_on_card_with_two_signatures(tmp_path):
    """Heading + 2 signatures used to yield 3 'references' (the heading
    inflated the spread → a meaningless 99 %) and no quality warning."""
    card = ss.make_card(_refs(2), heading="Signature card - Michel Neuféglise (DEMO)",
                        heading_font=ss.first_font(ss.BODY_FONTS))
    p = ss.save(ss.photo_effects(card, seed=2), tmp_path / "card2.png")
    crops, _w = se.analyze_card(p)
    assert len(crops) == 2
    q = se.make_crop(*_one_sig_page(tmp_path))
    res = se.compare(q, crops)
    assert res["n_references"] == 2
    assert any("reference" in w.lower() for w in res["warnings"])


def _one_sig_page(tmp_path):
    page = np.full((400, 1400), 250, np.uint8)
    ss.paste(page, ss.render_signature(NAME, SIG_FONT, 72, seed=42), 100, 100)
    ink, la = se.analyze_letter(ss.save(page, tmp_path / "one.png"))
    return ink, la.candidates[la.pick].blob
