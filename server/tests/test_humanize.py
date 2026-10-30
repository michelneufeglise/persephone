"""
Block-wise Humanize (and Translate / Redact) pipeline — server/humanize.py —
plus the OCR-vs-text-layer rule and the one-time repair in idp_engine.

No model is ever called: every chat / vision call is faked, and all files
live in pytest's tmp_path (conftest already routes PERSEPHONE_DATA_DIR to a
temp dir).
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from pathlib import Path

import httpx
import pytest

import humanize as hz
import idp_engine as idp


# ── fixtures / helpers ───────────────────────────────────────────────────────
@pytest.fixture
def iso(tmp_path, monkeypatch):
    storage = tmp_path / "uploads"
    storage.mkdir()
    monkeypatch.setattr(idp, "STORAGE_DIR", storage)
    monkeypatch.setattr(idp, "REGISTRY_FILE", storage / "_registry.json")
    monkeypatch.setattr(idp, "REGISTRY", {})
    monkeypatch.setattr(hz, "_RUNS", {})
    return storage


def _doc(doc_id, filename, *, mime="", text="", page_texts=None, page_images=None, meta=None, pages=1):
    return idp.Document(
        id=doc_id, filename=filename, mime=mime or idp._detect_mime(filename), size=1,
        uploaded_at=time.time(), pages=pages, text=text,
        page_texts=list(page_texts if page_texts is not None else ([text] if text else [])),
        page_images=list(page_images or []), meta=dict(meta or {}),
    )


TABLE = [
    ["Fase", "Hindernis", "Label", "Omweg"],
    ["1. Kindertijd en eerste lessen",
     "Haar vader ziet tekenen bij een meisje als tijdverdrijf en niet als voorbereiding op een beroep. Er wordt verwacht dat ze later trouwt.",
     "V",
     "Ze leert in het atelier van haar vader: kopiëren, assisteren en zijn leerlingen observeren. Hij blijft haar lesgeven."],
    ["2. Opleiding",
     "De kunstacademie laat vrouwen niet toe, of in elk geval niet bij de lessen met levende naaktmodellen.",
     "R",
     "Privélessen bij haar vader en collega’s, studeren in musea en tekenen naar gipsafgietsels."],
    ["3. Eerste opdrachten en verkoop",
     "Critici en kopers zien haar werk als “knap voor een vrouw” of schrijven het toe aan de invloed van haar vader.",
     "M",
     "Ze signeert met initialen, exposeert in groepstentoonstellingen en bouwt een netwerk op."],
    ["4. Carriere en privé leven",
     "Van haar wordt verwacht dat ze trouwt en moeder wordt, terwijl dat bij Picasso anders ligt.",
     "V",
     "Ze trouwt niet of kiest een partner die haar werk steunt en regelt hulp in het huishouden."],
]
LAST_Q = "Welke ideeën van Nochlin of Broude & Garrard herken je?"
LAST_A = ("Bij Nochlin herken ik de nadruk op toegang tot opleiding en op netwerken als verklaring "
          "voor haar loopbaan. Bij Broude en Garrard herken ik agency: ze gebruikt het zelfportret.")


def make_docx(path: Path) -> Path:
    """A synthetic copy of the real assignment's structure."""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = Document()
    p = d.add_paragraph()
    p.add_run("Opdracht 1: Pablita en de vraag van Nochlin").bold = True
    d.add_heading("Hindernissen, omwegen en labels", level=2)
    t = d.add_table(rows=len(TABLE), cols=4)
    t.style = "Table Grid"
    for ri, row in enumerate(TABLE):
        for ci, val in enumerate(row):
            t.cell(ri, ci).text = val
    p = d.add_paragraph()
    p.add_run("Reflectie").bold = True
    p = d.add_paragraph(style="List Bullet")
    p.add_run("Welke categorie kwam het vaakst voor?").bold = True
    p.add_run(" V (verwachting of norm) kwam twee keer voor, R en M elk één keer. "
              "Verwachtingen over vrouwelijkheid spelen in bijna elke fase mee.")
    d.add_paragraph("Ik heb gekozen voor Sofonisba Anguissola.")
    d.add_paragraph("Bernardino Campi schildert Sofonisba Anguissola, ca. 1559")
    d.add_paragraph("Sofonisba Anguissola (Cremona, ca. 1535–1625) schilderde haar leraar terwijl hij haar "
                    "portretteert. Vasari prees haar werk. In 1559 werd zij schilder aan het Spaanse hof.")
    p = d.add_paragraph()
    p.add_run("Extra vraag:").bold = True
    p.add_run(" Ik wilde vooral de keuze van de kunstenaar centraal stellen, zonder haar tot slachtoffer te maken.")
    # a paragraph with a hyperlink: never rewritten (write-back could not keep the link)
    p = d.add_paragraph("Meer informatie staat op de website van het museum, zie ")
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), "rId999")
    r = OxmlElement("w:r")
    tt = OxmlElement("w:t")
    tt.text = "deze pagina over de schilderes"
    r.append(tt)
    link.append(r)
    p._p.append(link)
    p = d.add_paragraph(style="List Bullet")
    p.add_run(LAST_Q).bold = True
    p.add_run(" " + LAST_A)
    d.add_paragraph("2024")
    d.save(str(path))
    return path


