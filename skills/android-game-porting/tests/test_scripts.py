#!/usr/bin/env python3
"""Offline stdlib unit tests for android-game-porting scripts (Python 3.9+)."""
from __future__ import annotations

from array import array
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
sys.path.insert(0, str(SCRIPTS))

import budget  # noqa: E402
import f32_audit  # noqa: E402
import probe  # noqa: E402


class TestBudget(unittest.TestCase):
    def test_example_nier_rp6(self) -> None:
        cfg = json.loads((EXAMPLES / "device-rp6-nier.json").read_text(encoding="utf-8"))
        res = budget.analyze(cfg)
        self.assertTrue(res["clean_pacing_divisor"])
        self.assertEqual(res["budget_ms"], 16.67)
        self.assertEqual(res["bottleneck"], "gpu")
        self.assertEqual(res["memory"]["pool_chunks"], 6)
        self.assertTrue(res["memory"]["cross_chunk_risk"])
        self.assertFalse(res["shading_floor"]["at_shading_floor"])
        self.assertFalse(res["memory_bandwidth"]["write_combined_trap"])

    def test_flags_judder_wc_trap_and_shading_floor(self) -> None:
        cfg = {
            "display_hz": 60,
            "target_fps": 40,
            "engine_cap_fps": 30,
            "measured": {
                "game_cpu_ms": 6.0,
                "replay_ms": 14.0,
                "gpu_ms": 18.0,
                "gpu_noloadstore_ms": 17.4,
                "sustained_gpu_ms": 26.0,
                "hwbuf_read_mbs": 1800,
                "malloc_read_mbs": 18000,
            },
            "memory": {
                "idle_available_mib": 4000,
                "lmk_headroom_mib": 1536,
                "peak_rss_mib": 3200,
                "largest_pool_mib": 600,
                "zc_chunk_mib": 256,
            },
        }
        res = budget.analyze(cfg)
        self.assertFalse(res["clean_pacing_divisor"])
        self.assertTrue(res["shading_floor"]["at_shading_floor"])
        self.assertTrue(res["memory_bandwidth"]["write_combined_trap"])
        self.assertLess(res["memory"]["lmk_margin_mib"], 0)
        joined = "\n".join(res["warnings"])
        self.assertIn("vsync judder", joined)
        self.assertIn("engine_cap_fps", joined)
        self.assertIn("write-combined", joined)
        self.assertIn("shading floor", joined)
        self.assertIn("LMK", joined)


