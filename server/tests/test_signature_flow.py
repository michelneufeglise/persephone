"""
Signature check as an extra step of the Documents agent: routing (rules path,
Laya mocked), roles, the signature-check tile, the answer, the handwriting
transcription, knowledge-graph relations, model-role priority and the crop
route. Synthetic images in tmp_path; no Ollama.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

pytest.importorskip("cv2")
pytest.importorskip("skimage")

import doc_agent as _agent  # noqa: E402
import doc_signature as _sig  # noqa: E402
import signature_engine as se  # noqa: E402
import sig_synth as ss  # noqa: E402

DEMO_Q = "Read this handwritten letter, check the company in the registry, and verify the signature against the reference card"
NAME = "M. Neuféglise"
SIG_FONT = ss.first_font(ss.SIGNATURE_FONTS)
OTHER_FONT = ss.first_font(ss.SIGNATURE_FONTS, skip=2)
BODY_FONT = ss.first_font(ss.BODY_FONTS)

REGISTRY_TEXT = """DEMO · Company Registry Extract
Fictitious document for a Persephone product demo. NOT issued by the Kamer van Koophandel.
Registry number (demo)
00 00 00 00 (DEMO)
Trade name
Neuféglise Digital Solutions
Legal form
Eenmanszaak (sole proprietorship)
Date of registration
1 March 2021
Address (fictitious)
Demostraat 1, 3500 AA Utrecht (DEMO)
Owner / authorised person
Name
Michel Neuféglise
Role
Owner (eigenaar)
"""
LETTER_TEXT = (
    "Utrecht, 28 september 2026\nGeachte heer, mevrouw,\nHierbij bevestig ik, Michel Neuféglise, dat ik eigenaar ben "
    "van Neuféglise Digital Solutions, Demostraat 1, 3500 AA Utrecht.\nMet vriendelijke groet,\n[signature]\nMichel Neuféglise"
)


def _doc(doc_id, filename, mime, text="", page_images=None):
    return SimpleNamespace(
        id=doc_id, filename=filename, mime=mime, size=1000, uploaded_at=0.0,
        pages=1, text=text, page_texts=[text], page_images=page_images or [], meta={},
    )


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    d = tmp_path_factory.mktemp("sigflow")
    refs = [ss.render_signature(NAME, SIG_FONT, 72, seed=s) for s in range(1, 6)]
    card = ss.save(ss.photo_effects(ss.make_card(refs, heading="Signature card - Michel Neuféglise (DEMO)",
                                                 heading_font=BODY_FONT), seed=2), d / "card.png")
    body = ["Utrecht, 28 september 2026", "", "Geachte heer, mevrouw,",
            "Hierbij bevestig ik dat ik eigenaar ben", "van Neuféglise Digital Solutions."]
    genuine = ss.save(ss.photo_effects(ss.make_letter(
        body, ss.render_signature(NAME, SIG_FONT, 72, seed=77), "Michel Neuféglise", BODY_FONT), seed=4), d / "letter.png")
    forged = ss.save(ss.photo_effects(ss.make_letter(
        body, ss.render_signature(NAME, OTHER_FONT, 72, seed=78), "Michel Neuféglise", BODY_FONT), seed=4), d / "forged.png")
    return {"card": str(card), "letter": str(genuine), "forged": str(forged), "dir": d}


class Rec:
    def __init__(self):
        self.vision_prompts: list[str] = []
        self.llm_prompts: list[str] = []
        self.kg: list[dict] = []
        self.transcribed: list[str] = []


def _hooks(docs, rec: Rec, tmp: Path, *, pick_reply=None, answer="**Letter** …\n\n**Company check** …",
           candidates=("sig-vision:7b",), laya_intent=None, kg_ingest=None):
    by_id = {d.id: d for d in docs}

    async def resolve_model(cat):
        return {"handwriting": "hw-vision:7b", "ocr": "ocr-model"}.get(cat, "text-model")

    async def resolve_text_model(doc, cat):
        return "text-model"

    async def vision_candidates():
        return list(candidates)

    async def vision_call(model, prompt, refs, subjects, **kw):
        rec.vision_prompts.append(prompt)
        if "numbered regions" in prompt:
            return pick_reply(subjects) if callable(pick_reply) else (pick_reply or "no idea")
        if "handwritten (written by hand" in prompt.lower() or "HANDWRITTEN" in prompt:
            return "handwritten"
        return "Similar initial M and capital N; comparable slant. Not a forensic determination."

    async def stream_llm(model, prompt, think=False, **kw):
        rec.llm_prompts.append(prompt)
        yield {"content": answer}
        yield {"done": True}

    async def run_ocr(doc, model):
        return "OCR TEXT"

    async def transcribe(doc, model):
        rec.transcribed.append(doc.id)
        doc.text = LETTER_TEXT
        doc.meta.update({"handwritten": True, "transcribed_by": model, "last_ocr_at": 1})
        return LETTER_TEXT

    async def model_info(name):
        return {"name": name}

    async def _kg(**kw):
        rec.kg.append(kw)
        return {"entities": 0, "relations": 0, "mentions": 0}

    return _agent.AgentHooks(
        get_doc=lambda i: by_id.get(i),
        laya_intent=lambda m, f: laya_intent,
        laya_role=lambda m, f: None,
        laya_doc_kind=lambda t: None,
        laya_info=lambda: {"available": False},
        page_image_paths=lambda doc, pages, dpi: [],
        resolve_model=resolve_model,
        resolve_text_model=resolve_text_model,
        pick_vision_model=AsyncMock(return_value=candidates[0] if candidates else None),
        vision_candidates=vision_candidates,
        model_info=model_info,
        run_ocr=run_ocr,
        stream_llm=stream_llm,
        vision_call=vision_call,
        mark_vision_failed=lambda m, e: None,
        kg_ingest=kg_ingest or _kg,
        signature_dir=lambda run_id: tmp / run_id,
        transcribe_handwriting=transcribe,
        now_ms=lambda: 1_000,
    )


def _run(req, hooks):
    async def go():
        return [e async for e in _agent.run_agent(req, hooks)]
    return asyncio.run(go())


def _tile(events, tid):
    tiles = [e["tile"] for e in events if "tile" in e and e["tile"]["id"] == tid]
    return tiles[-1] if tiles else None


def _decisions(events):
    return {d["id"]: d for d in _tile(events, "laya")["decisions"]}


def _demo_docs(images, letter="letter"):
    letter_doc = _doc("L", "DEMO_handwritten_letter_Michel_Neufeglise.png", "image/png", "", [images[letter]])
    reg = _doc("R", "DEMO_company_registry_extract_Michel_Neufeglise.pdf", "application/pdf", REGISTRY_TEXT)
    card = _doc("C", "DEMO_signature_card_Michel_Neufeglise.png", "image/png", "", [images["card"]])
    return letter_doc, reg, card


def _req(msg=DEMO_Q, ids=("L", "R", "C")):
    return {"message": msg, "attachments": [{"doc_id": i} for i in ids], "run_id": "run0001abcd", "conversation_id": "dconv-x"}


# ── pure routing ───────────────────────────────────────────────────────────

def test_plan_strips_signature_clause():
    plan = _sig.plan_signature_step(DEMO_Q)
    assert plan["combined"] and "signature" in plan["hits"]
    assert "registry" in plan["remainder"] and "signature" not in plan["remainder"]
    nl = "Lees deze handgeschreven brief, controleer het bedrijf in het handelsregister en vergelijk de handtekening met de handtekeningkaart"
    p2 = _sig.plan_signature_step(nl)
    assert p2["combined"] and "handtekening" in p2["hits"] and "handelsregister" in p2["remainder"]
    pure = _sig.plan_signature_step("compare the signature on the contract with the reference card")
    assert pure["hits"] and not pure["combined"]
    assert not _sig.signature_hits("who signed this letter?")


def test_rules_route_demo_question_to_cross_reference():
    files = [{"name": "DEMO_handwritten_letter_Michel_Neufeglise.png"},
             {"name": "DEMO_company_registry_extract_Michel_Neufeglise.pdf"},
             {"name": "DEMO_signature_card_Michel_Neufeglise.png"}]
    remainder = _sig.plan_signature_step(DEMO_Q)["remainder"]
    assert _agent.rules_intent(remainder, files)[0] == "cross_reference"
    # the full sentence alone would be a pure signature request
    assert _agent.rules_intent(DEMO_Q, files)[0] == "verify_signature"


def test_card_name_strength():
    assert _sig.card_name_strength("DEMO_signature_card_Michel_Neufeglise.png") == 2
    assert _sig.card_name_strength("handtekeningkaart.jpg") == 2
    assert _sig.card_name_strength("DEMO_handwritten_letter_FORGED_signature.png") == 0
    assert _sig.card_name_strength("DEMO_company_registry_extract_Michel_Neufeglise.pdf") == 0
    assert _sig.card_name_strength("design.pdf") == 0


def test_signature_step_decision_needs_card():
    plan = _sig.plan_signature_step(DEMO_Q)
    card = {"doc_id": "C", "name": "card.png", "how": "file name"}
    assert _sig.resolve_signature_step(plan, card, 3)["value"] == "yes"
    assert _sig.resolve_signature_step(plan, None, 3)["value"] == "no"
    laya_only = _sig.resolve_signature_step({"hits": [], "remainder": "x"}, card, 3, {"value": "yes", "confidence": 0.99})
    assert laya_only["value"] == "no"
    agree = _sig.resolve_signature_step(plan, card, 3, {"value": "yes", "confidence": 0.9})
    assert agree["value"] == "yes" and agree["source"] == "laya"


def test_pick_signer():
    assert _sig.name_from_filename("DEMO_signature_card_Michel_Neufeglise.png") == "Michel Neufeglise"
    assert _sig.pick_signer([None, "Jane Example", "Michel Neuféglise"], LETTER_TEXT) == "Michel Neuféglise"
    assert _sig.pick_signer(["Michel Neufeglise"], LETTER_TEXT) == "Michel Neufeglise"


# ── the run ────────────────────────────────────────────────────────────────

def test_demo_run_routes_and_scores(images, tmp_path):
    letter, reg, card = _demo_docs(images)
    pick = se.analyze_letter(images["letter"])[1].pick
    rec = Rec()
    hooks = _hooks([letter, reg, card], rec, tmp_path, pick_reply=f"{pick + 1}")
    events = _run(_req(), hooks)
    assert not [e for e in events if "error" in e], [e for e in events if "error" in e]
    dec = _decisions(events)
    assert dec["intent"]["value"] == "cross_reference"
    assert dec["signature_check"]["value"].startswith("yes")
    assert dec["role-L"]["value"] == "subject"
    assert dec["role-R"]["value"] == "reference"
    assert dec["role-C"]["value"] == "signature_reference"
    assert dec["handwritten-L"]["value"] == "yes"

    # the card is never read as text
    assert _tile(events, "extract-C") is None
    ext = _tile(events, "extract-L")
    assert ext["model"] == "hw-vision:7b" and "handwritten — transcribed by hw-vision:7b" in ext["detail"]
    assert rec.transcribed == ["L"]

    sig = _tile(events, "signature-check")
    assert sig["status"] == "done" and sig["kind"] == "signature"
    data = sig["data"]
    assert data["n_references"] == 5
    assert data["band"] == "consistent" and data["score"] >= 70, data
    assert data["model"] == "sig-vision:7b"
    assert data["questioned"]["url"] == "/api/idp/signatures/run0001abcd/questioned.png"
    assert len(data["references"]) == 5
    assert data["candidates"]["picked_by"] == "model"
    assert len(data["breakdown"]) == len(se.FEATURES)
    assert (tmp_path / "run0001abcd" / "questioned.png").is_file()
    assert (tmp_path / "run0001abcd" / "ref_5.png").is_file()
    assert data["assessment"] and "forensic" in data["assessment"].lower()

    # tile order: laya, extracts, signature, answer
    order = []
    for e in events:
        if "tile" in e and e["tile"]["id"] not in order:
            order.append(e["tile"]["id"])
    assert order.index("signature-check") < order.index("answer")

    # answer prompt carries the result; the score ends up in the answer
    prompt = rec.llm_prompts[-1]
    assert "SIGNATURE VERIFICATION RESULT" in prompt and "**Company check**" in prompt
    assert f"{data['score']}% — consistent with 5 reference signatures" in prompt
    content = "".join(e.get("content", "") for e in events)
    assert f"{data['score']}% — consistent with 5 reference signatures" in content
    assert "not a forensic determination" in content.lower()

    # knowledge graph payload
    kg = rec.kg[-1]
    s = kg["signature"]
    assert s["person"] == "Michel Neuféglise"
    assert s["letter_doc"].id == "L" and [c.id for c in s["card_docs"]] == ["C"]
    assert s["band"] == "consistent"


def test_forged_letter_scores_inconsistent(images, tmp_path):
    letter, reg, card = _demo_docs(images, letter="forged")
    letter.filename = "DEMO_handwritten_letter_FORGED_signature.png"
    rec = Rec()
    events = _run(_req(), _hooks([letter, reg, card], rec, tmp_path, pick_reply="nonsense"))
    data = _tile(events, "signature-check")["data"]
    assert data["band"] == "inconsistent" and data["score"] < 40, data
    assert data["candidates"]["picked_by"] == "heuristic"
    assert _decisions(events)["role-C"]["value"] == "signature_reference"


def test_signature_step_without_vision_model_still_answers(images, tmp_path):
    letter, reg, card = _demo_docs(images)
    rec = Rec()
    events = _run(_req(), _hooks([letter, reg, card], rec, tmp_path, candidates=()))
    sig = _tile(events, "signature-check")
    assert sig["status"] == "done" and sig["data"]["model"] is None
    assert any("no vision-capable" in w.lower() for w in sig["data"]["warnings"])
    assert _tile(events, "answer")["status"] == "done"


def test_signature_step_engine_missing_is_graceful(images, tmp_path, monkeypatch):
    monkeypatch.setattr(se, "available", lambda: (False, "cv2 missing"))
    letter, reg, card = _demo_docs(images)
    rec = Rec()
    events = _run(_req(), _hooks([letter, reg, card], rec, tmp_path))
    sig = _tile(events, "signature-check")
    assert sig["status"] == "error" and "cv2 missing" in sig["detail"]
    assert _tile(events, "answer")["status"] == "done"
    assert "could not be completed" in rec.llm_prompts[-1]


def test_no_card_means_no_signature_step(images, tmp_path):
    letter, reg, _card = _demo_docs(images)
    rec = Rec()
    events = _run(_req(ids=("L", "R")), _hooks([letter, reg], rec, tmp_path))
    dec = _decisions(events)
    assert dec["signature_check"]["value"] == "none"
    assert _tile(events, "signature-check") is None


def test_card_detected_by_probe_without_name_hints(images, tmp_path):
    letter = _doc("L", "IMG_2041.png", "image/png", "", [images["letter"]])
    reg = _doc("R", "extract.pdf", "application/pdf", REGISTRY_TEXT)
    card = _doc("C", "IMG_2042.png", "image/png", "", [images["card"]])
    rec = Rec()
    events = _run(_req(), _hooks([letter, reg, card], rec, tmp_path))
    dec = _decisions(events)
    assert dec["signature_check"]["value"] == "yes — vs IMG_2042.png"
    assert dec["role-C"]["value"] == "signature_reference"


def test_pure_verify_signature_uses_engine(images, tmp_path):
    letter, _reg, card = _demo_docs(images)
    rec = Rec()
    events = _run(_req("compare the signature on the letter with the reference card", ids=("L", "C")),
                  _hooks([letter, card], rec, tmp_path))
    dec = _decisions(events)
    assert dec["intent"]["value"] == "verify_signature"
    sig = _tile(events, "signature-check")
    assert sig["status"] == "done" and sig["data"]["score"] >= 70
    answer = _tile(events, "answer")
    assert answer["kind"] == "vision" and answer["status"] == "done"
    content = "".join(e.get("content", "") for e in events)
    assert "consistent with 5 reference signatures" in content
    assert any("LOCAL ENGINE RESULT" in p for p in rec.vision_prompts)


def test_cached_transcription_is_reused(images, tmp_path):
    letter, reg, card = _demo_docs(images)
    letter.text = LETTER_TEXT
    letter.meta = {"handwritten": True, "transcribed_by": "hw-vision:7b", "last_ocr_at": 1}
    rec = Rec()
    events = _run(_req(), _hooks([letter, reg, card], rec, tmp_path))
    ext = _tile(events, "extract-L")
    assert rec.transcribed == []
    assert "cached" in ext["detail"] and "hw-vision:7b" in ext["detail"]


# ── knowledge graph ────────────────────────────────────────────────────────

def test_kg_ingest_signature_relations(tmp_path, monkeypatch):
    import kg_store as kg
    monkeypatch.setattr(kg, "DB_PATH", tmp_path / "kg.db")

    async def go():
        await kg.init_db()
        letter = _doc("L", "letter.png", "image/png", LETTER_TEXT)
        card = _doc("C", "card.png", "image/png", "")
        await kg.ingest_run(
            conversation_id="dconv-1", run_id="r1", intent="cross_reference", subject_docs=[letter],
            answer_text="", signature={
                "person": "Michel Neuféglise", "letter_doc": letter, "card_docs": [card],
                "score": 84, "band": "consistent", "n_references": 5, "verified_at": "2026-09-28T10:00:00",
            },
        )
        g = await kg.get_graph()
        rels = {(r["src"], r["type"], r["dst"]): r for r in g["relations"]}
        assert ("person:michel neufeglise", "signed", "document:L") in rels
        assert ("document:C", "signature_specimen", "person:michel neufeglise") in rels
        va = rels[("document:L", "verified_against", "document:C")]
        assert va["props"]["score"] == 84 and va["props"]["band"] == "consistent" and va["props"]["verified_at"]
        signed = rels[("person:michel neufeglise", "signed", "document:L")]
        assert signed["props"]["band"] == "consistent" and abs(signed["confidence"] - 0.84) < 1e-6

        # a later inconsistent measurement replaces the score and drops "signed"
        await kg.ingest_run(
            conversation_id="dconv-1", run_id="r2", intent="cross_reference", subject_docs=[letter],
            answer_text="", signature={
                "person": "Michel Neuféglise", "letter_doc": letter, "card_docs": [card],
                "score": 12, "band": "inconsistent", "n_references": 5, "verified_at": "2026-09-28T11:00:00",
            },
        )
        g = await kg.get_graph()
        rels = {(r["src"], r["type"], r["dst"]): r for r in g["relations"]}
        assert ("person:michel neufeglise", "signed", "document:L") not in rels
        assert rels[("document:L", "verified_against", "document:C")]["props"]["band"] == "inconsistent"
        assert abs(rels[("document:L", "verified_against", "document:C")]["confidence"] - 0.12) < 1e-6

        # graph query facts mention the verified signature
        nb = await kg.neighborhood("person:michel neufeglise")
        return nb

    nb = asyncio.run(go())
    assert any(r["type"] == "verified_against" for r in nb["relations"])


def test_graph_query_mentions_signature(tmp_path):
    ents = [
        {"id": "person:m", "type": "person", "name": "Michel Neuféglise", "props": {}},
        {"id": "document:L", "type": "document", "name": "letter.png", "props": {"filename": "letter.png"}},
        {"id": "document:C", "type": "document", "name": "card.png", "props": {"filename": "card.png"}},
    ]
    rels = [
        {"src": "person:m", "dst": "document:L", "type": "signed", "confidence": 0.84,
         "props": {"score": 84, "band": "consistent", "n_references": 5}},
        {"src": "document:L", "dst": "document:C", "type": "verified_against", "confidence": 0.84,
         "props": {"score": 84, "band": "consistent", "verified_at": "2026-09-28T10:00:00"}},
    ]
    rec = Rec()
    hooks = _hooks([], rec, tmp_path)
    hooks.kg_search = AsyncMock(return_value=[ents[0]])
    hooks.kg_neighborhood = AsyncMock(return_value={"entities": ents, "relations": rels})

    async def info(doc, cat):
        return {"model": "text-model", "reason": None}
    hooks.resolve_text_model_info = info
    events = _run({"message": "What do we know about Michel Neuféglise?", "attachments": []}, hooks)
    prompt = rec.llm_prompts[-1]
    assert "—signed→ letter.png" in prompt and "signature score 84% — consistent" in prompt
    assert "Signatures" in prompt
    q = _tile(events, "kg-query")
    assert any("verified_against" in i["label"] for i in q["items"])


# ── model roles ────────────────────────────────────────────────────────────

def test_pick_signature_model_priority():
    from doc_agent_hooks import pick_signature_model
    installed = ["qwen2.5vl:7b", "minicpm-v:latest", "llama3.2-vision:latest", "glm-ocr:latest"]
    vis = lambda n: True  # noqa: E731
    cfg = {"signature_model": "qwen2.5vl:7b", "handwriting_model": "minicpm-v:latest", "vision_model": "llama3.2-vision:latest"}
    assert pick_signature_model(cfg, installed, vis) == "qwen2.5vl:7b"
    cfg["signature_model"] = ""
    assert pick_signature_model(cfg, installed, vis) == "minicpm-v:latest"
    # an OCR-only handwriting model cannot compare signatures → vision model
    cfg["handwriting_model"] = "glm-ocr:latest"
    assert pick_signature_model(cfg, installed, vis) == "llama3.2-vision:latest"


def test_rank_signature_models_signature_first():
    from doc_agent_hooks import rank_signature_models
    deps = Mock()
    deps.model_capabilities = AsyncMock(return_value=["completion", "vision"])
    deps.name_is_vision = lambda n: True
    installed = ["qwen2.5vl:7b", "minicpm-v:latest", "llama3.2-vision:latest", "hf.co/x/Unlimited-OCR:q4"]
    cfg = {"signature_model": "minicpm-v:latest", "handwriting_model": "hf.co/x/Unlimited-OCR:q4",
           "vision_model": "llama3.2-vision:latest"}
    ranked = asyncio.run(rank_signature_models(deps, cfg, installed, []))
    assert ranked[0] == "minicpm-v:latest"
    assert ranked[1] == "llama3.2-vision:latest"
    assert "hf.co/x/Unlimited-OCR:q4" not in ranked


def test_model_role_keys_include_signature():
    src = (Path(__file__).parent.parent / "main.py").read_text()
    assert '"signature_model"' in src.split("_MODEL_ROLE_KEYS = [", 1)[1].split("]", 1)[0]
    assert "signature_model:        str | None = None" in src
    assert '"signature":   ["signature_model", "handwriting_model", "vision_model"]' in src


# ── crop route ─────────────────────────────────────────────────────────────

def test_crop_route_rejects_traversal(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    import main  # noqa: E402  (tests run with an isolated data dir)

    base = tmp_path / "_signatures"
    (base / "abc123").mkdir(parents=True)
    se.save_png(se._cv()[0].zeros((10, 10), dtype="uint8"), base / "abc123" / "questioned.png")
    (tmp_path / "secret.png").write_bytes(b"secret")
    monkeypatch.setattr(main, "_signatures_dir", lambda: base)

    async def get(run_id, name):
        try:
            return await main.idp_signature_crop(run_id, name)
        except fastapi.HTTPException as e:
            return e.status_code

    ok = asyncio.run(get("abc123", "questioned.png"))
    assert getattr(ok, "path", "").endswith("questioned.png")
    for run_id, name in [("..", "secret.png"), ("abc123", "../../secret.png"), ("abc123", "x.png"),
                         ("abc123/..", "questioned.png"), ("abc123", "ref_1.png")]:
        assert asyncio.run(get(run_id, name)) == 404


def test_signature_model_failure_falls_back_to_next(images, tmp_path):
    letter, reg, card = _demo_docs(images)
    rec = Rec()
    hooks = _hooks([letter, reg, card], rec, tmp_path, candidates=("broken-vl:7b", "good-vl:7b"))
    inner = hooks.vision_call
    failed = []

    async def flaky(model, prompt, refs, subjects, **kw):
        if model == "broken-vl:7b":
            failed.append(model)
            raise RuntimeError("llama-server process has terminated")
        return await inner(model, prompt, refs, subjects, **kw)

    hooks.vision_call = flaky
    events = _run(_req(), hooks)
    data = _tile(events, "signature-check")["data"]
    assert failed == ["broken-vl:7b"]          # tried once, then dropped for the step
    assert data["model"] == "good-vl:7b" and data["assessment"]
    assert _tile(events, "answer")["status"] == "done"


def test_letter_role_forced_to_subject_when_laya_says_reference(images, tmp_path):
    letter, reg, card = _demo_docs(images)
    rec = Rec()
    hooks = _hooks([letter, reg, card], rec, tmp_path)
    hooks.laya_role = lambda m, f: {"role": "reference_specimen", "confidence": 0.95}
    dec = _decisions(_run(_req(), hooks))
    assert dec["role-L"]["value"] == "subject"
    assert dec["role-R"]["value"] == "reference"
    assert dec["role-C"]["value"] == "signature_reference"


# ── regressions from the live demo test (test phase) ─────────────────────

def test_rules_route_ownership_phrasings_to_cross_reference():
    """The phrasing used on demo day ("check whether the writer really owns the
    company in the registry") must be a cross_reference + signature step, not a
    pure signature request (the letter would then not be transcribed)."""
    files = [{"name": "DEMO_handwritten_letter_Michel_Neufeglise.png"},
             {"name": "DEMO_company_registry_extract_Michel_Neufeglise.pdf"},
             {"name": "DEMO_signature_card_Michel_Neufeglise.png"}]
    for q in [
        "Read this handwritten letter, check whether the writer really owns the company in the registry, "
        "and verify the signature against the reference card with a confidence score.",
        "Does the writer of this letter own the company in the registry? Also verify the signature against the card.",
        "Lees deze handgeschreven brief, controleer of de schrijver echt eigenaar is van het bedrijf in het "
        "handelsregister en controleer de handtekening met de referentiekaart.",
    ]:
        plan = _sig.plan_signature_step(q)
        assert plan["combined"], q
        assert _agent.rules_intent(plan["remainder"], files)[0] == "cross_reference", q
    assert _agent.rules_intent("Who owns this house?", files)[0] != "cross_reference"


def test_demo_day_phrasing_transcribes_letter(images, tmp_path):
    letter, reg, card = _demo_docs(images)
    rec = Rec()
    q = ("Read this handwritten letter, check whether the writer really owns the company in the registry, "
         "and verify the signature against the reference card with a confidence score.")
    events = _run(_req(q), _hooks([letter, reg, card], rec, tmp_path))
    assert _decisions(events)["intent"]["value"] == "cross_reference"
    assert rec.transcribed == ["L"]
    assert _tile(events, "signature-check")["data"]["score"] >= 70


def test_ocr_only_handwriting_model_is_replaced_by_vision_candidate():
    """An OCR-only Handwriting role (e.g. Unlimited-OCR returns nothing for a
    free-form transcription prompt) → the first Signature/Vision candidate."""
    async def resolve(cat):
        return "hf.co/DevQuasar/baidu.Unlimited-OCR-GGUF:q4_k_m"

    async def cands():
        return ["hf.co/DevQuasar/baidu.Unlimited-OCR-GGUF:q4_k_m", "gemma4:12b"]

    hooks = SimpleNamespace(resolve_model=resolve, signature_candidates=cands)
    model, note = asyncio.run(_agent._handwriting_model(hooks))
    assert model == "gemma4:12b" and note and "OCR-only" in note

    async def resolve_vlm(cat):
        return "qwen2.5vl:7b"
    model, note = asyncio.run(_agent._handwriting_model(SimpleNamespace(resolve_model=resolve_vlm, signature_candidates=cands)))
    assert model == "qwen2.5vl:7b" and note is None

    async def none():
        return []
    model, note = asyncio.run(_agent._handwriting_model(SimpleNamespace(resolve_model=resolve, signature_candidates=none)))
    assert model.endswith("Unlimited-OCR-GGUF:q4_k_m") and note is None


def test_transcribe_prompt_forbids_alignment_spaces():
    # gemma4 reproduced the right-aligned date with hundreds of spaces →
    # Ollama "token repeat limit reached" (HTTP 500)
    assert "flush-left" in _sig.TRANSCRIBE_PROMPT


def test_pure_verify_signature_ingests_kg(images, tmp_path):
    """The verify_signature path has no web lookup; kg_ingest used to crash on
    an unbound web_result and silently wrote nothing."""
    letter, _reg, card = _demo_docs(images)
    rec = Rec()
    _run(_req("compare the signature on the letter with the reference card", ids=("L", "C")),
         _hooks([letter, card], rec, tmp_path))
    assert rec.kg and rec.kg[-1].get("signature") and rec.kg[-1]["signature"]["score"] >= 70


def test_graph_query_specimen_is_not_a_score(tmp_path):
    ents = [
        {"id": "person:m", "type": "person", "name": "Michel Neuféglise", "props": {}},
        {"id": "document:C", "type": "document", "name": "card.png", "props": {"filename": "card.png"}},
    ]
    rels = [{"src": "document:C", "dst": "person:m", "type": "signature_specimen", "confidence": 0.9,
             "props": {"n_references": 5}}]
    rec = Rec()
    hooks = _hooks([], rec, tmp_path)
    hooks.kg_search = AsyncMock(return_value=[ents[0]])
    hooks.kg_neighborhood = AsyncMock(return_value={"entities": ents, "relations": rels})

    async def info(doc, cat):
        return {"model": "text-model", "reason": None}
    hooks.resolve_text_model_info = info
    _run({"message": "What do we know about Michel Neuféglise?", "attachments": []}, hooks)
    prompt = rec.llm_prompts[-1]
    assert "signature_specimen→ Michel Neuféglise [reference signature card with 5 specimen signatures]" in prompt
    assert "(90%)" not in prompt


def test_vision_call_keeps_gemma4_and_disables_thinking(tmp_path, monkeypatch):
    """gemma4 is vision-capable (it was silently swapped for another model) and
    a thinking model: vision calls must send think=false (≈15 s instead of ≈2 min)."""
    import httpx
    import idp_engine as ie

    img = tmp_path / "p.png"
    ss.save(np.full((20, 20), 255, np.uint8), img)
    sent: list[dict] = []
    caps = {"gemma4:12b": ["completion", "vision", "thinking"], "minicpm-v:8b": ["completion", "vision"]}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in caps]})
        body = _json.loads(request.content or b"{}")
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": caps.get(body.get("model"), [])})
        sent.append(body)
        return httpx.Response(200, json={"response": "ok"})

    real = httpx.AsyncClient

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(ie, "_THINKING_CACHE", {})
    assert asyncio.run(ie._ollama_vision_call("gemma4:12b", "hi", [str(img)])) == "ok"
    assert sent[-1]["model"] == "gemma4:12b" and sent[-1]["think"] is False
    asyncio.run(ie._ollama_vision_call("minicpm-v:8b", "hi", [str(img)]))
    assert sent[-1]["model"] == "minicpm-v:8b" and "think" not in sent[-1]


def test_answer_score_mention_accepts_slash_100():
    # gemma4 often writes "93/100"; the engine section was then appended twice
    r = {"score": 93}
    assert _sig.answer_mentions_score("**93/100 — consistent with 5 reference signatures**", r)
    assert _sig.answer_mentions_score("93 % — consistent", r)
    assert not _sig.answer_mentions_score("193% and 9/100", r)