def register_docx(iso: Path, doc_id: str = "dx1") -> idp.Document:
    name = "feminisme opdracht.docx"
    ddir = iso / doc_id
    ddir.mkdir()
    make_docx(ddir / name)
    text = "\n\n".join(idp._extract_docx(ddir / name))
    doc = _doc(doc_id, name, text=text)
    idp.REGISTRY[doc_id] = doc
    return doc


def body_of(messages: list[dict]) -> str:
    m = re.search(r"<<<\n([\s\S]*?)\n>>>", messages[-1]["content"])
    return m.group(1) if m else messages[-1]["content"]


def fake_chat(transform, reason="stop", calls=None):
    async def chat(model, messages, options):
        if calls is not None:
            calls.append({"model": model, "messages": copy.deepcopy(messages), "options": dict(options)})
        out = transform(body_of(messages), messages)
        yield {"content": out}
        yield {"done": True, "done_reason": reason, "eval_count": 10, "eval_duration": 10**9}
    return chat


async def drain(gen):
    return [ev async for ev in gen]


# ── segmentation ─────────────────────────────────────────────────────────────
def test_segment_docx_structure(tmp_path):
    blocks = hz.segment_docx(make_docx(tmp_path / "a.docx"))
    by_text = {b.text: b for b in blocks}
    assert by_text["Opdracht 1: Pablita en de vraag van Nochlin"].kind == "heading"      # all-bold line
    assert by_text["Hindernissen, omwegen en labels"].kind == "heading"                  # Heading 2 style
    cells = [b for b in blocks if b.table is not None]
    assert len(cells) == 20 and {b.row for b in cells} == {0, 1, 2, 3, 4}
    assert all(not b.rewrite and b.reason == "table header" for b in cells if b.row == 0)
    assert by_text["V"].rewrite is False and by_text["1. Kindertijd en eerste lessen"].rewrite is False
    assert by_text[TABLE[1][1]].rewrite is True
    qa = [b for b in blocks if b.prefix == "Welke categorie kwam het vaakst voor?"][0]
    assert qa.kind == "list" and qa.rewrite and qa.body.startswith("V (verwachting")
    cap = by_text["Bernardino Campi schildert Sofonisba Anguissola, ca. 1559"]
    assert cap.kind == "caption" and not cap.rewrite
    extra = [b for b in blocks if b.prefix == "Extra vraag:"][0]
    assert extra.rewrite and extra.body.startswith("Ik wilde")
    link = [b for b in blocks if b.text.startswith("Meer informatie")][0]
    assert not link.rewrite and link.reason == "links/fields"
    assert by_text["2024"].rewrite is False
    last = [b for b in blocks if b.prefix == LAST_Q][0]
    assert last.rewrite and last.body == LAST_A


def test_markdown_render_keeps_table_and_order(tmp_path):
    blocks = hz.segment_docx(make_docx(tmp_path / "a.docx"))
    md = hz.render_markdown(blocks)
    assert md.startswith("## Opdracht 1: Pablita")
    assert "| Fase | Hindernis | Label | Omweg |\n| --- | --- | --- | --- |" in md
    assert sum(1 for ln in md.splitlines() if ln.startswith("| ")) == 6   # header + separator + 4 rows
    assert "| V |" in md and "| M |" in md
    assert md.index("| 4. Carriere") < md.index("## Reflectie")
    assert md.rstrip().endswith("2024")
    assert f"- {LAST_Q} {LAST_A}" in md