class TestF32Audit(unittest.TestCase):
    def _write_f32(self, path: Path, vals: list) -> None:
        arr = array("f", vals)
        if sys.byteorder != "little":
            arr.byteswap()
        path.write_bytes(arr.tobytes())

    def test_rgb32f_detects_nan_and_inf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pass_30.ppm.f32"
            self._write_f32(
                p,
                [
                    0.5, 0.25, 1.0,
                    float("nan"), 0.1, 0.2,
                    0.0, float("inf"), 0.0,
                    -0.5, float("-inf"), 0.3,
                ],
            )
            res = f32_audit.audit_file(p, channels=3, width=2, height=2)
            self.assertEqual(res["nonfinite_pixels"], 3)
            self.assertEqual(res["nan_channels"], 1)
            self.assertEqual(res["pos_inf_channels"], 1)
            self.assertEqual(res["neg_inf_channels"], 1)
            self.assertEqual(res["negative_finite_channels"], 1)
            self.assertEqual(res["examples"][0]["x"], 1)
            self.assertEqual(res["examples"][0]["y"], 0)

    def test_scan_and_compare_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            before, after = root / "before", root / "after"
            before.mkdir()
            after.mkdir()
            self._write_f32(before / "pass_10.ppm.f32", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
            self._write_f32(before / "pass_30.ppm.f32", [0.1, float("nan"), 0.3, 0.4, 0.5, 0.6])
            self._write_f32(after / "pass_10.ppm.f32", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
            self._write_f32(after / "pass_30.ppm.f32", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])

            scan = f32_audit.scan_dir(before)
            self.assertEqual(scan["earliest_bad_file"], "pass_30.ppm.f32")
            self.assertEqual(scan["total_nonfinite_pixels"], 1)

            cmp = f32_audit.compare_dirs(before, after)
            self.assertEqual(cmp["before_total_nonfinite"], 1)
            self.assertEqual(cmp["after_total_nonfinite"], 0)
            self.assertTrue(cmp["clean_after"])


class TestProbe(unittest.TestCase):
    SAMPLE_LOG = """
hardware buffers: the CPU reads one at 14200 MB/s, malloc memory at 17800 MB/s
[nvn] timing up to frame 300: frame p50 18.20 / p95 21.10 / max 28.50 ms (300 frames);
[nvn] timing up to frame 600: frame p50 16.65 / p95 17.90 / max 20.10 ms (300 frames);
[nvn] timing up to frame 900: frame p50 16.70 / p95 18.10 / max 21.00 ms (300 frames);
replay per frame (last 600): 412 draws, replay 4.30 ms (descriptor sets 0.80, pipeline keys 0.20), queue submit 0.40 ms, uniform shadows 120 KB; GPU busy 11.40 ms; texture uploads 0.5 (0.10 MB, 0.05 ms); descriptor sets reused 8600, new 1400, key arena bypasses 0
[perf] adaptive frame rate: 60 fps, frame work p50 12.1 / p90 14.5 / p95 15.2 ms (last 12.0 ms); late on screen 1 of 64, step-downs held back 0
[perf] display timing: 1200 frames, 2 resyncs, 3 shown a vsync or more after their time (cumulative)
[RED] example warning line
VUID-vkCmdDrawIndexed-format-07753 something
custom_metric=42.5
"""

    def test_parse_nx2android_log(self) -> None:
        res = probe.parse_log_text(
            self.SAMPLE_LOG,
            preset="nx2android",
            skip_first=1,
            extra_metrics={"custom": {"pattern": r"custom_metric=([0-9.]+)", "agg": "last"}},
        )
        self.assertEqual(res["windows"], 2)
        self.assertAlmostEqual(res["frame_p50_ms"], 16.67, places=2)
        self.assertEqual(res["draws"], 412.0)
        self.assertEqual(res["replay_p50_ms"], 4.3)
        self.assertEqual(res["gpu_p50_ms"], 11.4)
        self.assertEqual(res["desc_reuse_pct"], 86.0)
        self.assertEqual(res["late_of_64"], 1.0)
        self.assertEqual(res["display_resyncs"], 2)
        self.assertEqual(res["hwbuf_read_mbs"], 14200.0)
        self.assertEqual(res["red_lines"], 1)
        self.assertEqual(res["vuid_count"], 1)
        self.assertEqual(res["custom"], 42.5)

    def test_parse_gfxinfo_preset(self) -> None:
        gfx = """
Total frames rendered: 600
Janky frames: 12 (2.00%)
50th percentile: 11ms
90th percentile: 15ms
95th percentile: 16ms
99th percentile: 22ms
"""
        res = probe.parse_log_text(gfx, preset="gfxinfo")
        self.assertEqual(res["total_frames"], 600)
        self.assertEqual(res["janky_frames"], 12)
        self.assertEqual(res["janky_pct"], 2.0)
        self.assertEqual(res["frame_p50_ms"], 11.0)
        self.assertEqual(res["frame_p95_ms"], 16.0)
        self.assertEqual(res["frame_p99_ms"], 22.0)

    def test_compare_triplet_verdicts(self) -> None:
        rows = [
            {"name": "ctl-a", "ok": True, "frame_p50_ms": 20.0, "frame_p95_ms": 22.0, "red_lines": 0, "vuid_count": 0},
            {"name": "cand-win", "ok": True, "frame_p50_ms": 16.7, "frame_p95_ms": 18.2, "red_lines": 0, "vuid_count": 0},
            {"name": "cand-noise", "ok": True, "frame_p50_ms": 19.7, "frame_p95_ms": 22.0, "red_lines": 0, "vuid_count": 0},
            {"name": "cand-reg", "ok": True, "frame_p50_ms": 16.5, "frame_p95_ms": 28.0, "red_lines": 0, "vuid_count": 0},
            {"name": "cand-red", "ok": True, "frame_p50_ms": 16.0, "frame_p95_ms": 18.0, "red_lines": 2, "vuid_count": 0},
            {"name": "ctl-b", "ok": True, "frame_p50_ms": 20.4, "frame_p95_ms": 22.2, "red_lines": 0, "vuid_count": 0},
            {"name": "ctl-drift", "ok": True, "frame_p50_ms": 24.0, "frame_p95_ms": 26.0, "red_lines": 0, "vuid_count": 0},
            {"name": "cand-flag", "ok": False, "flags": ["THERMAL_DRIFT"], "frame_p50_ms": 16.0},
        ]
        self.assertEqual(probe.compare_triplet(rows, "ctl-a", "cand-win", "ctl-b")["verdict"], "ACCEPT")
        self.assertEqual(probe.compare_triplet(rows, "ctl-a", "cand-noise", "ctl-b")["verdict"], "REJECT_NO_GAIN")
        self.assertEqual(probe.compare_triplet(rows, "ctl-a", "cand-reg", "ctl-b")["verdict"], "REJECT_REGRESSION")
        self.assertEqual(probe.compare_triplet(rows, "ctl-a", "cand-red", "ctl-b")["verdict"], "REJECT_REGRESSION")
        self.assertEqual(probe.compare_triplet(rows, "ctl-a", "cand-win", "ctl-drift")["verdict"], "VOID_CONTROL_DRIFT")
        self.assertEqual(probe.compare_triplet(rows, "ctl-a", "cand-flag", "ctl-b")["verdict"], "VOID_FLAGS")

    def test_format_ledger(self) -> None:
        txt = probe.format_ledger([{"name": "ctl-a", "frame_p50_ms": 16.7, "flags": []}])
        self.assertIn("ctl-a", txt)
        self.assertIn("16.7", txt)


if __name__ == "__main__":
    unittest.main()
