"""
Setup-wizard additions: the Laya "Decision model" step (one-click download)
and the Signature Verification role, plus the LLM fallback judge kept behind
Laya when it is used as the chat auto-router.

No network: ensure_downloaded / is_available / package_installed are mocked,
config reads/writes go to an in-memory dict, and Ollama calls are faked.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import judge_pref as jp       # noqa: E402
import laya_decider as ld     # noqa: E402
import model_catalog          # noqa: E402

LAYA = "laya-builtin"


# ── judge_pref (pure) ──────────────────────────────────────────────────────

class TestJudgePref:
    def test_regular_judge_is_used_directly(self):
        assert jp.llm_judge_pref("qwen2.5:1.5b", "llama3.2:3b") == "qwen2.5:1.5b"

    def test_laya_uses_fallback(self):
        assert jp.llm_judge_pref(LAYA, "llama3.2:3b") == "llama3.2:3b"

    def test_laya_without_fallback_is_empty(self):
        assert jp.llm_judge_pref(LAYA, "") == ""
        assert jp.llm_judge_pref(LAYA, None) == ""

    def test_fallback_never_laya(self):
        assert jp.llm_judge_pref(LAYA, LAYA) == ""

    def test_memory_model_follows_judge_or_fallback(self):
        assert jp.memory_model_for("qwen2.5:1.5b", "x") == "qwen2.5:1.5b"
        assert jp.memory_model_for("", "x") == ""
        assert jp.memory_model_for(LAYA, "llama3.2:3b") == "llama3.2:3b"
        assert jp.memory_model_for(LAYA, "") is None   # leave untouched

    def test_fallback_after_role_change(self):
        # picking a regular model → it becomes the fallback too
        assert jp.fallback_after_role_change("llama3.2:3b", "qwen2.5:1.5b", "") == "llama3.2:3b"
        # switching to Laya → previous LLM judge is remembered
        assert jp.fallback_after_role_change(LAYA, "qwen2.5:1.5b", "") == "qwen2.5:1.5b"
        # switching to Laya when the fallback already equals the old judge → unchanged
        assert jp.fallback_after_role_change(LAYA, "qwen2.5:1.5b", "qwen2.5:1.5b") is None
        # Laya → Laya, or clearing the judge → unchanged
        assert jp.fallback_after_role_change(LAYA, LAYA, "qwen2.5:1.5b") is None
        assert jp.fallback_after_role_change("", "qwen2.5:1.5b", "qwen2.5:1.5b") is None


# ── laya_decider download job ──────────────────────────────────────────────

@pytest.fixture
def fresh_download_state():
    ld._download_thread = None
    ld._download_error = None
    ld._last_download_error = None
    yield
    t = ld._download_thread
    if t is not None:
        t.join(timeout=5)
    ld._download_thread = None
    ld._download_error = None
    ld._last_download_error = None


def _wait_done(timeout=5.0):
    end = time.time() + timeout
    while ld.is_downloading() and time.time() < end:
        time.sleep(0.01)


class TestLayaDownload:
    def test_already_available_returns_done_without_downloading(self, fresh_download_state):
        with patch.object(ld, "is_available", return_value=True), \
             patch.object(ld, "ensure_downloaded") as ens, \
             patch.object(ld, "cache_size_bytes", return_value=846_123_456):
            res = ld.start_download()
        assert res["state"] == "done"
        assert res["available"] is True and res["downloading"] is False
        assert res["size_bytes"] == 846_123_456 and res["size_is_estimate"] is False
        ens.assert_not_called()

    def test_package_missing_is_unavailable(self, fresh_download_state):
        with patch.object(ld, "is_available", return_value=False), \
             patch.object(ld, "package_installed", return_value=False), \
             patch.object(ld, "ensure_downloaded") as ens:
            res = ld.start_download()
        assert res["state"] == "unavailable"
        assert res["package_installed"] is False
        assert "laya" in (res["error"] or "")
        ens.assert_not_called()

    def test_start_is_idempotent_while_running(self, fresh_download_state):
        gate = threading.Event()
        calls = []
        avail = {"v": False}

        def fake_ensure():
            calls.append(1)
            gate.wait(5)
            avail["v"] = True
            return True

        with patch.object(ld, "is_available", side_effect=lambda: avail["v"]), \
             patch.object(ld, "package_installed", return_value=True), \
             patch.object(ld, "ensure_downloaded", side_effect=fake_ensure), \
             patch.object(ld, "cache_size_bytes", return_value=None):
            first = ld.start_download()
            second = ld.start_download()
            st = ld.download_status()
            assert first["state"] == "started"
            assert second["state"] == "running"
            assert st["downloading"] is True and st["available"] is False
            # not downloaded → approximate size is reported as an estimate
            assert st["size_bytes"] == ld.LAYA_APPROX_DOWNLOAD_BYTES
            assert st["size_is_estimate"] is True
            gate.set()
            _wait_done()
            done = ld.start_download()
            final = ld.download_status()
        assert len(calls) == 1
        assert done["state"] == "done"
        assert final["available"] is True and final["downloading"] is False
        assert final["error"] is None

    def test_failure_reports_error_and_allows_retry(self, fresh_download_state):
        def failing():
            ld._last_download_error = "ConnectionError: offline"
            return False

        with patch.object(ld, "is_available", return_value=False), \
             patch.object(ld, "package_installed", return_value=True), \
             patch.object(ld, "ensure_downloaded", side_effect=failing) as ens:
            assert ld.start_download()["state"] == "started"
            _wait_done()
            st = ld.download_status()
            assert st["downloading"] is False
            assert st["error"] == "ConnectionError: offline"
            # retry starts a new job and clears the old error
            assert ld.start_download()["state"] == "started"
            _wait_done()
        assert ens.call_count == 2

    def test_download_status_shape(self, fresh_download_state):
        with patch.object(ld, "is_available", return_value=False), \
             patch.object(ld, "package_installed", return_value=True):
            st = ld.download_status()
        for key in ("available", "loaded", "device", "package_installed", "downloading",
                    "error", "size_bytes", "size_is_estimate", "target_device", "params"):
            assert key in st
        assert st["target_device"] in ("mps", "cpu", "cuda")
        assert st["params"] == "~421M"

    def test_ensure_downloaded_records_error(self, fresh_download_state):
        import types
        fake_hub = types.ModuleType("huggingface_hub")

        def boom(*a, **k):
            raise OSError("no network")

        fake_hub.snapshot_download = boom
        with patch.object(ld, "is_available", return_value=False), \
             patch.dict(sys.modules, {"huggingface_hub": fake_hub}):
            assert ld.ensure_downloaded() is False
        assert ld._last_download_error == "OSError: no network"


def test_downloaded_bytes_counts_partial_blobs(tmp_path, monkeypatch):
    from huggingface_hub import constants
    blobs = tmp_path / "models--convaiinnovations--laya" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "a").write_bytes(b"x" * 1000)
    (blobs / "b.incomplete").write_bytes(b"x" * 500)
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    assert ld.downloaded_bytes() == 1500
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path / "missing"))
    assert ld.downloaded_bytes() == 0


# ── local-first: loading cached Laya never queries huggingface.co ──────────

class TestLayaOfflineLoad:
    def _fake_snapshot(self, calls, path):
        def fake(*args, **kwargs):
            calls.append(kwargs)
            if not kwargs.get("local_files_only"):
                raise AssertionError("network snapshot_download while loading the router")
            return str(path)
        return fake

    def test_local_snapshot_dir_is_cache_only(self, tmp_path, monkeypatch):
        import huggingface_hub
        (tmp_path / "rl_agent_config.json").write_text("{}")
        (tmp_path / "model.safetensors").write_bytes(b"x")
        calls: list[dict] = []
        monkeypatch.setattr(huggingface_hub, "snapshot_download", self._fake_snapshot(calls, tmp_path))
        assert ld._local_snapshot_dir() == str(tmp_path)
        assert calls and all(c.get("local_files_only") is True for c in calls)

    def test_local_snapshot_dir_none_when_incomplete_or_missing(self, tmp_path, monkeypatch):
        import huggingface_hub
        calls: list[dict] = []
        monkeypatch.setattr(huggingface_hub, "snapshot_download", self._fake_snapshot(calls, tmp_path))
        assert ld._local_snapshot_dir() is None          # no weights in the dir
        def boom(*a, **k):
            raise FileNotFoundError("not cached")
        monkeypatch.setattr(huggingface_hub, "snapshot_download", boom)
        assert ld._local_snapshot_dir() is None

    def test_router_built_from_local_snapshot(self, tmp_path, monkeypatch):
        import types
        made: list[dict] = []

        class FakeRouter:
            def __init__(self, **kwargs):
                made.append(kwargs)
                self.device = kwargs.get("device")

        fake_torch = types.SimpleNamespace(
            backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)))
        monkeypatch.setitem(sys.modules, "laya", types.SimpleNamespace(Router=FakeRouter))
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        monkeypatch.setattr(ld, "_router_instance", None)
        monkeypatch.setattr(ld, "is_available", lambda: True)
        monkeypatch.setattr(ld, "_reset_idle_timer", lambda: None)
        monkeypatch.setattr(ld, "_local_snapshot_dir", lambda: str(tmp_path))
        assert ld._get_or_create_router() is not None
        assert made[0]["models"] == {"english": str(tmp_path)}
        assert made[0]["default"] == "english"

    def test_download_still_uses_network(self, monkeypatch):
        import huggingface_hub
        calls: list[dict] = []
        monkeypatch.setattr(ld, "is_available", lambda: False)
        monkeypatch.setattr(huggingface_hub, "snapshot_download",
                            lambda *a, **k: calls.append(k) or "/tmp/x")
        assert ld.ensure_downloaded() is True
        assert calls and not calls[0].get("local_files_only")


# ── catalog: signature category ────────────────────────────────────────────

def test_catalog_has_signature_category_with_vision_models():
    assert "signature" in model_catalog.CATEGORIES
    sig = [m for m in model_catalog.MODELS if m["category"] == "signature"]
    ids = {m["id"] for m in sig}
    assert {"gemma4:12b", "qwen2.5vl:7b", "minicpm-v:latest"} <= ids
    import idp_engine
    from doc_agent_hooks import is_ocr_only_model
    for m in sig:
        assert idp_engine._name_is_vision(m["id"]), m["id"]
        assert not is_ocr_only_model(m["id"]), m["id"]


def test_signature_entries_do_not_shadow_library_labels():
    import ollama_library
    names = {m["id"]: m["name"] for m in ollama_library._catalog_models(set())}
    assert "Signature" not in names["gemma4:12b"]
    assert "Signature" not in names["minicpm-v:latest"]


def test_recommendations_include_signature_bucket():
    recs = model_catalog.get_recommendations("high", set())
    assert recs["signature"], "signature bucket should not be empty on 'high'"


# ── main.py: wizard save, Settings roles, LLM judge fallback ──────────────

@pytest.fixture
def main_mod(monkeypatch):
    pytest.importorskip("fastapi")
    import main
    store: dict[str, str] = {}

    async def get_config(k):
        return store.get(k)

    async def set_config(k, v):
        store[k] = v

    monkeypatch.setattr(main._db, "get_config", get_config)
    monkeypatch.setattr(main._db, "set_config", set_config)
    monkeypatch.setattr(main._emb, "set_embed_model", lambda *_: None)
    monkeypatch.setattr(main, "invalidate_context_cache", lambda *a, **k: None)
    main._test_store = store
    return main


def _wizard(main, **kw):
    req = main.WizardCompleteRequest(account_name="Tester", **kw)
    return asyncio.run(main.setup_complete(req))


class TestWizardSave:
    def test_saves_signature_model(self, main_mod):
        _wizard(main_mod, judge_model="qwen2.5:1.5b", signature_model="gemma4:12b")
        s = main_mod._test_store
        assert s["signature_model"] == "gemma4:12b"
        assert s["judge_model"] == "qwen2.5:1.5b"
        assert s["judge_fallback_model"] == "qwen2.5:1.5b"
        assert s["memory_model"] == "qwen2.5:1.5b"

    def test_laya_router_keeps_llm_judge_as_fallback(self, main_mod):
        _wizard(main_mod, judge_model=LAYA, judge_fallback_model="llama3.2:3b")
        s = main_mod._test_store
        assert s["judge_model"] == LAYA
        assert s["judge_fallback_model"] == "llama3.2:3b"
        # memory_model follows the LLM fallback judge, never Laya
        assert s["memory_model"] == "llama3.2:3b"

    def test_laya_router_without_fallback_leaves_memory_model(self, main_mod):
        main_mod._test_store["memory_model"] = "qwen2.5:0.5b"
        _wizard(main_mod, judge_model=LAYA)
        s = main_mod._test_store
        assert s["judge_fallback_model"] == ""
        assert s["memory_model"] == "qwen2.5:0.5b"


class TestRolesUpdate:
    def _post(self, main, **kw):
        return asyncio.run(main.update_model_roles(main.ModelRolesUpdate(**kw)))

    def test_switching_to_laya_remembers_previous_llm_judge(self, main_mod):
        self._post(main_mod, judge_model="qwen2.5:1.5b")
        s = main_mod._test_store
        assert s["judge_fallback_model"] == "qwen2.5:1.5b"
        self._post(main_mod, judge_model=LAYA)
        assert s["judge_model"] == LAYA
        assert s["judge_fallback_model"] == "qwen2.5:1.5b"
        assert s["memory_model"] == "qwen2.5:1.5b"

    def test_explicit_fallback_update(self, main_mod):
        self._post(main_mod, judge_model=LAYA, judge_fallback_model="llama3.2:3b")
        s = main_mod._test_store
        assert s["judge_fallback_model"] == "llama3.2:3b"
        assert s["memory_model"] == "llama3.2:3b"

    def test_fallback_only_update_syncs_memory_when_judge_is_laya(self, main_mod):
        s = main_mod._test_store
        s.update({"judge_model": LAYA, "judge_fallback_model": "qwen2.5:1.5b",
                  "memory_model": "qwen2.5:1.5b"})
        self._post(main_mod, judge_fallback_model="llama3.2:3b")   # Settings dropdown
        assert s["memory_model"] == "llama3.2:3b"
        self._post(main_mod, judge_fallback_model="")              # "None" → leave as is
        assert s["memory_model"] == "llama3.2:3b"

    def test_fallback_only_update_leaves_memory_for_regular_judge(self, main_mod):
        s = main_mod._test_store
        s.update({"judge_model": "qwen2.5:1.5b", "memory_model": "qwen2.5:1.5b"})
        self._post(main_mod, judge_fallback_model="llama3.2:3b")
        assert s["memory_model"] == "qwen2.5:1.5b"
        assert s["judge_model"] == "qwen2.5:1.5b"

    def test_regular_judge_change_matches_head_behaviour(self, main_mod):
        s = main_mod._test_store
        self._post(main_mod, judge_model="qwen2.5:1.5b")
        assert s["judge_model"] == s["memory_model"] == "qwen2.5:1.5b"
        self._post(main_mod, judge_model="")
        assert s["judge_model"] == s["memory_model"] == ""

    def test_fallback_is_a_role_key(self, main_mod):
        assert "judge_fallback_model" in main_mod._MODEL_ROLE_KEYS
        roles = asyncio.run(main_mod.get_model_roles())
        assert "judge_fallback_model" in roles and "signature_model" in roles


class _FakeResp:
    status_code = 200

    def json(self):
        return {"message": {"content": '{"category": "code"}'}}


def _fake_client(seen):
    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            seen.append(json["model"])
            return _FakeResp()
    return C


class TestLlmJudgeFallback:
    def test_laya_unsure_falls_back_to_configured_llm_judge(self, main_mod, monkeypatch):
        main = main_mod
        main._test_store.update({"judge_model": LAYA, "judge_fallback_model": "llama3.2:3b"})
        seen: list[str] = []
        monkeypatch.setattr(main.httpx, "AsyncClient", _fake_client(seen))
        if main._laya is not None:
            monkeypatch.setattr(main._laya, "judge_chat_category", lambda *a, **k: None)
        installed = {"qwen2.5:1.5b", "llama3.2:3b"}
        cat = asyncio.run(main._llm_judge("refactor this function", installed))
        assert cat == "code"
        # the wizard-chosen LLM judge, NOT the first installed (qwen2.5:1.5b)
        assert seen == ["llama3.2:3b"]

    def test_laya_without_fallback_uses_first_installed(self, main_mod, monkeypatch):
        main = main_mod
        main._test_store.update({"judge_model": LAYA})
        seen: list[str] = []
        monkeypatch.setattr(main.httpx, "AsyncClient", _fake_client(seen))
        if main._laya is not None:
            monkeypatch.setattr(main._laya, "judge_chat_category", lambda *a, **k: None)
        asyncio.run(main._llm_judge("refactor this function", {"qwen2.5:1.5b", "llama3.2:3b"}))
        assert seen == ["qwen2.5:1.5b"]

    def test_laya_confident_skips_llm(self, main_mod, monkeypatch):
        main = main_mod
        if main._laya is None:
            pytest.skip("laya_decider not importable")
        main._test_store.update({"judge_model": LAYA, "judge_fallback_model": "llama3.2:3b"})
        seen: list[str] = []
        monkeypatch.setattr(main.httpx, "AsyncClient", _fake_client(seen))
        monkeypatch.setattr(main._laya, "judge_chat_category", lambda *a, **k: "reasoning")
        assert asyncio.run(main._llm_judge("prove it", {"llama3.2:3b"})) == "reasoning"
        assert seen == []


class TestDownloadEndpoint:
    def test_route_download_endpoint_delegates(self, main_mod, monkeypatch):
        main = main_mod
        if main._laya is None:
            pytest.skip("laya_decider not importable")
        monkeypatch.setattr(main._laya, "start_download", lambda: {"state": "running", "downloading": True})
        assert asyncio.run(main.idp_route_download()) == {"state": "running", "downloading": True}

    def test_route_status_endpoint_uses_download_status(self, main_mod, monkeypatch):
        main = main_mod
        if main._laya is None:
            pytest.skip("laya_decider not importable")
        monkeypatch.setattr(main._laya, "download_status", lambda: {"available": False, "downloading": False})
        assert asyncio.run(main.idp_route_status()) == {"available": False, "downloading": False}