def test_segment_text_markdown_and_bullets():
    text = ("# Titel\n\nEerste alinea met een\nafgebroken regel die doorloopt.\n\n•\nWelke vraag stel je hier? "
            "Het antwoord is lang genoeg om te herschrijven.\n- Tweede punt met genoeg woorden erin.\n\n"
            "| A | B |\n|---|---|\n| kort | Een langere cel met een hele zin erin. |\n")
    blocks = hz.segment_text(text)
    hz._classify_all(blocks)
    kinds = [(b.kind, b.text[:20]) for b in blocks]
    assert kinds[0] == ("heading", "Titel")
    assert blocks[1].text == "Eerste alinea met een afgebroken regel die doorloopt."
    assert blocks[2].kind == "list" and blocks[2].prefix == "Welke vraag stel je hier?"
    assert blocks[3].kind == "list" and blocks[3].marker == "- "
    cells = [b for b in blocks if b.table is not None]
    assert [c.text for c in cells] == ["A", "B", "kort", "Een langere cel met een hele zin erin."]
    assert not cells[0].rewrite and cells[3].rewrite


def test_pdf_line_join_trailing_space_mode():
    assert hz._join_lines(["1. ", "Kinder", "tijd en ", "eerste ", "lessen"], True) == "1. Kindertijd en eerste lessen"
    assert hz._join_lines(["een woord", "en nog een"], False) == "een woord en nog een"
    assert hz._join_lines(["afge-", "broken"], False) == "afgebroken"


def test_segment_pdf_text_layer_and_table(tmp_path):
    import fitz
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Opdracht 2: Het museumbordje", fontsize=13)
    page.insert_textbox(fitz.Rect(72, 100, 520, 220),
                        "Sofonisba Anguissola (Cremona, ca. 1535–1625) schilderde haar leraar terwijl hij haar "
                        "portretteert. Vasari prees haar werk en zij werd schilder aan het Spaanse hof.", fontsize=11)
    path = tmp_path / "x.pdf"
    pdf.save(str(path))
    pdf.close()
    blocks = hz.segment_pdf(path)
    hz._classify_all(blocks)
    assert blocks[0].kind == "heading" and not blocks[0].rewrite
    body = [b for b in blocks if b.rewrite]
    assert body and "Anguissola" in body[0].text and "Vasari" in body[0].text


# ── protected rules / Q&A split ──────────────────────────────────────────────
@pytest.mark.parametrize("text,rewrite,reason", [
    ("Fase", False, "short label"),
    ("1. Opleiding", False, "short label"),
    ("V", False, "short label"),
    ("1535–1625", False, "numbers"),
    ("Bernardino Campi schildert Sofonisba Anguissola, ca. 1559", False, "caption/label"),
    ("Welke categorie kwam het vaakst voor?", False, "question"),
    ("Dit is een gewone zin met genoeg woorden om te herschrijven.", True, ""),
])
def test_protection_rules(text, rewrite, reason):
    b = hz.classify(hz.Block(idx=0, kind="para", text=text))
    assert b.rewrite is rewrite and b.reason == reason


def test_split_lead_question_and_label():
    p, s = hz._split_lead("Hadden de hindernissen te maken met talent? Nee, geen enkele. Pablita heeft talent.")
    assert p == "Hadden de hindernissen te maken met talent?" and s == " "
    p, _ = hz._split_lead("Extra vraag: Ik wilde vooral de keuze centraal stellen.")
    assert p == "Extra vraag:"
    # a '?' after a full sentence is not a Q&A lead
    p, _ = hz._split_lead("Zij zet haar auteurschap in beeld. Wie schildert hier wie? Als dochter kreeg zij les.")
    assert p == ""


# ── quality guard ────────────────────────────────────────────────────────────
SRC = ("Sofonisba Anguissola (Cremona, ca. 1535–1625) schilderde haar leraar. "
       "Als adellijke dochter kreeg zij les van Campi. Vasari prees haar werk in 1559.")


def test_guard_accepts_faithful_rewrite():
    out = ("Sofonisba Anguissola (Cremona, ca. 1535-1625) schilderde haar eigen leraar. "
           "Zij kreeg als adellijke dochter les van Campi, en Vasari prees haar werk in 1559.")
    assert hz.check_block(SRC, out, lang="nl") == []


