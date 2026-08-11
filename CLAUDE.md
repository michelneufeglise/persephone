# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Required workflow for every request

Apply these steps to **every** question or task, in order:

1. **Plan first — on the main thread (Claude Opus or Fable).** Before touching any code, produce a numbered plan and get it approved. The main session already runs Opus.
2. **Implement via a Claude Haiku 4.5 sub-agent.** Dispatch the approved plan to a sub-agent with `model: haiku` (the Agent tool). Do **not** write code on the main Opus thread.
3. **Test via a Claude Haiku 4.5 sub-agent.** Dispatch running/verifying to a sub-agent with `model: haiku`.

Pure questions or trivial one-liners: answer directly, no sub-agents. This workflow is injected automatically on every prompt by a `UserPromptSubmit` hook in `.claude/settings.local.json` — the main thread's model cannot be switched by a hook, so the Haiku phases run as sub-agents by design. If a designated model is unavailable, say so and fall back to the closest available model rather than skipping the phase.

## What this is

Persephone is a **local-first AI chat app**: a React + Vite frontend, a FastAPI (Python) backend that proxies a local **Ollama** install, and an Electron shell that packages both into a `.dmg`/`.exe`. Everything runs on `127.0.0.1` — no data leaves the machine. Beyond chat it includes deep research, persistent memory, a task scheduler, background workers, MCP tool execution, IDP/OCR, Kokoro TTS, a Reels video studio, and an Ableton Live composer.

The frontend lives in `src/`, the backend in `server/`, the Electron main process in `electron/`, and all dev/build orchestration in `scripts/` (Node `.mjs` files).

## Commands

```bash
npm run dev              # THE dev command: frees ports 8000+5173, then runs FastAPI + Vite concurrently
npm run setup            # one-time: download_models.py — pulls Kokoro TTS + other model assets
npm run server           # run just the FastAPI backend (server/main.py)
npm run build            # tsc + vite build → dist/  (frontend only)

# Electron / packaging
npm run electron:dev     # run the app in Electron against the dev servers
npm run dmg              # build signed macOS .dmg (icon → build → bundle python → electron-builder)
npm run exe              # build Windows .exe (nsis)

# Process control CLI (scripts/persephone.mjs) — cleans FastAPI, Vite, Electron, orphaned MCP procs
npm run start            # start detached; add :electron variant to launch the shell
npm run stop / restart / status
```

Prefer `npm run dev` while developing. It kills whatever already holds ports **8000** (FastAPI) and **5173** (Vite) before launching, so stale processes are not a concern.

### Python & tests

The backend targets Python 3.11. Deps are in `server/requirements.txt` (fastapi, uvicorn, httpx, aiosqlite, sqlite-vec, torch, kokoro-onnx, openai-whisper, python-osc, croniter, …). Tests use pytest and must be run **from the repo root** (they add `server/` to the import path):

```bash
python3 -m pytest server/tests/                          # all tests
python3 -m pytest server/tests/test_hardware.py          # one file
python3 -m pytest server/tests/test_hardware.py::test_x  # one test
```

There is no linter/formatter configured for either side; `npm run build` (tsc) is the type-check gate for the frontend.

## Architecture — the parts that span multiple files

**Two processes, one origin.** In dev, Vite (5173) proxies `/api/*` to FastAPI (8000) — see `vite.config.ts`. In production, `server/main.py` mounts the built `dist/` as static files and serves the whole app itself, and Electron (`electron/main.cjs`) spawns the bundled Python interpreter on a chosen port and `loadURL`s it. So the frontend always talks to the same relative `/api` regardless of mode.

