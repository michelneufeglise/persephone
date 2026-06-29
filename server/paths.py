"""
Resolve writable data paths for Persephone.

In development we keep everything next to the script (server/persephone.db,
server/uploads/, …) so it's easy to inspect.

In the Electron .dmg build the script lives inside Persephone.app/Contents/
Resources/server, which is **read-only** — sqlite writes there fail with a
cryptic exit-1 on first launch. Electron sets PERSEPHONE_DATA_DIR to
`~/Library/Application Support/Persephone` (via `app.getPath('userData')`)
and we route every writable artefact there.

Resolution order:
  1. `PERSEPHONE_DATA_DIR` env var (set by Electron in production)
  2. `~/.persephone` if it exists (manual override)
  3. The directory of the calling script (dev fallback)
"""
from __future__ import annotations

import os
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent


def data_dir() -> Path:
    env = os.environ.get("PERSEPHONE_DATA_DIR")
    if env:
        p = Path(env).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p

    home_override = Path.home() / ".persephone"
    if home_override.exists():
        return home_override

    return _SCRIPT_DIR


def db_path() -> Path:
    return data_dir() / "persephone.db"


def uploads_dir() -> Path:
    p = data_dir() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p