def test_guard_repetition_missing_and_length():
    rep = SRC + " " + "Als adellijke dochter kreeg zij les van Campi."
    assert "repeated sentence" in hz.check_block(SRC, rep, lang="nl")
    loop = "Zij kreeg les van haar vader en van Campi en zij kreeg les van haar vader en van Campi. " + SRC
    assert any(i.startswith("repeated") for i in hz.check_block(SRC, loop, lang="nl"))
    miss = "Anguissola schilderde haar leraar. Zij kreeg les van een meester, rond 1559 geprezen."
    issues = hz.check_block(SRC, miss, lang="nl")
    assert any(i.startswith("missing:") and "Campi" in i and "Cremona" in i for i in issues)
    # a sentence-initial name only counts when the document uses it as a name elsewhere
    assert not any("Vasari" in i for i in issues)
    names = hz.document_names(["Het werk werd door Vasari geprezen."], "nl")
    assert "Vasari" in names
    assert any("Vasari" in i for i in hz.check_block(SRC, miss, lang="nl", names=names))
    assert any(i.startswith("length") for i in hz.check_block(SRC, "Kort.", lang="nl"))
    assert hz.check_block(SRC, "") == ["empty output"]


def test_guard_key_terms_and_quotes():
    src = "Daardoor mist ze de basis voor historieschilderkunst. Critici noemen het “knap voor een vrouw” en meer."
    bad = "Hierdoor mist ze de basis voor genreschilderkunst. Critici noemen het knap voor een vrouw en meer."
    issues = hz.check_block(src, bad, lang="nl")
    assert any("historieschilderkunst" in i for i in issues)
    assert any(i.startswith("quotation changed") for i in issues)
    # heavy intensity tolerates a reworded key term (names/numbers still checked)
    assert not any("historieschilderkunst" in i for i in hz.check_block(src, bad, intensity="heavy", lang="nl"))
    assert hz.restore_quotes("als “knap”", "als „knap“") == "als “knap”"


def test_clean_output_strips_preamble_think_and_echoed_question():
    b = hz.Block(idx=0, kind="para", text="Vraag hier? Het antwoord volgt nu in deze zin.",
                 prefix="Vraag hier?", sep=" ")
    raw = "<think>hmm</think>Hier is de herschreven tekst:\nVraag hier? Het antwoord komt nu."
    assert hz.clean_output(raw, b) == "Het antwoord komt nu."


# ── prompts & language ───────────────────────────────────────────────────────
def test_dutch_prompt_for_dutch_document():
    lang = hz.detect_language(" ".join(r[1] for r in TABLE[1:]) + " " + LAST_A)
    assert lang == "nl"
    b = hz.Block(idx=0, kind="cell", text=TABLE[1][1], table=0, row=1, col=1, rewrite=True)
    msgs = hz.build_block_messages(b, {"title": "opdracht", "column": "Hindernis", "prev": "x", "next": "y"},
                                   lang="nl", tone="natural", intensity="medium")
    sys_ = msgs[0]["content"]
    assert "Nederlandstalige" in sys_ and "Kortom" in sys_ and "Het is belangrijk om te benadrukken" in sys_
    assert "contraction" not in sys_.lower() and "imperfect" not in sys_.lower() or "Maak nooit opzettelijk fouten" in sys_
    assert "kolom „Hindernis”" in msgs[1]["content"]
    assert msgs[1]["content"].rstrip().endswith(">>>")


def test_english_prompt_rules_per_tone():
    nat = hz._system_prompt("en", "natural", "heavy")
    pro = hz._system_prompt("en", "professional", "light")
    assert "Contractions" in nat and "Contractions" not in pro
    assert "Never add deliberate errors" in nat and "imperfections" not in nat.replace("'human imperfections'", "")
    de = hz._system_prompt("de", "natural", "medium")
    assert "Write in German only" in de and "Contractions" not in de


