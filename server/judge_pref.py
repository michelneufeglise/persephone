"""
Judge-model preference helpers (pure, no imports from main).

`judge_model` (app_config) is the chat auto-router judge. It is normally an
Ollama model id, but can be the Laya sentinel (``laya-builtin``). Laya is a
classifier, not a generative model, so every LLM consumer of the judge
(the LLM-judge fallback when Laya is unsure, delegate categorisation, skill
selection, spoken summaries, background fact extraction) needs an Ollama
model instead. That model is stored in `judge_fallback_model` — the LLM
judge the user picked in the setup wizard (or last picked in Settings)
before switching to Laya.
"""

from __future__ import annotations

LAYA_JUDGE_ID = "laya-builtin"


def llm_judge_pref(judge_model: str | None, fallback_model: str | None,
                   laya_id: str = LAYA_JUDGE_ID) -> str:
    """The Ollama model to use wherever an LLM judge is needed.

    - judge_model is a regular model  → judge_model
    - judge_model is Laya (or empty)  → fallback_model (may be "")
    The fallback is never the Laya sentinel itself.
    """
    judge = (judge_model or "").strip()
    if judge and judge != laya_id:
        return judge
    fb = (fallback_model or "").strip()
    return fb if fb and fb != laya_id else ""


def memory_model_for(judge_model: str | None, fallback_model: str | None,
                     laya_id: str = LAYA_JUDGE_ID) -> str | None:
    """Value to mirror into `memory_model` when the judge changes, or None
    to leave memory_model untouched.

    memory_model follows the judge (same small, fast model) — except Laya,
    which cannot extract facts; then it follows the LLM fallback judge, and
    if there is none it is left as it was.
    """
    judge = judge_model or ""
    if judge != laya_id:
        return judge
    fb = llm_judge_pref(judge, fallback_model, laya_id)
    return fb or None


def fallback_after_role_change(new_judge: str | None, previous_judge: str | None,
                               current_fallback: str | None,
                               laya_id: str = LAYA_JUDGE_ID) -> str | None:
    """New `judge_fallback_model` after the judge role is reassigned in
    Settings, or None to leave it unchanged.

    - picking a regular model     → it becomes the fallback too
    - switching to Laya           → remember the previous LLM judge (if any)
      so it keeps serving as the fallback
    - clearing the judge ("")     → unchanged
    """
    new = (new_judge or "").strip()
    if new and new != laya_id:
        return new
    if new == laya_id:
        prev = (previous_judge or "").strip()
        if prev and prev != laya_id and prev != (current_fallback or ""):
            return prev
    return None
