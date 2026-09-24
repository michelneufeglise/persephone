"""
Pytest configuration for Persephone test suite.

CRITICAL: This file must run FIRST (before test modules import server code).
It sets up test isolation by routing all writes to a temporary directory
instead of the user's real data directory.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────
# ISOLATION SETUP — runs BEFORE any test module imports server code
# ──────────────────────────────────────────────────────────────────────────

# Create a fresh temp directory for all test writes (documents, db, etc.)
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="persephone-test-")

# Set BEFORE importing server modules so paths.py resolves to temp dir
os.environ["PERSEPHONE_DATA_DIR"] = _TEST_DATA_DIR

# Ensure server/ is on sys.path so imports work
_SERVER_DIR = Path(__file__).parent.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

# Import server modules AFTER env is set, so STORAGE_DIR and DB_PATH resolve to temp
import paths as _paths
import idp_engine as _idp_engine

# Verify paths are isolated
_RESOLVED_STORAGE = str(_idp_engine.STORAGE_DIR)
_RESOLVED_DB = str(_paths.db_path())

if not _RESOLVED_STORAGE.startswith(_TEST_DATA_DIR):
    raise RuntimeError(
        f"ISOLATION FAILED: idp_engine.STORAGE_DIR is {_RESOLVED_STORAGE}, "
        f"expected under {_TEST_DATA_DIR}. Tests would write to real user data!"
    )

if not _RESOLVED_DB.startswith(_TEST_DATA_DIR):
    raise RuntimeError(
        f"ISOLATION FAILED: db_path() is {_RESOLVED_DB}, "
        f"expected under {_TEST_DATA_DIR}. Tests would write to real user data!"
    )

# ──────────────────────────────────────────────────────────────────────────
# SNAPSHOT REAL DATA before tests run (for post-test validation)
# ──────────────────────────────────────────────────────────────────────────

import pytest

# Paths that must NOT change during tests
_REAL_REGISTRY = Path(__file__).parent.parent / "uploads" / "_registry.json"
_REAL_DB = Path(__file__).parent.parent / "persephone.db"

# Record stat (mtime, size) of real files before any test runs
_REAL_FILES_BEFORE = {}
for path in [_REAL_REGISTRY, _REAL_DB]:
    if path.exists():
        stat = path.stat()
        _REAL_FILES_BEFORE[str(path)] = {
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        }


@pytest.fixture(scope="session", autouse=True)
def isolation_guard():
    """
    Session-scoped fixture that:
    1. Validates paths are isolated at startup
    2. Cleans up temp dir after all tests
    3. Verifies real user data was not modified
    """
    # Validation already happened above (module load), but recheck for safety
    assert _RESOLVED_STORAGE.startswith(_TEST_DATA_DIR), \
        f"Storage not isolated: {_RESOLVED_STORAGE}"
    assert _RESOLVED_DB.startswith(_TEST_DATA_DIR), \
        f"DB not isolated: {_RESOLVED_DB}"

    # Run all tests
    yield

    # After all tests: verify real data wasn't modified
    for path_str, before in _REAL_FILES_BEFORE.items():
        path = Path(path_str)
        if path.exists():
            stat = path.stat()
            if stat.st_mtime != before["mtime"] or stat.st_size != before["size"]:
                # Print what changed for debugging
                print(f"\n[ISOLATION BREACH] Real file was modified: {path}")
                print(f"  Before: mtime={before['mtime']}, size={before['size']}")
                print(f"  After:  mtime={stat.st_mtime}, size={stat.st_size}")
                raise AssertionError(
                    f"Tests modified real user data at {path}! "
                    f"This should never happen with proper isolation."
                )

    # Clean up temp directory
    if _TEST_DATA_DIR and Path(_TEST_DATA_DIR).exists():
        shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