# ── generation: sizing + continuation ────────────────────────────────────────
def test_num_predict_scales_with_block():
    assert hz.block_num_predict("kort") == 256
    long = "woord " * 1000
    assert hz.block_num_predict(long) >= 2.5 * (len(long) // 3) - 1


def test_continuation_on_length():
    calls = []

    async def chat(model, messages, options):
        calls.append(copy.deepcopy(messages))
        if len(calls) == 1:
            yield {"content": "Eerste helft "}
            yield {"done": True, "done_reason": "length"}
        else:
            yield {"content": "tweede helft."}
            yield {"done": True, "done_reason": "stop"}

    async def go():
        out = []
        async for kind, val in hz.generate(chat, "m", [{"role": "user", "content": "x"}],
                                           temperature=0.3, num_predict=10):
            out.append((kind, val))
        return out

    evs = asyncio.run(go())
    assert evs[-1][0] == "final"
    text, info = evs[-1][1]
    assert text == "Eerste helft tweede helft." and info["continuations"] == 1
    assert calls[1][-1] == {"role": "assistant", "content": "Eerste helft "}


def test_ollama_chat_sends_think_false(monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["json"] = json.loads(request.content)
        body = (json.dumps({"message": {"content": "Hallo"}, "done": False}) + "\n"
                + json.dumps({"message": {"content": ""}, "done": True, "done_reason": "length",
                              "eval_count": 3, "eval_duration": 1000}) + "\n")
        return httpx.Response(200, content=body.encode())

    real = httpx.AsyncClient
    monkeypatch.setattr(hz.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))

    async def go():
        return [ev async for ev in hz.ollama_chat("deepseek-r1:14b", [{"role": "user", "content": "x"}], {"num_predict": 5})]

    evs = asyncio.run(go())
    assert seen["json"]["think"] is False and seen["json"]["stream"] is True
    assert evs[0] == {"content": "Hallo"} and evs[-1]["done_reason"] == "length"


# ── end-to-end pipeline (fake model) ─────────────────────────────────────────
def test_stream_humanize_docx_end_to_end(iso):
    doc = register_docx(iso)
    calls = []
    evs = asyncio.run(drain(hz.stream_humanize(
        doc, "gemma4:26b", {"tone": "natural", "intensity": "medium"},
        chat=fake_chat(lambda body, m: body.replace("vader", "papa"), calls=calls))))
    blocks = [e for e in evs if e.get("phase") == "block"]
    seg = evs[0]
    assert seg["phase"] == "segmented" and seg["language"] == "nl" and seg["of"] == len(blocks)
    assert [b["i"] for b in blocks] == list(range(1, len(blocks) + 1))
    assert blocks[2]["detail"] == f"block 3/{len(blocks)}"
    content = "".join(e.get("content", "") for e in evs)
    assert "| Fase | Hindernis | Label | Omweg |" in content and "papa" in content and " vader" not in content
    assert f"- {LAST_Q} " in content and content.rstrip().endswith("2024")
    done = evs[-1]
    assert done["done"] and done["docx"] is True and done["flagged"] == [] and done["run_id"]
    # thinking off is the transport's job; here: options sized per block + a Dutch system prompt
    assert all(c["options"]["num_predict"] >= 256 for c in calls)
    assert all("Nederlandstalige" in c["messages"][0]["content"] for c in calls)
    assert len(calls) == len(blocks)   # no retries needed
    run = hz.load_run(doc.id, done["run_id"])
    assert run and run["settings"] == {"tone": "natural", "intensity": "medium"}
    assert hz.load_run("other", done["run_id"]) is None
    assert hz.load_run(doc.id, "../../etc") is None
    # survives a process restart: the JSON copy on disk
    hz._RUNS.clear()
    assert hz.load_run(doc.id, done["run_id"])["run_id"] == done["run_id"]


def test_stream_humanize_flags_and_keeps_original_when_guard_fails(iso):
    doc = register_docx(iso, "dx2")
    calls = []
    evs = asyncio.run(drain(hz.stream_humanize(
        doc, "m", {}, chat=fake_chat(lambda body, m: body + " " + body, calls=calls))))
    flags = [e["flag"] for e in evs if "flag" in e]
    n_blocks = len([e for e in evs if e.get("phase") == "block"])
    assert len(flags) == n_blocks and all("repeated sentence" in f["issues"] or
                                          any(i.startswith("repeated") or i.startswith("length") for i in f["issues"])
                                          for f in flags)
    assert len(calls) == 2 * n_blocks                     # one stricter retry per block
    assert calls[1]["options"]["temperature"] == 0.2
    assert "vorige versie" in calls[1]["messages"][1]["content"]
    content = "".join(e.get("content", "") for e in evs)
    assert content == hz.render_markdown(hz.segment_docx(hz.raw_file(doc)))   # originals kept verbatim
    assert evs[-1]["flagged"] == flags


def test_docx_write_back_preserves_layout(iso):
    from docx import Document
    doc = register_docx(iso, "dx3")
    evs = asyncio.run(drain(hz.stream_humanize(
        doc, "m", {}, chat=fake_chat(lambda body, m: body.replace("vader", "papa").replace("Ik wilde", "Ik wou")))))
    run = hz.load_run(doc.id, evs[-1]["run_id"])
    data = hz.docx_for_run(doc, run)
    out = iso / "out.docx"
    out.write_bytes(data)
    d = Document(str(out))
    t = d.tables[0]
    assert len(d.tables) == 1 and len(t.rows) == 5 and len(t.columns) == 4
    assert [c.text for c in t.rows[0].cells] == TABLE[0]
    assert t.cell(1, 1).text == TABLE[1][1].replace("vader", "papa")
    assert t.cell(2, 1).text == TABLE[2][1]                    # nothing to change → identical
    assert "papa" in t.cell(1, 3).text and t.cell(1, 2).text == "V"
    assert t.style.name == "Table Grid"
    paras = d.paragraphs
    styles = [p.style.name for p in paras]
    src_styles = [p.style.name for p in Document(str(hz.raw_file(doc))).paragraphs]
    assert styles == src_styles                                # same paragraphs, same styles
    extra = [p for p in paras if p.text.startswith("Extra vraag:")][0]
    assert extra.text.startswith("Extra vraag: Ik wou vooral")
    assert extra.runs[0].text == "Extra vraag:" and extra.runs[0].bold is True
    assert not any(r.bold for r in extra.runs[1:])
    last = [p for p in paras if p.text.startswith(LAST_Q)][0]
    assert last.runs[0].bold and last.text == f"{LAST_Q} {LAST_A}"
    link = [p for p in paras if p.text.startswith("Meer informatie")][0]
    assert link._p.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}hyperlink") is not None
    with pytest.raises(ValueError):
        hz.docx_for_run(_doc("p1", "a.pdf"), run)


