#!/usr/bin/env python3
"""
One-time setup: download model assets to local HuggingFace cache.
After this runs, the app is fully offline (HF_HUB_OFFLINE=1).

Run: python3 server/download_models.py
"""

import os
import sys
from pathlib import Path

# Allow HF download for this script only
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["HUGGINGFACE_HUB_VERBOSITY"] = "info"

print("🌸 Persephone — downloading model assets …")
print("   (This runs once; the app is fully offline afterwards)\n")

# Track success
failed = False

# Download SNAC model
print("📦 Downloading SNAC 24kHz model…")
try:
    from snac import SNAC
    model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz")
    print("✓ SNAC model cached successfully.\n")
except Exception as exc:
    print(f"✗ SNAC download failed: {exc}")
    print("  Ensure you have internet access and snac is installed:")
    print("  pip3 install snac --break-system-packages\n")
    failed = True

# Download Laya English model (failure-tolerant; does not set failure flag)
print("📦 Downloading Laya English decision model…")
try:
    import laya_decider as _laya
    if _laya.ensure_downloaded():
        print("✓ Laya English model cached successfully.\n")
    else:
        print("⚠ Laya download failed (see log above).\n")
        print("  (Laya is optional; the app will work without it)\n")
        # Note: Laya is optional, so we don't set failed=True here
except Exception as exc:
    print(f"⚠ Laya setup skipped: {exc}")
    print("  (Laya is optional; the app will work without it)\n")

if failed:
    print("⚠ Some models failed to download; re-run this script after fixing the errors.")
    sys.exit(1)
else:
    print("✓ All models cached successfully.")
    print("  The Persephone app will now run with HF_HUB_OFFLINE=1 (no network calls).")
