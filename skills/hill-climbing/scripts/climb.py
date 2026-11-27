#!/usr/bin/env python3
"""Domain-agnostic Hill-Climbing Engineering harness (Python 3.9+, stdlib only).

Subcommands:
  init            Scaffold a hill-climbing workspace on disk.
  lock-verifier   Record SHA-256 hashes of read-only verifier scripts/fixtures.
  check-verifier  Verify locked verifier files have not been modified.
  record          Append a measured run to ledger.jsonl.
  compare         Judge a bracketed triplet (ctl-a -> candidate -> ctl-b).
  compare-abab    Judge interleaved pairs (A1, B1, A2, B2, ...).
  plateau         Detect when N consecutive candidates failed to beat noise.
  show            Render a scoreboard table (with optional % of ceiling).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERIFIER_LOCK_FILE = ".verifier.sha256"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "examples"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def current_git_sha(cwd: Optional[Path] = None) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except Exception:
        return None


def _rel_or_abs(path: Path, base: Path) -> str:
    rp = path.resolve()
    rb = base.resolve()
    try:
        return str(rp.relative_to(rb))
    except ValueError:
        return str(rp)


def init_workspace(work_dir: Path, force: bool = False) -> Dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "configs").mkdir(exist_ok=True)

    mapping = [
        ("objective-template.md", "OBJECTIVE.md", False),
        ("plan-template.md", "PLAN.md", False),
        ("dead-ends-template.md", "DEAD_ENDS.md", False),
        ("verify-template.sh", "verify.sh", True),
    ]
    created: List[str] = []
    skipped: List[str] = []

    for src_name, dst_name, executable in mapping:
        dst = work_dir / dst_name
        if dst.exists() and not force:
            skipped.append(dst_name)
            continue
        src = TEMPLATES_DIR / src_name
        if src.is_file():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            dst.write_text(f"# {dst_name}\n", encoding="utf-8")
        if executable:
            dst.chmod(dst.stat().st_mode | 0o111)
        created.append(dst_name)

    theory = work_dir / "THEORY.md"
    if not theory.exists() or force:
        theory.write_text(
            "# Physical & Theoretical Ceilings\n\n"
            "## Measured Machine / System Inventory\n\n"
            "- Hardware / runtime:\n"
            "- Measured memory bandwidth / compute / I/O floor:\n"
            "- Fixed environment settings (power, clocks, load):\n\n"
            "## Ceiling Calculation\n\n"
            "- Primary metric:\n"
            "- Formula & measured inputs:\n"
            "- Theoretical ceiling:\n"
            "- Practical ceiling:\n"
            "- Baseline as % of ceiling:\n",
            encoding="utf-8",
        )
        created.append("THEORY.md")
    else:
        skipped.append("THEORY.md")

    ledger = work_dir / "ledger.jsonl"
    if not ledger.exists():
        ledger.write_text("", encoding="utf-8")
        created.append("ledger.jsonl")
    else:
        skipped.append("ledger.jsonl")

    return {"work_dir": str(work_dir), "created": created, "skipped": skipped}


def lock_verifier(work_dir: Path, files: Sequence[Path]) -> Dict[str, Any]:
    if not files:
        raise ValueError("at least one verifier file is required")
    hashes: Dict[str, str] = {}
    for f in files:
        p = f if f.is_absolute() else (Path.cwd() / f)
        if not p.is_file():
            raise FileNotFoundError(f"verifier file not found: {f}")
        key = _rel_or_abs(p, work_dir)
        hashes[key] = sha256_file(p)
    payload = {"locked_at": utc_now(), "files": hashes}
    lock_path = work_dir / VERIFIER_LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def check_verifier(work_dir: Path) -> Dict[str, Any]:
    lock_path = work_dir / VERIFIER_LOCK_FILE
    if not lock_path.is_file():
        return {"ok": False, "error": "MISSING_LOCK", "mismatches": [], "missing": [str(lock_path)]}
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    files: Dict[str, str] = data.get("files", {})
    mismatches: List[Dict[str, str]] = []
    missing: List[str] = []
    for key, expected in files.items():
        p = Path(key)
        if not p.is_absolute():
            p = work_dir / p
        if not p.is_file():
            missing.append(key)
            continue
        actual = sha256_file(p)
        if actual != expected:
            mismatches.append({"file": key, "expected": expected, "actual": actual})
    ok = (not mismatches) and (not missing) and bool(files)
    return {
        "ok": ok,
        "checked": len(files),
        "locked_at": data.get("locked_at"),
        "mismatches": mismatches,
        "missing": missing,
    }


def load_ledger(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {lineno} of {path}: {exc}") from exc
    return rows


def append_ledger(path: Path, row: Dict[str, Any]) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def parse_samples(text: str) -> List[float]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        raise ValueError("samples list is empty")
    return [float(p) for p in parts]


def parse_kv_floats(items: Sequence[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"expected KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = float(v.strip())
    return out


def parse_guardrail_specs(specs: Sequence[str]) -> List[Tuple[str, str, float]]:
    parsed: List[Tuple[str, str, float]] = []
    for raw in specs:
        parts = raw.split(":")
        if len(parts) != 3:
            raise ValueError(f"guardrail must be METRIC:higher|lower:MAX_REG_PCT, got {raw!r}")
        name, direction, pct = parts[0].strip(), parts[1].strip().lower(), float(parts[2])
        if direction not in ("higher", "lower"):
            raise ValueError(f"guardrail direction must be higher or lower, got {direction!r}")
        parsed.append((name, direction, pct))
    return parsed


def build_record(
    name: str,
    role: str,
    metric: str,
    value: Optional[float] = None,
    samples: Optional[Sequence[float]] = None,
    gate_pass: bool = True,
    guardrails: Optional[Dict[str, float]] = None,
    flags: Optional[Sequence[str]] = None,
    lever: Optional[str] = None,
    commit: Optional[str] = None,
    verdict: Optional[str] = None,
    notes: Optional[str] = None,
    work_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    sample_list = [float(x) for x in samples] if samples else []
    if value is None:
        if not sample_list:
            raise ValueError("must supply either value or samples")
        metric_val = float(statistics.median(sample_list))
    else:
        metric_val = float(value)

    flag_list = list(flags or [])
    if work_dir is not None and (work_dir / VERIFIER_LOCK_FILE).is_file():
        chk = check_verifier(work_dir)
        if not chk["ok"] and "VERIFIER_TAMPERED" not in flag_list:
            flag_list.append("VERIFIER_TAMPERED")

    metrics_map: Dict[str, float] = {metric: metric_val}
    if guardrails:
        for k, v in guardrails.items():
            metrics_map[k] = float(v)

    row: Dict[str, Any] = {
        "timestamp": utc_now(),
        "name": name,
        "role": role,
        "metric": metric,
        "value": metric_val,
        "metrics": metrics_map,
        "gate_pass": bool(gate_pass),
        "flags": flag_list,
        "ok": bool(gate_pass) and (len(flag_list) == 0),
    }
    if sample_list:
        row["samples"] = sample_list
    if lever:
        row["lever"] = lever
    if commit:
        row["commit"] = commit
    if verdict:
        row["verdict"] = verdict
    if notes:
        row["notes"] = notes
    return row


def _extract_metric(row: Dict[str, Any], metric: str) -> float:
    metrics_map = row.get("metrics")
    if isinstance(metrics_map, dict) and metric in metrics_map:
        return float(metrics_map[metric])
    if row.get("metric") == metric and "value" in row:
        return float(row["value"])
    if metric in row:
        return float(row[metric])
    raise KeyError(f"run {row.get('name')!r} missing metric {metric!r}")


def _latest_by_name(rows: Sequence[Dict[str, Any]], name: str) -> Dict[str, Any]:
    for row in reversed(rows):
        if row.get("name") == name:
            return row
    raise KeyError(f"run not found in ledger: {name!r}")


def _gain_pct(ctl: float, cand: float, direction: str) -> float:
    denom = abs(ctl)
    if denom < 1e-12:
        if direction == "higher":
            return 100.0 if cand > ctl else (-100.0 if cand < ctl else 0.0)
        return 100.0 if cand < ctl else (-100.0 if cand > ctl else 0.0)
    if direction == "higher":
        return ((cand - ctl) / denom) * 100.0
    return ((ctl - cand) / denom) * 100.0


def _regression_pct(ctl: float, cand: float, direction: str) -> float:
    # Positive return means candidate regressed relative to control.
    denom = abs(ctl)
    if denom < 1e-12:
        if direction == "lower":
            return 100.0 if cand > ctl else 0.0
        return 100.0 if cand < ctl else 0.0
    if direction == "higher":
        return ((ctl - cand) / denom) * 100.0
    return ((cand - ctl) / denom) * 100.0


def _check_guardrails(
    controls: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    guardrails: Sequence[Tuple[str, str, float]],
) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []
    for gname, gdir, max_reg_pct in guardrails:
        ctl_vals = [_extract_metric(r, gname) for r in controls]
        cand_vals = [_extract_metric(r, gname) for r in candidates]
        ctl_ref = float(statistics.mean(ctl_vals))
        cand_ref = float(statistics.mean(cand_vals))
        reg = _regression_pct(ctl_ref, cand_ref, gdir)
        if reg > max_reg_pct:
            failures.append(
                {
                    "metric": gname,
                    "direction": gdir,
                    "control": round(ctl_ref, 4),
                    "candidate": round(cand_ref, 4),
                    "regression_pct": round(reg, 3),
                    "max_allowed_pct": max_reg_pct,
                }
            )
    return failures


def compare_triplet(
    rows: Sequence[Dict[str, Any]],
    ctl_a_name: str,
    cand_name: str,
    ctl_b_name: str,
    metric: str,
    direction: str = "higher",
    min_gain_pct: float = 3.0,
    max_drift_pct: float = 10.0,
    guardrails: Sequence[Tuple[str, str, float]] = (),
) -> Dict[str, Any]:
    if direction not in ("higher", "lower"):
        raise ValueError("direction must be 'higher' or 'lower'")

    ra = _latest_by_name(rows, ctl_a_name)
    rc = _latest_by_name(rows, cand_name)
    rb = _latest_by_name(rows, ctl_b_name)

    for r in (ra, rc, rb):
        if "VERIFIER_TAMPERED" in (r.get("flags") or []):
            return {"verdict": "VOID_VERIFIER_TAMPERED", "run": r.get("name"), "metric": metric}

    for r in (ra, rb):
        if (r.get("flags") or []) or not r.get("gate_pass", True):
            return {
                "verdict": "VOID_FLAGS",
                "reason": f"control {r.get('name')!r} failed gate or has flags",
                "flags": r.get("flags") or [],
                "metric": metric,
            }

    if rc.get("flags"):
        return {
            "verdict": "VOID_FLAGS",
            "reason": f"candidate {cand_name!r} has flags",
            "flags": rc.get("flags"),
            "metric": metric,
        }

    if not rc.get("gate_pass", True):
        return {
            "verdict": "REJECT_GATE",
            "reason": f"candidate {cand_name!r} failed hard correctness gate",
            "metric": metric,
        }

    va = _extract_metric(ra, metric)
    vc = _extract_metric(rc, metric)
    vb = _extract_metric(rb, metric)

    ctl_mean = (va + vb) / 2.0
    drift_pct = (abs(vb - va) / max(abs(ctl_mean), 1e-12)) * 100.0
    if drift_pct > max_drift_pct:
        return {
            "verdict": "VOID_CONTROL_DRIFT",
            "metric": metric,
            "ctl_a": va,
            "cand": vc,
            "ctl_b": vb,
            "control_drift_pct": round(drift_pct, 3),
            "max_drift_pct": max_drift_pct,
        }

    reg_failures = _check_guardrails([ra, rb], [rc], guardrails)
    if reg_failures:
        return {
            "verdict": "REJECT_REGRESSION",
            "metric": metric,
            "ctl_a": va,
            "cand": vc,
            "ctl_b": vb,
            "regressions": reg_failures,
        }

    gain_vs_a = _gain_pct(va, vc, direction)
    gain_vs_b = _gain_pct(vb, vc, direction)
    min_gain = min(gain_vs_a, gain_vs_b)
    mean_gain = _gain_pct(ctl_mean, vc, direction)
    verdict = "ACCEPT" if min_gain >= min_gain_pct else "REJECT_NO_GAIN"

    return {
        "verdict": verdict,
        "metric": metric,
        "direction": direction,
        "ctl_a": va,
        "cand": vc,
        "ctl_b": vb,
        "control_drift_pct": round(drift_pct, 3),
        "gain_vs_a_pct": round(gain_vs_a, 3),
        "gain_vs_b_pct": round(gain_vs_b, 3),
        "min_neighbor_gain_pct": round(min_gain, 3),
        "gain_vs_mean_pct": round(mean_gain, 3),
        "min_gain_pct": min_gain_pct,
    }


def compare_abab(
    rows: Sequence[Dict[str, Any]],
    leg_names: Sequence[str],
    metric: str,
    direction: str = "higher",
    min_gain_pct: float = 3.0,
    max_drift_pct: float = 10.0,
    guardrails: Sequence[Tuple[str, str, float]] = (),
) -> Dict[str, Any]:
    if len(leg_names) < 4 or len(leg_names) % 2 != 0:
        raise ValueError("compare-abab requires an even number of legs >= 4 (A1,B1,A2,B2,...)")

    legs = [_latest_by_name(rows, n) for n in leg_names]
    controls = legs[0::2]
    candidates = legs[1::2]

    for r in legs:
        if "VERIFIER_TAMPERED" in (r.get("flags") or []):
            return {"verdict": "VOID_VERIFIER_TAMPERED", "run": r.get("name"), "metric": metric}
        if r.get("flags"):
            return {"verdict": "VOID_FLAGS", "run": r.get("name"), "flags": r.get("flags"), "metric": metric}

    for r in controls:
        if not r.get("gate_pass", True):
            return {"verdict": "VOID_FLAGS", "reason": f"control {r.get('name')!r} failed gate", "metric": metric}
    for r in candidates:
        if not r.get("gate_pass", True):
            return {"verdict": "REJECT_GATE", "reason": f"candidate {r.get('name')!r} failed gate", "metric": metric}

    ctl_vals = [_extract_metric(r, metric) for r in controls]
    cand_vals = [_extract_metric(r, metric) for r in candidates]

    ctl_mean = float(statistics.mean(ctl_vals))
    ctl_spread_pct = ((max(ctl_vals) - min(ctl_vals)) / max(abs(ctl_mean), 1e-12)) * 100.0
    if ctl_spread_pct > max_drift_pct:
        return {
            "verdict": "VOID_CONTROL_DRIFT",
            "metric": metric,
            "controls": ctl_vals,
            "candidates": cand_vals,
            "control_drift_pct": round(ctl_spread_pct, 3),
            "max_drift_pct": max_drift_pct,
        }

    reg_failures = _check_guardrails(controls, candidates, guardrails)
    if reg_failures:
        return {
            "verdict": "REJECT_REGRESSION",
            "metric": metric,
            "controls": ctl_vals,
            "candidates": cand_vals,
            "regressions": reg_failures,
        }

    pair_gains = [_gain_pct(a, b, direction) for a, b in zip(ctl_vals, cand_vals)]
    min_pair_gain = min(pair_gains)
    median_pair_gain = float(statistics.median(pair_gains))
    all_pairs_beat = all(g >= min_gain_pct for g in pair_gains)
    verdict = "ACCEPT" if all_pairs_beat else "REJECT_NO_GAIN"

    return {
        "verdict": verdict,
        "metric": metric,
        "direction": direction,
        "pairs": len(pair_gains),
        "controls": ctl_vals,
        "candidates": cand_vals,
        "pair_gains_pct": [round(g, 3) for g in pair_gains],
        "min_pair_gain_pct": round(min_pair_gain, 3),
        "median_pair_gain_pct": round(median_pair_gain, 3),
        "control_drift_pct": round(ctl_spread_pct, 3),
        "min_gain_pct": min_gain_pct,
    }


def detect_plateau(
    rows: Sequence[Dict[str, Any]],
    consecutive: int = 3,
    metric: Optional[str] = None,
    direction: str = "higher",
    min_gain_pct: float = 3.0,
) -> Dict[str, Any]:
    candidates = [r for r in rows if r.get("role") == "candidate"]
    if len(candidates) < consecutive:
        return {
            "plateau": False,
            "consecutive_failures": 0,
            "required": consecutive,
            "candidates_seen": len(candidates),
            "reason": "fewer candidates than plateau threshold",
        }

    # Prefer explicit verdicts if present on recent candidates.
    recent = candidates[-consecutive:]
    if all(r.get("verdict") for r in recent):
        accepted_tags = {"accept", "keep", "accepted", "kept"}
        streak = 0
        for r in reversed(candidates):
            v = str(r.get("verdict", "")).strip().lower()
            if v in accepted_tags:
                break
            streak += 1
        return {
            "plateau": streak >= consecutive,
            "consecutive_failures": streak,
            "required": consecutive,
            "candidates_seen": len(candidates),
            "recent_candidates": [r.get("name") for r in recent],
            "mode": "verdict",
        }

    if not metric:
        metric = str(candidates[-1].get("metric", ""))
    baseline_rows = [r for r in rows if r.get("role") in ("baseline", "control") and r.get("ok", True)]
    best = _extract_metric(baseline_rows[0], metric) if baseline_rows else _extract_metric(candidates[0], metric)

    streak = 0
    for r in candidates:
        if not r.get("ok", True):
            streak += 1
            continue
        val = _extract_metric(r, metric)
        if _gain_pct(best, val, direction) >= min_gain_pct:
            best = val
            streak = 0
        else:
            streak += 1

    return {
        "plateau": streak >= consecutive,
        "consecutive_failures": streak,
        "required": consecutive,
        "candidates_seen": len(candidates),
        "best_value": best,
        "metric": metric,
        "mode": "metric",
    }


def format_scoreboard(
    rows: Sequence[Dict[str, Any]],
    metric: Optional[str] = None,
    direction: str = "higher",
    ceiling: Optional[float] = None,
) -> str:
    if not rows:
        return "(empty ledger)"
    m = metric or str(rows[0].get("metric", "value"))
    baseline_val: Optional[float] = None
    for r in rows:
        if r.get("role") in ("baseline", "control"):
            try:
                baseline_val = _extract_metric(r, m)
                break
            except KeyError:
                pass

    headers = ["name", "role", m, "vs_base"]
    if ceiling is not None:
        headers.append("%_ceiling")
    headers.extend(["gate", "verdict", "lever"])

    table: List[List[str]] = []
    for r in rows:
        try:
            v = _extract_metric(r, m)
            v_str = f"{v:.3f}"
        except KeyError:
            v = None
            v_str = "-"

        if v is not None and baseline_val is not None:
            g = _gain_pct(baseline_val, v, direction)
            delta_str = f"{g:+.1f}%"
        else:
            delta_str = "-"

        line = [str(r.get("name", "")), str(r.get("role", "")), v_str, delta_str]
        if ceiling is not None:
            if v is not None and abs(ceiling) > 1e-12:
                pct = (v / ceiling) * 100.0 if direction == "higher" else (ceiling / max(v, 1e-12)) * 100.0
                line.append(f"{pct:.1f}%")
            else:
                line.append("-")
        gate_str = "PASS" if r.get("gate_pass", True) and not r.get("flags") else "FAIL"
        line.extend([gate_str, str(r.get("verdict", "")), str(r.get("lever", ""))])
        table.append(line)

    widths = [max(len(headers[i]), max(len(row[i]) for row in table)) for i in range(len(headers))]
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    head = "| " + " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers))) + " |"
    body = [
        "| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |"
        for row in table
    ]
    return "\n".join([head, sep] + body)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Domain-agnostic Hill-Climbing Engineering CLI.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_init = sub.add_parser("init", help="Scaffold a hill-climbing working directory.")
    sp_init.add_argument("work_dir", type=Path)
    sp_init.add_argument("--force", action="store_true")

    sp_lock = sub.add_parser("lock-verifier", help="Record SHA-256 lock for read-only verifier files.")
    sp_lock.add_argument("work_dir", type=Path)
    sp_lock.add_argument("files", nargs="+", type=Path)

    sp_chk = sub.add_parser("check-verifier", help="Check verifier SHA-256 integrity.")
    sp_chk.add_argument("work_dir", type=Path)

    sp_rec = sub.add_parser("record", help="Append a run record to ledger.jsonl.")
    sp_rec.add_argument("ledger", type=Path)
    sp_rec.add_argument("--name", required=True)
    sp_rec.add_argument("--role", choices=("baseline", "control", "candidate", "probe"), required=True)
    sp_rec.add_argument("--metric", required=True)
    sp_rec.add_argument("--value", type=float, default=None)
    sp_rec.add_argument("--samples", type=str, default=None, help="Comma-separated sample floats (median used).")
    gate = sp_rec.add_mutually_exclusive_group()
    gate.add_argument("--gate-pass", dest="gate_pass", action="store_true", default=True)
    gate.add_argument("--gate-fail", dest="gate_pass", action="store_false")
    sp_rec.add_argument("--guardrail", action="append", default=[], help="KEY=FLOAT guardrail observation.")
    sp_rec.add_argument("--flag", action="append", default=[], help="Invalidation flag (e.g. THERMAL_DRIFT).")
    sp_rec.add_argument("--lever", default=None)
    sp_rec.add_argument("--commit", default=None)
    sp_rec.add_argument("--verdict", default=None)
    sp_rec.add_argument("--notes", default=None)
    sp_rec.add_argument("--work-dir", type=Path, default=None, help="Check .verifier.sha256 in this dir if present.")

    sp_cmp = sub.add_parser("compare", help="Judge a bracketed triplet (ctl-a -> cand -> ctl-b).")
    sp_cmp.add_argument("ledger", type=Path)
    sp_cmp.add_argument("ctl_a")
    sp_cmp.add_argument("cand")
    sp_cmp.add_argument("ctl_b")
    sp_cmp.add_argument("--metric", required=True)
    sp_cmp.add_argument("--direction", choices=("higher", "lower"), default="higher")
    sp_cmp.add_argument("--min-gain-pct", type=float, default=3.0)
    sp_cmp.add_argument("--max-drift-pct", type=float, default=10.0)
    sp_cmp.add_argument("--guardrail", action="append", default=[], help="METRIC:higher|lower:MAX_REG_PCT")

    sp_abab = sub.add_parser("compare-abab", help="Judge interleaved ABAB/ABBA legs.")
    sp_abab.add_argument("ledger", type=Path)
    sp_abab.add_argument("--legs", required=True, help="Comma-separated A1,B1,A2,B2[,...]")
    sp_abab.add_argument("--metric", required=True)
    sp_abab.add_argument("--direction", choices=("higher", "lower"), default="higher")
    sp_abab.add_argument("--min-gain-pct", type=float, default=3.0)
    sp_abab.add_argument("--max-drift-pct", type=float, default=10.0)
    sp_abab.add_argument("--guardrail", action="append", default=[], help="METRIC:higher|lower:MAX_REG_PCT")

    sp_plat = sub.add_parser("plateau", help="Check whether consecutive candidates have plateaued.")
    sp_plat.add_argument("ledger", type=Path)
    sp_plat.add_argument("--n", type=int, default=3)
    sp_plat.add_argument("--metric", default=None)
    sp_plat.add_argument("--direction", choices=("higher", "lower"), default="higher")
    sp_plat.add_argument("--min-gain-pct", type=float, default=3.0)

    sp_show = sub.add_parser("show", help="Display markdown scoreboard from ledger.jsonl.")
    sp_show.add_argument("ledger", type=Path)
    sp_show.add_argument("--metric", default=None)
    sp_show.add_argument("--direction", choices=("higher", "lower"), default="higher")
    sp_show.add_argument("--ceiling", type=float, default=None)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        res = init_workspace(args.work_dir, force=args.force)
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "lock-verifier":
        res = lock_verifier(args.work_dir, args.files)
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "check-verifier":
        res = check_verifier(args.work_dir)
        print(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1

    if args.cmd == "record":
        samples = parse_samples(args.samples) if args.samples else None
        guardrails = parse_kv_floats(args.guardrail) if args.guardrail else None
        commit = args.commit or current_git_sha()
        row = build_record(
            name=args.name,
            role=args.role,
            metric=args.metric,
            value=args.value,
            samples=samples,
            gate_pass=args.gate_pass,
            guardrails=guardrails,
            flags=args.flag,
            lever=args.lever,
            commit=commit,
            verdict=args.verdict,
            notes=args.notes,
            work_dir=args.work_dir,
        )
        append_ledger(args.ledger, row)
        print(json.dumps(row, indent=2))
        return 0

    if args.cmd == "compare":
        rows = load_ledger(args.ledger)
        res = compare_triplet(
            rows,
            args.ctl_a,
            args.cand,
            args.ctl_b,
            metric=args.metric,
            direction=args.direction,
            min_gain_pct=args.min_gain_pct,
            max_drift_pct=args.max_drift_pct,
            guardrails=parse_guardrail_specs(args.guardrail),
        )
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "compare-abab":
        rows = load_ledger(args.ledger)
        legs = [x.strip() for x in args.legs.split(",") if x.strip()]
        res = compare_abab(
            rows,
            legs,
            metric=args.metric,
            direction=args.direction,
            min_gain_pct=args.min_gain_pct,
            max_drift_pct=args.max_drift_pct,
            guardrails=parse_guardrail_specs(args.guardrail),
        )
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "plateau":
        rows = load_ledger(args.ledger)
        res = detect_plateau(
            rows,
            consecutive=args.n,
            metric=args.metric,
            direction=args.direction,
            min_gain_pct=args.min_gain_pct,
        )
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "show":
        rows = load_ledger(args.ledger)
        print(format_scoreboard(rows, metric=args.metric, direction=args.direction, ceiling=args.ceiling))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