def test_stream_grouped_translate_any_length(iso):
    parts = [f"Dit is alinea nummer {i} met genoeg tekst om echt iets te vertalen en te tellen." for i in range(120)]
    text = "\n\n".join(parts)
    doc = _doc("t1", "lang.txt", text=text)
    seen = []

    async def chat(model, messages, options):
        seen.append(options["num_predict"])
        body = messages[-1]["content"] if messages[-1]["role"] == "user" else messages[-2]["content"]
        yield {"content": body.replace("Dit is alinea nummer", "This is paragraph number")}
        yield {"done": True, "done_reason": "stop"}

    evs = asyncio.run(drain(hz.stream_grouped(doc, "m", "translate", {"target": "English"}, chat=chat)))
    content = "".join(e.get("content", "") for e in evs)
    assert len(seen) > 1                                    # chunked, no 24 000-char cap
    assert content.count("This is paragraph number") == 120
    assert "paragraph number 119 " in content and "\n\n" in content
    assert all(n >= 512 for n in seen)


def test_writing_ops_delegate_in_idp_engine(iso, monkeypatch):
    doc = _doc("t2", "a.txt", text="Een alinea die lang genoeg is om door de pijplijn te gaan, met Campi erin.")

    async def prep(m):
        return m or "x"

    monkeypatch.setattr(idp, "_prepare_text_model", prep)
    monkeypatch.setattr(hz, "ollama_chat", fake_chat(lambda body, m: body))
    out = asyncio.run(idp.humanize(doc, "m", "natural", "light"))
    assert "Campi" in out


# ── OCR keeps the native text layer; one-time repair ────────────────────────
NATIVE = ("Sofonisba Anguissola (Cremona, ca. 1535–1625) schilderde haar leraar terwijl hij haar "
          "portretteert. Vasari prees haar werk.")


def make_pdf(path: Path, with_text_page2: bool = False) -> Path:
    import fitz
    pdf = fitz.open()
    p1 = pdf.new_page()
    p1.insert_textbox(fitz.Rect(72, 72, 520, 300), NATIVE, fontsize=11)
    p2 = pdf.new_page()
    if with_text_page2:
        p2.insert_text((72, 72), "kort")
    pdf.save(str(path))
    pdf.close()
    return path


