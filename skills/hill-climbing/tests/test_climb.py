#!/usr/bin/env python3
"""Offline stdlib unit tests for hill-climbing meta-skill scripts (Python 3.9+)."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import climb  # noqa: E402


class TestWorkspaceAndVerifierLock(unittest.TestCase):
    def test_init_creates_expected_files_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td) / "climb-work"
            res = climb.init_workspace(wd)
            for fname in ("OBJECTIVE.md", "THEORY.md", "PLAN.md", "DEAD_ENDS.md", "ledger.jsonl", "verify.sh"):
                self.assertTrue((wd / fname).is_file(), fname)
                self.assertIn(fname, res["created"])
            self.assertTrue((wd / "configs").is_dir())
            self.assertTrue(os.access(wd / "verify.sh", os.X_OK))

            # Second call skips existing files without clobbering edits.
            (wd / "PLAN.md").write_text("custom edits\n", encoding="utf-8")
            res2 = climb.init_workspace(wd)
            self.assertEqual(res2["created"], [])
            self.assertEqual((wd / "PLAN.md").read_text(encoding="utf-8"), "custom edits\n")

    def test_verifier_lock_detects_tampering_and_flags_records(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            climb.init_workspace(wd)
            fixture = wd / "prompts.json"
            fixture.write_text('["hello"]\n', encoding="utf-8")

            climb.lock_verifier(wd, [wd / "verify.sh", fixture])
            ok_chk = climb.check_verifier(wd)
            self.assertTrue(ok_chk["ok"])
            self.assertEqual(ok_chk["checked"], 2)

            # Tamper with verifier script -> check fails and record auto-flags.
            (wd / "verify.sh").write_text("#!/bin/sh\necho cheated\n", encoding="utf-8")
            bad_chk = climb.check_verifier(wd)
            self.assertFalse(bad_chk["ok"])
            self.assertEqual(len(bad_chk["mismatches"]), 1)

            row = climb.build_record(
                name="cand-cheat",
                role="candidate",
                metric="tps",
                value=999.0,
                gate_pass=True,
                work_dir=wd,
            )
            self.assertIn("VERIFIER_TAMPERED", row["flags"])
            self.assertFalse(row["ok"])


class TestCompareTriplet(unittest.TestCase):
    def _sample_rows(self) -> list:
        return [
            climb.build_record("ctl-a", "control", "tps", samples=[74.0, 75.0, 76.0], guardrails={"prefill": 740.0, "p95_ms": 20.0, "errors": 0.0}),
            climb.build_record("cand-win", "candidate", "tps", samples=[80.5, 81.0, 81.5], guardrails={"prefill": 735.0, "p95_ms": 20.2, "errors": 0.0}),
            climb.build_record("cand-noise", "candidate", "tps", samples=[76.0, 76.5, 76.2], guardrails={"prefill": 740.0, "p95_ms": 20.0, "errors": 0.0}),
            climb.build_record("cand-reg", "candidate", "tps", samples=[85.0, 86.0, 85.5], guardrails={"prefill": 650.0, "p95_ms": 20.0, "errors": 0.0}),
            climb.build_record("cand-err", "candidate", "tps", samples=[85.0, 86.0, 85.5], guardrails={"prefill": 740.0, "p95_ms": 20.0, "errors": 1.0}),
            climb.build_record("cand-gate", "candidate", "tps", value=95.0, gate_pass=False, guardrails={"prefill": 740.0, "p95_ms": 20.0, "errors": 0.0}),
            climb.build_record("cand-flag", "candidate", "tps", value=95.0, flags=["THERMAL_DRIFT"]),
            climb.build_record("cand-tamper", "candidate", "tps", value=95.0, flags=["VERIFIER_TAMPERED"]),
            climb.build_record("ctl-b", "control", "tps", samples=[74.5, 75.2, 75.5], guardrails={"prefill": 738.0, "p95_ms": 20.1, "errors": 0.0}),
            climb.build_record("ctl-drift", "control", "tps", samples=[62.0, 62.5, 63.0], guardrails={"prefill": 738.0, "p95_ms": 20.1, "errors": 0.0}),
        ]

    def test_higher_is_better_verdicts(self) -> None:
        rows = self._sample_rows()
        guards = [("prefill", "higher", 3.0), ("p95_ms", "lower", 3.0), ("errors", "lower", 0.0)]

        win = climb.compare_triplet(rows, "ctl-a", "cand-win", "ctl-b", "tps", "higher", 3.0, 10.0, guards)
        self.assertEqual(win["verdict"], "ACCEPT")
        self.assertGreater(win["min_neighbor_gain_pct"], 7.0)

        self.assertEqual(
            climb.compare_triplet(rows, "ctl-a", "cand-noise", "ctl-b", "tps", "higher", 3.0, 10.0, guards)["verdict"],
            "REJECT_NO_GAIN",
        )
        self.assertEqual(
            climb.compare_triplet(rows, "ctl-a", "cand-reg", "ctl-b", "tps", "higher", 3.0, 10.0, guards)["verdict"],
            "REJECT_REGRESSION",
        )
        self.assertEqual(
            climb.compare_triplet(rows, "ctl-a", "cand-err", "ctl-b", "tps", "higher", 3.0, 10.0, guards)["verdict"],
            "REJECT_REGRESSION",
        )
        self.assertEqual(
            climb.compare_triplet(rows, "ctl-a", "cand-gate", "ctl-b", "tps", "higher", 3.0, 10.0, guards)["verdict"],
            "REJECT_GATE",
        )
        self.assertEqual(
            climb.compare_triplet(rows, "ctl-a", "cand-flag", "ctl-b", "tps", "higher", 3.0, 10.0, guards)["verdict"],
            "VOID_FLAGS",
        )
        self.assertEqual(
            climb.compare_triplet(rows, "ctl-a", "cand-tamper", "ctl-b", "tps", "higher", 3.0, 10.0, guards)["verdict"],
            "VOID_VERIFIER_TAMPERED",
        )
        self.assertEqual(
            climb.compare_triplet(rows, "ctl-a", "cand-win", "ctl-drift", "tps", "higher", 3.0, 10.0, guards)["verdict"],
            "VOID_CONTROL_DRIFT",
        )

    def test_lower_is_better_verdict(self) -> None:
        rows = [
            climb.build_record("a1", "control", "frame_ms", value=24.0),
            climb.build_record("b1", "candidate", "frame_ms", value=16.2),
            climb.build_record("a2", "control", "frame_ms", value=23.6),
        ]
        res = climb.compare_triplet(rows, "a1", "b1", "a2", "frame_ms", "lower", min_gain_pct=3.0)
        self.assertEqual(res["verdict"], "ACCEPT")
        self.assertGreater(res["min_neighbor_gain_pct"], 30.0)


class TestCompareAbabAndPlateau(unittest.TestCase):
    def test_abab_accept_and_reject_split_pairs(self) -> None:
        rows = [
            climb.build_record("a1", "control", "hitches", value=75.0),
            climb.build_record("b1", "candidate", "hitches", value=1.0),
            climb.build_record("a2", "control", "hitches", value=70.0),
            climb.build_record("b2", "candidate", "hitches", value=1.0),
            climb.build_record("b2-bad", "candidate", "hitches", value=72.0),
        ]
        win = climb.compare_abab(rows, ["a1", "b1", "a2", "b2"], "hitches", "lower", min_gain_pct=5.0)
        self.assertEqual(win["verdict"], "ACCEPT")
        self.assertEqual(win["pairs"], 2)

        split = climb.compare_abab(rows, ["a1", "b1", "a2", "b2-bad"], "hitches", "lower", min_gain_pct=5.0)
        self.assertEqual(split["verdict"], "REJECT_NO_GAIN")

    def test_plateau_detection_by_verdict_and_metric(self) -> None:
        rows_v = [
            climb.build_record("exp-0", "baseline", "tps", value=70.0, verdict="baseline"),
            climb.build_record("exp-1", "candidate", "tps", value=75.0, verdict="keep"),
            climb.build_record("exp-2", "candidate", "tps", value=75.5, verdict="revert"),
            climb.build_record("exp-3", "candidate", "tps", value=74.8, verdict="revert"),
            climb.build_record("exp-4", "candidate", "tps", value=76.0, verdict="revert"),
        ]
        res = climb.detect_plateau(rows_v, consecutive=3)
        self.assertTrue(res["plateau"])
        self.assertEqual(res["consecutive_failures"], 3)
        self.assertEqual(res["mode"], "verdict")

        rows_m = [
            climb.build_record("base", "baseline", "tps", value=70.0),
            climb.build_record("c1", "candidate", "tps", value=76.0),
            climb.build_record("c2", "candidate", "tps", value=76.5),
            climb.build_record("c3", "candidate", "tps", value=77.0),
        ]
        res2 = climb.detect_plateau(rows_m, consecutive=3, metric="tps", direction="higher", min_gain_pct=3.0)
        self.assertFalse(res2["plateau"])
        self.assertEqual(res2["consecutive_failures"], 2)

    def test_scoreboard_and_cli_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger.jsonl"
            for argv in (
                ["record", str(ledger), "--name", "base", "--role", "baseline", "--metric", "tps", "--samples", "70,71,72", "--verdict", "baseline"],
                ["record", str(ledger), "--name", "c1", "--role", "candidate", "--metric", "tps", "--samples", "80,81,82", "--verdict", "keep", "--lever", "metal4"],
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(climb.main(argv), 0)
            rows = climb.load_ledger(ledger)
            board = climb.format_scoreboard(rows, metric="tps", direction="higher", ceiling=100.0)
            self.assertIn("81.000", board)
            self.assertIn("81.0%", board)
            self.assertIn("metal4", board)


class TestQueueScript(unittest.TestCase):
    SCRIPT = SCRIPTS / "queue.sh"

    @unittest.skipUnless(shutil.which("bash"), "bash is required")
    def test_queue_runs_configs_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "configs").mkdir()
            (root / "configs" / "ctl-a.json").write_text('{"x":1}\n', encoding="utf-8")
            (root / "configs" / "cand-1.json").write_text('{"x":2}\n', encoding="utf-8")
            verify = root / "verify.sh"
            verify.write_text("#!/usr/bin/env bash\necho \"ran:$1\"\n", encoding="utf-8")
            verify.chmod(0o755)

            env = dict(os.environ)
            env["VERIFY_CMD"] = str(verify)
            env["QUEUE_LOCK"] = str(root / "test.lock.d")
            proc = subprocess.run(
                ["bash", str(self.SCRIPT), "ctl-a", "cand-1"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("QUEUE_DONE ctl-a cand-1", proc.stdout)
            self.assertIn("ran:configs/ctl-a.json", (root / "run-ctl-a.log").read_text(encoding="utf-8"))
            self.assertIn("ran:configs/cand-1.json", (root / "run-cand-1.log").read_text(encoding="utf-8"))
            self.assertFalse((root / "test.lock.d").exists())


if __name__ == "__main__":
    unittest.main()
