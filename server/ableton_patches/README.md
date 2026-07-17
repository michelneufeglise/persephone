# Persephone patches for AbletonOSC

Everything in this folder is a **Persephone-authored addition** to the
[AbletonOSC](https://github.com/ideoforms/AbletonOSC) Live Remote Script.
Files here get copied on top of the cloned AbletonOSC install by
`server/ableton_bridge.py::install()`.

## What's here

- **`browser.py`** — a new `BrowserHandler` module that exposes Live's Browser
  tree over OSC. Upstream doesn't ship this; we need it to auto-load instruments
  onto tracks that Persephone's composer creates. Copied into
  `AbletonOSC/abletonosc/browser.py`.

## Patch application

`install()` walks `install_dirs_all()` (the primary + any legacy Live-version
Remote Scripts folders) and for each:

1. Copies `browser.py` into `AbletonOSC/abletonosc/`.
2. Ensures `abletonosc/__init__.py` imports `BrowserHandler`.
3. Ensures `manager.py` instantiates `abletonosc.BrowserHandler(self)` alongside
   the other handlers.

The `__init__.py` and `manager.py` patches are **surgical string appends** so
we don't invalidate the file if upstream tweaks unrelated lines.

## When to update

- If upstream AbletonOSC ships its own browser support (see
  [ideoforms/AbletonOSC](https://github.com/ideoforms/AbletonOSC/pulls)),
  delete this folder and let upstream's implementation take over.
- If upstream renames `handler.AbletonOSCHandler`, this file breaks —
  the `install()` code will still copy but Live won't load the module.
  Watch for `error importing browser` in `AbletonOSC/logs/abletonosc.log`.