def register_pdf(iso: Path, doc_id: str, *, page_texts=None, meta=None) -> idp.Document:
    ddir = iso / doc_id
    ddir.mkdir()
    make_pdf(ddir / "scan.pdf")
    imgs = []
    for i in (1, 2):
        p = ddir / f"page_{i:04d}.png"
        p.write_bytes(b"\x89PNG fake")
        imgs.append(str(p))
    native = idp.native_page_texts(_doc(doc_id, "scan.pdf"))
    pts = page_texts if page_texts is not None else native
    doc = _doc(doc_id, "scan.pdf", text="\n\n".join(pts).strip(), page_texts=pts, page_images=imgs,
               meta=meta, pages=2)
    idp.REGISTRY[doc_id] = doc
    return doc


def test_usable_text_layer_heuristic():
    assert not idp.usable_text_layer("  12  \n p. 3 ")
    assert idp.usable_text_layer(NATIVE)


def test_ocr_never_overwrites_usable_text_layer(iso, monkeypatch):
    async def fake_vision(model, prompt, image_paths, *, num_predict=2048):
        return "Sofonisba Anguisola twint knaap (Cremona) " * 5 + Path(image_paths[0]).stem

    monkeypatch.setattr(idp, "_ollama_vision_call", fake_vision)
    doc = register_pdf(iso, "o1")
    native = list(doc.page_texts)
    out = asyncio.run(idp.run_ocr(doc, "ocr-model"))
    assert "twint" in out                                     # the OCR result is still returned
    assert doc.page_texts[0] == native[0] and "twint" not in doc.page_texts[0]
    assert "page_0002" in doc.page_texts[1]                   # image-only page gets OCR text
    assert "twint" not in doc.text.split("\n\n")[0]
    side = idp.ocr_page_texts(doc)
    assert "twint" in side[0]
    assert doc.meta["ocr_text_layer_pages"] == [1] and doc.meta[idp.TEXT_LAYER_MARK] == 1
    saved = json.loads(idp.REGISTRY_FILE.read_text())
    assert saved["o1"]["page_texts"][0] == native[0]


def test_ocr_image_documents_still_get_text(iso, monkeypatch):
    async def fake_vision(model, prompt, image_paths, *, num_predict=2048):
        return "Tekst uit de scan van een brief"

    monkeypatch.setattr(idp, "_ollama_vision_call", fake_vision)
    p = iso / "im1"
    p.mkdir()
    (p / "brief.png").write_bytes(b"\x89PNG fake")
    doc = _doc("im1", "brief.png", page_texts=[""], page_images=[str(p / "brief.png")])
    idp.REGISTRY["im1"] = doc
    asyncio.run(idp.run_ocr(doc, "ocr"))
    assert doc.text == "Tekst uit de scan van een brief"
    # a second OCR pass on an image doc replaces the earlier OCR text (no text layer to keep)
    async def v2(model, prompt, image_paths, *, num_predict=2048):
        return "Betere tekst uit de scan van een brief, nu completer"
    monkeypatch.setattr(idp, "_ollama_vision_call", v2)
    asyncio.run(idp.run_ocr(doc, "ocr"))
    assert doc.text.startswith("Betere tekst")


def test_restore_text_layer_repairs_once(iso):
    garbled = "Sofonisba Anguisola twint knaap twint knaap twint knaap twint knaap twint"
    doc = register_pdf(iso, "r1", page_texts=[garbled, "ocr van lege pagina"],
                       meta={"last_ocr_at": 1, "ocr_model": "x"})
    assert idp.repair_ocr_overwrites() == 1
    assert doc.page_texts[0].strip() == NATIVE.replace("\n", " ").strip() or "Vasari" in doc.page_texts[0]
    assert "twint" not in doc.text
    assert doc.page_texts[1] == "ocr van lege pagina"          # no usable layer → OCR text stays
    assert idp.ocr_page_texts(doc)[0] == garbled              # old OCR kept aside
    assert doc.meta["ocr_text_layer_pages"] == [1]
    saved = json.loads(idp.REGISTRY_FILE.read_text())
    assert "twint" not in saved["r1"]["text"] and saved["r1"]["meta"][idp.TEXT_LAYER_MARK] == 1
    before = copy.deepcopy(doc.page_texts)
    assert idp.restore_text_layer(doc) is False and idp.repair_ocr_overwrites() == 0
    assert doc.page_texts == before