**`server/main.py` is the hub (~5000 lines).** It wires together every backend module (imported as `import db as _db`, `import research as _research`, etc.) and defines all `/api/*` routes. Chat is SSE-streamed per browser tab. Key collaborators it orchestrates:
- **Auto-router** — a two-stage heuristic + LLM-judge classifier that picks which Ollama model handles each turn.
- **`read_bridge.py`** — intercepts filesystem MCP read calls *before* they run; PDFs/DOCX/XLSX/images get routed through `idp_engine.py` (OCR/extraction) so the chat model receives text, not raw bytes. This interception is invoked from `main._run_chat_turn` and `main._stream_ollama_chat`.
- **`mcp_manager.py` / `mcp_client.py`** — spawn enabled MCP servers as subprocesses at startup, speak JSON-RPC 2.0 over stdio, and expose the union of tools to chat models via Ollama's `tools` field. Tools are namespaced `<serverId>__<toolName>`. `mcp_catalog.py` is the curated server list; `mcp_persephone_git.py` is a repo-scoped git server.
- **`delegate.py`** — the "auxiliary worker" flow: a judge model picks a category and dispatches a specialist model in the background, persisting to the `delegated_tasks` table. To avoid an import cycle, `main.py` injects DB hooks into `delegate` at startup rather than delegate importing main.
- **`research.py` + `research_db.py` + `embeddings.py`** — deep-research pipeline (decompose → web search via MCP → fetch → embed into `sqlite-vec` → synthesize cited markdown). The vector index is the persistent knowledge base.
- **`workers.py` / `workers_impl.py`** — background workers (Memory Curator, Model Warmer) that run on idle.
- **`planner.py`** — the task scheduler; `planned_tasks` fire on cron/interval/one-shot and each run spawns a fresh conversation.

**Data paths — always go through `server/paths.py`.** Never hardcode where the DB or uploads live. In dev everything sits next to the scripts (`server/persephone.db`, `server/uploads/`); in the packaged app the script dir is **read-only**, so Electron sets `PERSEPHONE_DATA_DIR` (→ `~/Library/Application Support/Persephone`) and `paths.py` routes all writes there. `~/.persephone` is a manual override. `db.py` holds the SQLite schema (app_config, conversations, messages, user_facts, delegated_tasks, planned_tasks, …) accessed via aiosqlite.

**Frontend state.** `src/store/appStore.ts` is a single Zustand store (persisted to localStorage) holding settings, conversations, and messages. `src/lib/ollama.ts` is the `streamChat` SSE client. Components are grouped by feature under `src/components/` (`chat/`, `research/`, `delegate/`, `documents/`, `reels/`, `ableton/`, `tasks/`, `workers/`, `memory/`, `settings/`, `wizard/`, plus `layout/` for the shell and `markdown/` for the editorial renderer). The `@/` import alias maps to `src/`. Canvas code (e.g. VoiceOrb) reads theme colors via `getComputedStyle` because canvas can't resolve CSS `var()`.

**Skills** are `.md` files with YAML frontmatter in `server/skills/` (or `~/.persephone/skills/`). A skill-selector picks 0–3 whose triggers match a turn and injects them into the system prompt — see `server/skills.py` and `src/lib/skills.ts`.

## Conventions & gotchas

- **Never leave `127.0.0.1`.** Ollama, MCP servers, and embeddings are all local by design; keep it that way.
- Backend modules are imported into `main.py` by short aliases and communicate through injected hooks, not cross-imports, to avoid cycles (see the delegate pattern above). Follow that when adding a module that main needs to call back into.
- The Node scripts in `scripts/` are cross-platform on purpose (`lsof` vs `netstat`, `python3` vs `python`, spawning `vite.js` directly to dodge Windows `.cmd` issues). Preserve the platform branches when editing them.
- Model IDs and tok/s recommendations live in `server/model_catalog.py`; hardware fingerprinting in `server/hardware.py`. The setup wizard (`src/components/wizard/`) drives both.
- `src/store/appStore.ts` also contains the `ORNITH_CODER_SYSTEM_PROMPT` — an in-app coding-assistant persona ("Ornith") with its own MCP-only workflow. It is app runtime content, unrelated to how *you* (Claude Code) operate on this repo.
