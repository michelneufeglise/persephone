"""
Tests for `server/hardware.py` — chip fingerprint + bandwidth estimation.
Runs offline (no subprocess calls); exercises the pure parser functions.
"""

from __future__ import annotations

import pathlib
import sys

# Make the server module importable when pytest is run from the repo root.
_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import hardware  # noqa: E402


# ── Apple Silicon fingerprint ────────────────────────────────────────────────
class TestAppleSiliconFingerprint:
    def test_m1_base(self):
        assert hardware.fingerprint_chip("Apple M1", True) == ("apple_silicon_m1", "base")

    def test_m1_pro(self):
        assert hardware.fingerprint_chip("Apple M1 Pro", True) == ("apple_silicon_m1", "pro")

    def test_m1_max(self):
        assert hardware.fingerprint_chip("Apple M1 Max", True) == ("apple_silicon_m1", "max")

    def test_m1_ultra(self):
        assert hardware.fingerprint_chip("Apple M1 Ultra", True) == ("apple_silicon_m1", "ultra")

    def test_m2_pro(self):
        assert hardware.fingerprint_chip("Apple M2 Pro", True) == ("apple_silicon_m2", "pro")

    def test_m3_max(self):
        assert hardware.fingerprint_chip("Apple M3 Max", True) == ("apple_silicon_m3", "max")

    def test_m4_base(self):
        assert hardware.fingerprint_chip("Apple M4", True) == ("apple_silicon_m4", "base")

    def test_unknown_apple_silicon(self):
        # Some Apple hardware reports odd strings.
        assert hardware.fingerprint_chip("Apple Silicon", True) == ("apple_silicon_unknown", "base")

    def test_apple_silicon_takes_precedence(self):
        # An Intel-looking string on Apple Silicon shouldn't misclassify.
        assert hardware.fingerprint_chip("something weird", True) == ("apple_silicon_unknown", "base")


# ── Intel fingerprint ────────────────────────────────────────────────────────
class TestIntelFingerprint:
    def test_i7_12th_gen(self):
        fam, var = hardware.fingerprint_chip("Intel(R) Core(TM) i7-12700H", False)
        assert fam == "intel_12th"
        assert var == "n/a"

    def test_i9_13th_gen(self):
        assert hardware.fingerprint_chip("Intel(R) Core(TM) i9-13900K", False) == ("intel_13th", "n/a")

    def test_i5_8th_gen(self):
        # 4-digit model → single-digit gen prefix.
        assert hardware.fingerprint_chip("Intel(R) Core(TM) i5-8265U", False) == ("intel_8th", "n/a")

    def test_i7_9th_gen(self):
        assert hardware.fingerprint_chip("Intel(R) Core(TM) i7-9750H", False) == ("intel_9th", "n/a")

    def test_i5_10th_gen(self):
        assert hardware.fingerprint_chip("Intel(R) Core(TM) i5-10300H", False) == ("intel_10th", "n/a")

    def test_core_ultra_155h(self):
        # Meteor Lake — mapped to 14th gen for benchmark purposes.
        assert hardware.fingerprint_chip("Intel(R) Core(TM) Ultra 7 155H", False) == ("intel_14th", "n/a")

    def test_unknown_intel(self):
        assert hardware.fingerprint_chip("Intel(R) Something Weird", False) == ("intel_unknown", "n/a")


# ── AMD fingerprint ─────────────────────────────────────────────────────────
class TestAmdFingerprint:
    def test_ryzen_9_zen4(self):
        assert hardware.fingerprint_chip("AMD Ryzen 9 7940HS", False) == ("amd_zen4", "n/a")

    def test_ryzen_5_zen3(self):
        assert hardware.fingerprint_chip("AMD Ryzen 5 5600X", False) == ("amd_zen3", "n/a")

    def test_ryzen_5_zen2(self):
        assert hardware.fingerprint_chip("AMD Ryzen 5 3600", False) == ("amd_zen2", "n/a")

    def test_ryzen_9_zen5(self):
        assert hardware.fingerprint_chip("AMD Ryzen 9 9950X", False) == ("amd_zen5", "n/a")

    def test_unknown_amd(self):
        # No "ryzen" keyword → unknown.
        assert hardware.fingerprint_chip("AMD Athlon II X4 640", False) == ("amd_unknown", "n/a")

    def test_no_amd_keyword(self):
        # No AMD, no Ryzen — falls through to unknown.
        assert hardware.fingerprint_chip("Something completely different", False) == ("unknown", "n/a")


# ── Bandwidth estimation ────────────────────────────────────────────────────
class TestBandwidthEstimation:
    def test_m1_max_uses_apple_bandwidth(self):
        # M1 Max should hit 400 GB/s, NOT the discrete-GPU 350 fallback,
        # even though we set gpu_vram_gb=RAM on Apple Silicon.
        bw = hardware._estimate_bandwidth("apple_silicon_m1", "max", "Apple Silicon (unified memory)", 64)
        assert bw == 400

    def test_m4_max(self):
        bw = hardware._estimate_bandwidth("apple_silicon_m4", "max", "Apple Silicon (unified memory)", 48)
        assert bw == 546

    def test_intel_12th_no_gpu(self):
        bw = hardware._estimate_bandwidth("intel_12th", "n/a", "", 0)
        assert bw == 76

    def test_intel_12th_with_rtx_4070(self):
        # Discrete GPU takes precedence when VRAM is meaningful.
        bw = hardware._estimate_bandwidth("intel_12th", "n/a", "NVIDIA GeForce RTX 4070 Laptop", 8)
        assert bw == 504

    def test_intel_12th_with_iris(self):
        # Integrated Intel Iris should NOT trigger the GPU path.
        bw = hardware._estimate_bandwidth("intel_12th", "n/a", "Intel Iris Xe Graphics", 8)
        assert bw == 76

    def test_intel_12th_with_amd_radeon_igpu(self):
        # Integrated AMD Radeon Graphics should NOT trigger the GPU path.
        bw = hardware._estimate_bandwidth("amd_zen4", "n/a", "AMD Radeon Graphics", 8)
        assert bw == 83

    def test_unknown_discrete_gpu(self):
        # Some GPU we don't have in our table → fallback estimate.
        bw = hardware._estimate_bandwidth("intel_13th", "n/a", "NVIDIA GeForce RTX Super Special 5555", 12)
        assert bw == 350

    def test_dead_conservative_fallback(self):
        # Truly unknown chip → 40 GB/s.
        bw = hardware._estimate_bandwidth("unknown", "n/a", "", 0)
        assert bw == 40

    def test_apple_silicon_ignores_gpu_vram_number(self):
        # Even if gpu_vram_gb is huge (bug from the wizard), Apple path wins.
        bw = hardware._estimate_bandwidth("apple_silicon_m2", "max", "Apple Silicon (unified memory)", 128)
        assert bw == 400  # not the discrete-GPU 350 fallback