def test_restore_skips_handwriting_images_and_unocrd(iso):
    hw = register_pdf(iso, "h1", page_texts=["getranscribeerde brief " * 3, ""],
                      meta={"last_ocr_at": 1, "handwritten": True})
    plain = register_pdf(iso, "n1", page_texts=["iets anders", ""], meta={})
    img = _doc("i1", "a.png", page_texts=["ocr"], meta={"last_ocr_at": 1})
    idp.REGISTRY["i1"] = img
    assert idp.repair_ocr_overwrites() == 0
    assert hw.page_texts[0].startswith("getranscribeerde") and plain.page_texts[0] == "iets anders"
    assert idp.TEXT_LAYER_MARK not in img.meta


def test_get_document_fresh_repairs_lazily(iso):
    doc = register_pdf(iso, "g1", page_texts=["kapotte ocr " * 6, ""], meta={"last_ocr_at": 1})
    got = idp.get_document_fresh("g1")
    assert got is doc and "Vasari" in doc.page_texts[0] and doc.meta[idp.TEXT_LAYER_MARK] == 1


# ── writing role resolution (main) ───────────────────────────────────────────
@pytest.fixture
def main_mod(monkeypatch):
    import main
    cfg: dict[str, str] = {}

    async def get_config(k):
        return cfg.get(k, "")

    monkeypatch.setattr(main._db, "get_config", get_config)
    return main, cfg


def test_writing_role_priority(main_mod):
    main, cfg = main_mod
    cfg.update({"active_model": "deepseek-r1:14b", "docs_model": "gemma4:12b"})
    routed = _doc("w1", "a.docx", meta={"route": {"category": "text", "model": "deepseek-r1:14b"}})
    # the cached auto-route to a reasoning model is ignored → docs_model
    assert asyncio.run(main._resolve_writing_model(routed)) == "gemma4:12b"
    # a reasoning-model override does not hijack writing ops
    ov = _doc("w2", "a.docx", meta={"route_override": "deepseek-r1:14b"})
    assert asyncio.run(main._resolve_writing_model(ov)) == "gemma4:12b"
    ov2 = _doc("w3", "a.docx", meta={"route_override": "gemma4:26b"})
    assert asyncio.run(main._resolve_writing_model(ov2)) == "gemma4:26b"
    cfg["writing_model"] = "gemma4:26b"
    assert asyncio.run(main._resolve_writing_model(ov)) == "gemma4:26b"
    cfg.pop("writing_model")
    cfg.pop("docs_model")
    assert asyncio.run(main._resolve_writing_model(_doc("w4", "a.txt"))) == "deepseek-r1:14b"
    assert "writing_model" in main._MODEL_ROLE_KEYS
    assert "writing_model" in main.ModelRolesUpdate.model_fields


def test_stream_endpoint_and_docx_download(iso, main_mod, monkeypatch):
    from fastapi.testclient import TestClient
    main, cfg = main_mod
    cfg.update({"writing_model": "gemma4:26b", "active_model": "deepseek-r1:14b"})
    doc = register_docx(iso, "dx9")

    async def prep(m):
        return m

    used = []
    monkeypatch.setattr(idp, "_prepare_text_model", prep)
    monkeypatch.setattr(hz, "ollama_chat", fake_chat(lambda body, m: body.replace("vader", "papa"), calls=used))
    client = TestClient(main.app)
    r = client.post("/api/idp/stream/humanize", json={"doc_id": doc.id, "options": {"intensity": "medium"}})
    assert r.status_code == 200
    evs = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: {")]
    assert evs[0] == {"phase": "start", "model": "gemma4:26b"}
    assert {c["model"] for c in used} == {"gemma4:26b"}
    done = [e for e in evs if e.get("done")][0]
    assert done["docx"] and r.text.rstrip().endswith("data: [DONE]")
    d = client.post("/api/idp/humanize/docx", json={"doc_id": doc.id, "run_id": done["run_id"]})
    assert d.status_code == 200 and d.content[:2] == b"PK"
    cd = d.headers["content-disposition"]
    assert "feminisme opdracht (humanized).docx" in cd or "feminisme%20opdracht%20%28humanized%29.docx" in cd
    assert client.post("/api/idp/humanize/docx", json={"doc_id": doc.id, "run_id": "0" * 16}).status_code == 404
    # non-streaming endpoint returns run id + text
    r2 = client.post("/api/idp/humanize", json={"doc_id": doc.id, "options": {}})
    assert r2.status_code == 200 and r2.json()["model"] == "gemma4:26b" and "papa" in r2.json()["text"]
