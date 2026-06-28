#!/usr/bin/env python3
"""
One-time setup: download SNAC 24kHz model to local HuggingFace cache.
After this runs, the app is fully offline (HF_HUB_OFFLINE=1).

Run: python3 server/download_models.py
"""

import os
import sys
from pathlib import Path

# Allow HF download for this script only
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["HUGGINGFACE_HUB_VERBOSITY"] = "info"

print("🌸 Persephone — downloading SNAC 24kHz model …")
print("   (This runs once; the app is fully offline afterwards)\n")

try:
    from snac import SNAC
    model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz")
    print("\n✓ SNAC model cached successfully.")
    print("  The Persephone app will now run with HF_HUB_OFFLINE=1 (no network calls).")
except Exception as exc:
    print(f"\n✗ Download failed: {exc}")
    print("  Ensure you have internet access and snac is installed:")
    print("  pip3 install snac --break-system-packages")
    sys.exit(1)
