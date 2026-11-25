#!/usr/bin/env python3
"""Offline stdlib unit tests for android-game-porting scripts (Python 3.9+)."""
from __future__ import annotations

from array import array
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Optional
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

    def test_parse_frame_time_format_uploads_and_hitches(self) -> None:
        log = """
[dm]    10.0s [nvn] timing up to frame 300: frame time p50 33.40 / p95 37.30 / max 52.00 ms (300 frames); frame work p50 30.0 / p95 34.0 / max 50.0 ms (300 frames);
[dm]    10.1s [vk] replay per frame (last 600): 2400 draws, replay 9.30 ms (descriptor sets 5.0, pipeline keys 0.3), queue submit 0.5 ms, uniform shadows 0 KB; GPU busy 21.1 ms; texture uploads 1.2 (0.62 MB, 0.40 ms); descriptor sets reused 100, new 900, key arena bypasses 0
[dm]    10.2s [longframe] frame 301 (10.20 s) whole frame 36.1 ms, submit+present 9.0 ms, GPU 22.0 ms
[dm]    10.3s [longframe] frame 302 (10.30 s) whole frame 51.0 ms, submit+present 30.0 ms, GPU 22.0 ms
"""
        res = probe.parse_log_text(log, preset="nx2android", hitch_ms=40.0)
        self.assertEqual(res["frame_p50_ms"], 33.4)
        self.assertEqual(res["frame_p95_ms"], 37.3)
        self.assertEqual(res["upload_mb_per_frame"], 0.62)
        self.assertEqual(res["upload_ms_per_frame"], 0.4)
        self.assertEqual(res["long_frames"], 2)
        self.assertEqual(res["hitches"], 1)

    def test_windows_split_at_live_toggles(self) -> None:
        log = """
[dm] 168854.4s header line with another clock
[dm]     1.0s [longframe] frame 1 (1.0 s) whole frame 45.0 ms
[dm]     5.0s DM_CTL: env DM_X=1
[dm]     6.0s [longframe] frame 2 (6.0 s) whole frame 36.0 ms
[dm]    95.0s [nvn] timing up to frame 300: frame time p50 33.40 / p95 37.30 / max 39.00 ms (300 frames);
[dm]    95.1s DM_CTL: env DM_X=0
[dm]    96.0s [longframe] frame 3 (96.0 s) whole frame 48.0 ms
[dm]    97.0s [longframe] frame 4 (97.0 s) whole frame 52.0 ms
[dm]   185.0s [nvn] timing up to frame 600: frame time p50 33.40 / p95 41.20 / max 52.00 ms (300 frames);
"""
        rows = probe.window_table(log, hitch_ms=40.0)
        self.assertEqual([r["label"] for r in rows], ["start", "DM_X=1", "DM_X=0"])
        self.assertEqual(rows[0]["seconds"], 0.0)   # the restarted clock: from 1.0 s, not from 168854 s
        self.assertEqual([r["hitches"] for r in rows], [1, 0, 2])
        self.assertEqual(rows[1]["seconds"], 90.0)
        self.assertEqual(rows[2]["frame_p95_ms"], 41.2)
        self.assertIn("DM_X=0", probe.format_windows(rows))

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


class TestAbWindows(unittest.TestCase):
    """The hands-off A/B driver's input-loop guard (no device needed)."""
    SCRIPT = SCRIPTS / "ab_windows.sh"

    def _check(self, steps: str, forbid: Optional[str] = None) -> "subprocess.CompletedProcess[str]":
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "steps.txt"
            f.write_text(steps, encoding="utf-8")
            env = dict(os.environ)
            if forbid is not None:
                env["FORBID"] = forbid
            return subprocess.run(["bash", str(self.SCRIPT), "--check-steps", str(f)], capture_output=True, text=True, env=env)

    @unittest.skipUnless(shutil.which("bash"), "bash is required")
    def test_accepts_stick_and_attack_loop(self) -> None:
        r = self._check("LUp+RRight~2.5 2.7   # run in a circle\nY~0.2 0.5\n\n# comment\nR~1.5 1.7\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("steps ok: 3", r.stdout)

    @unittest.skipUnless(shutil.which("bash"), "bash is required")
    def test_refuses_confirm_and_menu_presses(self) -> None:
        for steps in ("A~0.2 0.5\n", "LUp+A~1 1\n", "Plus 1\n"):
            r = self._check(steps)
            self.assertEqual(r.returncode, 1, steps)
            self.assertIn("forbidden button", r.stderr)
        self.assertEqual(self._check("B~0.2 0.5\n", forbid="B").returncode, 1)
        self.assertEqual(self._check("A~0.2 0.5\n", forbid="").returncode, 1)   # empty keeps the default
        self.assertEqual(self._check("A~0.2 0.5\n", forbid="none").returncode, 0)

    @unittest.skipUnless(shutil.which("bash"), "bash is required")
    def test_refuses_bad_waits_and_empty_files(self) -> None:
        self.assertEqual(self._check("Y~0.2 soon\n").returncode, 1)
        self.assertEqual(self._check("# nothing\n").returncode, 2)


if __name__ == "__main__":
    unittest.main()
