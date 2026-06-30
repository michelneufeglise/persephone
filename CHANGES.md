# Changes

## 2026-06-30 — Fix CPU thread count hardcoded for Apple Silicon
Found while investigating slow token generation on Windows: every Ollama
request (`num_thread`) defaulted to a flat constant tuned for M1 Pro/Max
(8 on the frontend, 10 on the backend), regardless of the host's actual CPU.
On a 12-core Windows machine this left a third of the CPU idle during
generation; on smaller CPUs it could over-subscribe.
- `server/hardware.py` — added `recommended_num_thread()`, derived from the already-detected physical core count (`lru_cache`d, falls back to 10 if detection fails).
- `server/main.py`, `server/idp_engine.py`, `server/tts_engine.py` — replaced the hardcoded `num_thread: 10` in every Ollama request with `hardware.recommended_num_thread()`.
- `src/store/appStore.ts` — `model.numThread` default changed from `8` to `0` ("auto"); added a persist `migrate` step so existing users with the old hardcoded `8` get reset to auto on next load (explicit non-default choices are left alone).
- `src/lib/ollama.ts` — omits `num_thread` from the chat request when set to "auto" (`<= 0`), letting the backend's hardware-aware default apply.
- `src/components/settings/sections/ModelSection.tsx` — "CPU Threads" field now supports 0 = auto and shows the detected core count as a hint.

Note: a quick on-machine benchmark (12th-gen Intel hybrid P+E core laptop, no discrete GPU) showed thread scaling is noisy on hybrid CPUs — more threads isn't always faster. The auto value is a solid general default, but the new Settings control lets you hand-tune it if it isn't optimal for your specific CPU.

## 2026-06-30 — Post-setup model role reassignment
- `server/main.py` — added `GET/POST /api/models/roles` to read and update the per-function model assignments (main chat, auto-router judge, vision, code, OCR, documents, handwriting, tables) that the setup wizard originally writes to `app_config`, without re-running the wizard. `judge_model` updates also mirror to `memory_model` as the wizard does.
- `src/components/settings/sections/ModelRolesSection.tsx` (new) — Settings panel listing each model role with a dropdown of currently-installed Ollama models, plus a Refresh button that re-scans `/api/models` so newly-pulled models become selectable immediately. Changing "Main Chat" also updates the live chat header via the Zustand store.
- `src/components/settings/SettingsView.tsx` — added a "Models" tab (Boxes icon) wired to the new section; relabeled the existing parameters tab "Generation" to disambiguate.

## 2026-06-30 — Add Windows support alongside macOS
- `electron/main.cjs` — resolve the bundled/system Python binary and shared-lib env vars per platform (`python/python.exe` + no DYLD on Windows vs. `python/bin/python3` + `DYLD_LIBRARY_PATH` on macOS, `LD_LIBRARY_PATH` on Linux); use `python`/`python3` correctly in dev.
- `scripts/bundle-python.mjs` — generalized the portable-Python bundler to target Windows (`x86_64-pc-windows-msvc`) in addition to macOS, with platform-specific output dirs, binary layout, and a host-OS guard (pip must run the target interpreter natively). Added a JS-based `dirSize` fallback since `du` isn't available on Windows.
- `scripts/dev.mjs` — fixed a real Windows bug: spawning `npx.cmd` directly threw `EINVAL` (Node can't launch `.cmd` shims without `shell: true`). Now runs Vite's CLI script directly via `node node_modules/vite/bin/vite.js` when available, falling back to the old `npx.cmd`/shell approach otherwise.
- `scripts/run-python.mjs` (new) — tiny cross-platform shim so `npm run server`/`npm run setup` use `python` on Windows and `python3` elsewhere.
- `server/index.js` — fixed Python path resolution (`which` → `where` on Windows) for this currently-unreferenced legacy Express server, for consistency.
- `package.json` — added a `win` electron-builder target (NSIS installer), split `extraResources` python mapping per platform (`build-resources/python-${arch}` for mac, `build-resources/python-win-${arch}` for Windows), and added an `exe` script mirroring `dmg`.
- `README.md` — documented the Windows build (`npm run exe`) and dev-from-source path; moved Windows off the "wishlist" list.

No macOS behavior changed — all platform branches default to the previous macOS paths/commands.
