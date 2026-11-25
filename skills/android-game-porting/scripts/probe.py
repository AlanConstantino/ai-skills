#!/usr/bin/env python3
"""Device inventory, save backup, log telemetry parser, controlled A/B runner & ledger for Android game ports.

Standard library only (Python 3.9+).

  python3 probe.py inventory [--serial S] [--pkg P] [--json]
  python3 probe.py backup-saves --pkg P --out DIR [--serial S] [--remote-path PATH]
  python3 probe.py parse-log path/to/log.txt [--preset nx2android|generic] [--config cfg.json] [--hitch-ms 40] [--json]
  python3 probe.py windows path/to/log.txt [--marker REGEX] [--hitch-ms 40] [--json]
  python3 probe.py run configs/NAME.json [--ledger ledger.jsonl] [--allow-running]
  python3 probe.py compare ledger.jsonl ctl-a cand-1 ctl-b [--metric frame_p50_ms] [--tol 0.03]
  python3 probe.py show [ledger.jsonl]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
HEALTH_SH = HERE / "device_health.sh"

# Built-in nx2android / nxnative telemetry patterns
# (newer hosts write "frame time p50"; older ones "frame p50")
RE_NVN_TIMING = re.compile(
    r"timing up to frame \d+:.*?frame (?:time )?p50\s+([0-9.]+)\s*/\s*p95\s+([0-9.]+)\s*/\s*max\s+([0-9.]+)\s*ms"
)
RE_REPLAY = re.compile(
    r"replay per frame \(last \d+\):\s*([0-9.]+)\s*draws,\s*replay\s+([0-9.]+)\s*ms.*?GPU busy\s+([0-9.]+)\s*ms.*?descriptor sets reused\s+(\d+),\s*new\s+(\d+)"
)
RE_UPLOADS = re.compile(r"replay per frame \(last \d+\):.*?texture uploads\s+[0-9.]+\s*\(([0-9.]+)\s*MB,\s*([0-9.]+)\s*ms\)")
# One line per frame over the budget: the frame's whole time. A hitch (a frame shown a vsync late) is one over --hitch-ms
RE_LONGFRAME = re.compile(r"\[longframe\].*?whole frame\s+([0-9.]+)\s*ms")
RE_LOG_TIME = re.compile(r"^\[dm\]\s+([0-9.]+)s ")
DEFAULT_MARKER = r"DM_CTL: env (\S+=\S*)"
RE_ADAPTIVE = re.compile(
    r"adaptive frame rate:\s*([0-9.]+)\s*fps,\s*frame work p50\s+([0-9.]+)\s*/\s*p90\s+([0-9.]+)\s*/\s*p95\s+([0-9.]+)\s*ms.*?late on screen\s+(\d+)\s*of\s*64"
)
RE_DISPLAY_TIMING = re.compile(
    r"display timing:\s*(\d+)\s*frames,\s*(\d+)\s*resyncs,\s*(\d+)\s*shown a vsync"
)
RE_HWBUF = re.compile(
    r"hardware buffers:\s*the CPU reads one at\s*([0-9.]+)\s*MB/s,\s*malloc memory at\s*([0-9.]+)\s*MB/s"
)
RE_VUID = re.compile(r"(VUID-[A-Za-z0-9_.-]+)")
RE_CRASH = re.compile(r"\b(SIGSEGV|SIGABRT|SIGBUS|VK_ERROR_DEVICE_LOST|FATAL)\b")

# Universal Android `dumpsys gfxinfo <pkg>` summary patterns
RE_GFX_TOTAL = re.compile(r"Total frames rendered:\s*(\d+)")
RE_GFX_JANKY = re.compile(r"Janky frames:\s*(\d+)\s*\(([0-9.]+)%\)")
RE_GFX_P50 = re.compile(r"50th percentile:\s*([0-9.]+)ms")
RE_GFX_P90 = re.compile(r"90th percentile:\s*([0-9.]+)ms")
RE_GFX_P95 = re.compile(r"95th percentile:\s*([0-9.]+)ms")
RE_GFX_P99 = re.compile(r"99th percentile:\s*([0-9.]+)ms")


def adb_base(serial: str = "") -> List[str]:
    return ["adb", "-s", serial] if serial else ["adb"]


def run_health(serial: str = "", pkg: str = "") -> Dict[str, Any]:
    cmd = ["bash", str(HEALTH_SH), "--json"]
    if serial:
        cmd.extend(["--serial", serial])
    if pkg:
        cmd.extend(["--pkg", pkg])
    p = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def med(vals: List[float]) -> Optional[float]:
    return round(statistics.median(vals), 2) if vals else None


def parse_log_text(
    text: str,
    preset: str = "nx2android",
    skip_first: int = 0,
    extra_metrics: Optional[Dict[str, Dict[str, Any]]] = None,
    hitch_ms: float = 40.0,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "preset": preset,
        "red_lines": 0,
        "red_samples": [],
        "vuid_count": 0,
        "unique_vuids": [],
        "crash_markers": [],
    }
    red_msgs: Dict[str, int] = {}
    vuids: set = set()
    crashes: set = set()

    f_p50: List[float] = []
    f_p95: List[float] = []
    f_max: List[float] = []
    draws: List[float] = []
    replay_ms: List[float] = []
    gpu_ms: List[float] = []
    desc_reuse: List[float] = []
    adapt_fps: List[float] = []
    late64: List[float] = []
    resyncs: Optional[int] = None
    late_cum: Optional[int] = None
    hwbuf_mbs: Optional[float] = None
    malloc_mbs: Optional[float] = None
    upload_mb: List[float] = []
    upload_ms: List[float] = []
    long_frames: List[float] = []

    for line in text.splitlines():
        if "[RED]" in line:
            out["red_lines"] += 1
            msg = line.split("[RED]", 1)[1].strip()[:120]
            red_msgs[msg] = red_msgs.get(msg, 0) + 1
        for vm in RE_VUID.findall(line):
            out["vuid_count"] += 1
            vuids.add(vm)
        for cm in RE_CRASH.findall(line):
            crashes.add(cm)

        if preset == "nx2android":
            m = RE_NVN_TIMING.search(line)
            if m:
                f_p50.append(float(m.group(1)))
                f_p95.append(float(m.group(2)))
                f_max.append(float(m.group(3)))
            m = RE_REPLAY.search(line)
            if m:
                draws.append(float(m.group(1)))
                replay_ms.append(float(m.group(2)))
                gpu_ms.append(float(m.group(3)))
                reused, new = int(m.group(4)), int(m.group(5))
                if reused + new > 0:
                    desc_reuse.append(100.0 * reused / (reused + new))
            m = RE_ADAPTIVE.search(line)
            if m:
                adapt_fps.append(float(m.group(1)))
                late64.append(float(m.group(5)))
            m = RE_DISPLAY_TIMING.search(line)
            if m:
                resyncs = int(m.group(2))
                late_cum = int(m.group(3))
            m = RE_HWBUF.search(line)
            if m:
                hwbuf_mbs = float(m.group(1))
                malloc_mbs = float(m.group(2))
            m = RE_UPLOADS.search(line)
            if m:
                upload_mb.append(float(m.group(1)))
                upload_ms.append(float(m.group(2)))
            m = RE_LONGFRAME.search(line)
            if m:
                long_frames.append(float(m.group(1)))

    if preset == "nx2android":
        def trim(xs: List[float]) -> List[float]:
            return xs[skip_first:] if skip_first and len(xs) > skip_first else xs

        tf_p50, tf_p95, tf_max = trim(f_p50), trim(f_p95), trim(f_max)
        tdraws, treplay, tgpu, tdesc = trim(draws), trim(replay_ms), trim(gpu_ms), trim(desc_reuse)
        tadapt, tlate = trim(adapt_fps), trim(late64)
        out.update({
            "windows": len(tf_p50),
            "frame_p50_ms": med(tf_p50),
            "frame_p95_ms": med(tf_p95),
            "frame_max_ms": round(max(tf_max), 2) if tf_max else None,
            "fps_from_p50": round(1000.0 / statistics.median(tf_p50), 1) if tf_p50 and statistics.median(tf_p50) > 0 else None,
            "draws": round(statistics.median(tdraws), 0) if tdraws else None,
            "replay_p50_ms": med(treplay),
            "gpu_p50_ms": med(tgpu),
            "desc_reuse_pct": med(tdesc),
            "adaptive_fps": med(tadapt),
            "late_of_64": med(tlate),
            "display_resyncs": resyncs,
            "display_late_cum": late_cum,
            "hwbuf_read_mbs": hwbuf_mbs,
            "malloc_read_mbs": malloc_mbs,
            "upload_mb_per_frame": med(trim(upload_mb)),
            "upload_ms_per_frame": med(trim(upload_ms)),
            "long_frames": len(long_frames),
            "hitches": sum(1 for x in long_frames if x > hitch_ms),
            "hitch_ms": hitch_ms,
        })
    elif preset == "gfxinfo":
        m = RE_GFX_TOTAL.search(text)
        out["total_frames"] = int(m.group(1)) if m else None
        m = RE_GFX_JANKY.search(text)
        out["janky_frames"] = int(m.group(1)) if m else None
        out["janky_pct"] = float(m.group(2)) if m else None
        for key, rx in (("frame_p50_ms", RE_GFX_P50), ("frame_p90_ms", RE_GFX_P90), ("frame_p95_ms", RE_GFX_P95), ("frame_p99_ms", RE_GFX_P99)):
            m = rx.search(text)
            out[key] = float(m.group(1)) if m else None

    if extra_metrics:
        for name, spec in extra_metrics.items():
            rx = re.compile(spec["pattern"])
            agg = spec.get("agg", "median")
            matches = rx.findall(text)
            if agg == "count":
                out[name] = len(matches)
            else:
                nums = [float(x if isinstance(x, str) else x[0]) for x in matches]
                if not nums:
                    out[name] = None
                elif agg == "last":
                    out[name] = round(nums[-1], 3)
                elif agg == "max":
                    out[name] = round(max(nums), 3)
                elif agg == "min":
                    out[name] = round(min(nums), 3)
                else:
                    out[name] = round(statistics.median(nums), 3)

    out["red_samples"] = [
        {"msg": k, "count": v}
        for k, v in sorted(red_msgs.items(), key=lambda kv: kv[1], reverse=True)[:5]
    ]
    out["unique_vuids"] = sorted(vuids)
    out["crash_markers"] = sorted(crashes)
    return out


def split_windows(text: str, marker: str = DEFAULT_MARKER) -> List[Dict[str, Any]]:
    """Cut a log at each line matching `marker` (a live toggle, e.g. `DM_CTL: env KEY=VALUE`). → [{label, t0, t1, text}], the first window labelled `start`.
    Lines without a `[dm] <seconds>s` time stay in the current window; t0/t1 are None when no line in it had a time."""
    rx = re.compile(marker)
    wins: List[Dict[str, Any]] = [{"label": "start", "t0": None, "t1": None, "lines": []}]
    for line in text.splitlines():
        tm = RE_LOG_TIME.match(line)
        t = float(tm.group(1)) if tm else None
        m = rx.search(line)
        if m:
            wins.append({"label": m.group(1) if m.groups() else m.group(0), "t0": t, "t1": t, "lines": []})
            continue
        w = wins[-1]
        if t is not None:
            if w["t0"] is None or (w["t1"] is not None and t < w["t1"]):   # (a log whose clock restarts: its window counts from there)
                w["t0"] = t
            w["t1"] = t
        w["lines"].append(line)
    out = []
    for w in wins:
        if w["label"] == "start" and not any(l.strip() for l in w["lines"]):
            continue
        out.append({"label": w["label"], "t0": w["t0"], "t1": w["t1"], "text": "\n".join(w["lines"])})
    return out


def window_table(text: str, marker: str = DEFAULT_MARKER, preset: str = "nx2android", hitch_ms: float = 40.0) -> List[Dict[str, Any]]:
    rows = []
    for w in split_windows(text, marker):
        r = parse_log_text(w["text"], preset=preset, hitch_ms=hitch_ms)
        secs = (w["t1"] - w["t0"]) if w["t0"] is not None and w["t1"] is not None else None
        rows.append({"label": w["label"], "t0": w["t0"], "seconds": round(secs, 1) if secs is not None else None, **{k: r.get(k) for k in (
            "windows", "frame_p50_ms", "frame_p95_ms", "frame_max_ms", "replay_p50_ms", "gpu_p50_ms", "upload_mb_per_frame", "upload_ms_per_frame",
            "long_frames", "hitches", "red_lines", "vuid_count")}})
    return rows


def format_windows(rows: List[Dict[str, Any]]) -> str:
    cols = ("label", "seconds", "frame_p50_ms", "frame_p95_ms", "replay_p50_ms", "gpu_p50_ms", "upload_mb_per_frame", "long_frames", "hitches", "red_lines")
    lines = [" | ".join(cols), " | ".join("---" for _ in cols)]
    for r in rows:
        lines.append(" | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols))
    return "\n".join(lines)


def backup_saves(pkg: str, out_dir: Path, serial: str = "", remote_path: Optional[str] = None) -> Dict[str, Any]:
    src = remote_path or f"/sdcard/Android/data/{pkg}/files/save"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = out_dir / f"{pkg}-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    adb = adb_base(serial)
    p = subprocess.run(adb + ["pull", src, str(dest)], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"adb pull {src} failed: {p.stderr.strip() or p.stdout.strip()}")

    files: List[Dict[str, Any]] = []
    for f in sorted(dest.rglob("*")):
        if f.is_file():
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            files.append({"path": str(f.relative_to(dest)), "bytes": f.stat().st_size, "sha256": h})
    if not files:
        raise RuntimeError(f"save backup pulled 0 files from {src}")
    manifest = {
        "package": pkg,
        "remote_path": src,
        "timestamp": stamp,
        "file_count": len(files),
        "total_bytes": sum(f["bytes"] for f in files),
        "files": files,
    }
    (dest / "manifest.sha256.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["local_dir"] = str(dest)
    return manifest


def run_experiment(cfg_path: Path, ledger_path: Path, allow_running: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    serial = cfg.get("serial", "")
    pkg = cfg["package"]
    mode = cfg.get("mode", "attach")
    preset = cfg.get("preset", "nx2android")
    guard = cfg.get("guardrails", {})
    adb = adb_base(serial)

    pre = run_health(serial, pkg)
    proc_pre = pre.get("process") or {}

    if mode == "launch" and proc_pre.get("running") and not allow_running:
        raise SystemExit(
            f"REFUSED: {pkg} is currently running (pid={proc_pre.get('pid')}). "
            "Confirm with the user before interrupting, or pass --allow-running / use mode='attach'."
        )
    if mode == "attach" and not proc_pre.get("running"):
        raise SystemExit(f"REFUSED: mode='attach' requires {pkg} to be running on the device.")

    batt_pre = pre.get("battery", {})
    if guard.get("require_ac") and not batt_pre.get("powered"):
        raise SystemExit("REFUSED: device is on battery (guardrails.require_ac=true)")
    if guard.get("min_battery_pct") is not None and batt_pre.get("level_pct") is not None:
        if batt_pre["level_pct"] < guard["min_battery_pct"]:
            raise SystemExit(
                f"REFUSED: battery {batt_pre['level_pct']}% < min_battery_pct {guard['min_battery_pct']}%"
            )

    if dry_run:
        return {"dry_run": True, "name": cfg["name"], "pre_health": pre}

    log_remote = cfg.get("log_remote_path", f"/sdcard/Android/data/{pkg}/files/log.txt")
    sample_s = int(cfg.get("sample_seconds", 30))

    start_line = 1
    if preset == "gfxinfo":
        subprocess.run(adb + ["shell", "dumpsys", "gfxinfo", pkg, "reset"], capture_output=True, text=True)
    elif mode == "attach":
        wc = subprocess.run(
            adb + ["shell", f"wc -l < '{log_remote}' 2>/dev/null || echo 0"],
            capture_output=True, text=True,
        ).stdout.strip()
        start_line = (int(wc) + 1) if wc.isdigit() else 1

    if mode == "launch":
        subprocess.run(adb + ["shell", "am", "force-stop", pkg], check=True)
        activity = cfg.get("activity", f"{pkg}/android.app.NativeActivity")
        subprocess.run(adb + ["shell", "am", "start", "-n", activity], check=True)
        warmup_s = int(cfg.get("warmup_seconds", 15))
        time.sleep(warmup_s)

    time.sleep(sample_s)

    post = run_health(serial, pkg)
    if preset == "gfxinfo":
        raw_text = subprocess.run(
            adb + ["shell", "dumpsys", "gfxinfo", pkg],
            capture_output=True, text=True,
        ).stdout
    else:
        raw_text = subprocess.run(
            adb + ["shell", f"tail -n +{start_line} '{log_remote}' 2>/dev/null"],
            capture_output=True, text=True, errors="replace",
        ).stdout

    metrics = parse_log_text(
        raw_text,
        preset=preset,
        skip_first=int(cfg.get("skip_first_windows", 0)),
        extra_metrics=cfg.get("extra_metrics"),
    )

    flags: List[str] = []
    proc_post = post.get("process") or {}
    batt_post = post.get("battery", {})
    if not proc_post.get("running"):
        flags.append("CRASH_OR_DEAD")
    elif proc_pre.get("pid") and proc_post.get("pid") != proc_pre.get("pid"):
        flags.append("PID_CHANGED")
    if guard.get("require_ac") and not batt_post.get("powered"):
        flags.append("ON_BATTERY")
    if post.get("cpu_policy_capped"):
        flags.append("CPU_POLICY_CAP")

    t_pre, t_post = pre.get("temp_max_c"), post.get("temp_max_c")
    temp_drift = round(t_post - t_pre, 1) if (t_pre is not None and t_post is not None) else None
    if guard.get("max_temp_c") is not None and t_post is not None and t_post > guard["max_temp_c"]:
        flags.append("THERMAL_CAP")
    if guard.get("max_temp_drift_c") is not None and temp_drift is not None and abs(temp_drift) > guard["max_temp_drift_c"]:
        flags.append("THERMAL_DRIFT")
    if guard.get("max_rss_mib") is not None and proc_post.get("rss_mib", 0) > guard["max_rss_mib"]:
        flags.append("MEM_CAP")
    if metrics.get("red_lines", 0) > guard.get("max_red_lines", 0):
        flags.append("HAS_RED")
    if metrics.get("vuid_count", 0) > guard.get("max_vuids", 0):
        flags.append("HAS_VUID")
    if metrics.get("crash_markers"):
        flags.append("CRASH_MARKER")

    rec: Dict[str, Any] = {
        "name": cfg["name"],
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "package": pkg,
        "scene": cfg.get("scene", ""),
        "driver": cfg.get("driver", ""),
        "target_fps": cfg.get("target_fps"),
        "sample_seconds": sample_s,
        **metrics,
        "rss_mib": proc_post.get("rss_mib"),
        "mem_avail_mib": (post.get("memory_mib") or {}).get("memavailable"),
        "temp_pre_c": t_pre,
        "temp_post_c": t_post,
        "temp_drift_c": temp_drift,
        "battery_pct": batt_post.get("level_pct"),
        "flags": sorted(set(flags)),
        "ok": not any(f in ("CRASH_OR_DEAD", "PID_CHANGED", "ON_BATTERY", "THERMAL_DRIFT", "MEM_CAP", "CRASH_MARKER") for f in flags),
    }
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def compare_triplet(
    rows: List[Dict[str, Any]],
    ctl_a_name: str,
    cand_name: str,
    ctl_b_name: str,
    metric: str = "frame_p50_ms",
    higher_is_better: bool = False,
    tol: float = 0.03,
    max_control_drift: float = 0.06,
) -> Dict[str, Any]:
    by_name = {r["name"]: r for r in rows}
    missing = [n for n in (ctl_a_name, cand_name, ctl_b_name) if n not in by_name]
    if missing:
        raise ValueError(f"missing runs in ledger: {', '.join(missing)}")
    a, c, b = by_name[ctl_a_name], by_name[cand_name], by_name[ctl_b_name]

    bad_flags = [
        f"{n}:{r.get('flags')}"
        for n, r in ((ctl_a_name, a), (cand_name, c), (ctl_b_name, b))
        if not r.get("ok", True)
    ]
    if bad_flags:
        return {"verdict": "VOID_FLAGS", "reason": "; ".join(bad_flags), "metric": metric}

    va, vc, vb = a.get(metric), c.get(metric), b.get(metric)
    if va is None or vc is None or vb is None:
        raise ValueError(f"metric '{metric}' missing in one of the runs")
    va, vc, vb = float(va), float(vc), float(vb)

    ctl_mean = (va + vb) / 2.0
    drift = abs(vb - va) / abs(ctl_mean) if ctl_mean != 0 else 0.0
    delta_pct = ((vc - ctl_mean) / abs(ctl_mean) * 100.0) if ctl_mean != 0 else 0.0

    if drift > max_control_drift:
        return {
            "verdict": "VOID_CONTROL_DRIFT",
            "metric": metric,
            "ctl_a": va,
            "cand": vc,
            "ctl_b": vb,
            "control_drift_pct": round(drift * 100.0, 2),
            "reason": f"controls drifted by {drift * 100:.1f}% (>{max_control_drift * 100:.0f}%): repeat in steady thermal state",
        }

    if higher_is_better:
        beats_both = vc > max(va, vb) and (vc - ctl_mean) / max(abs(ctl_mean), 1e-9) >= tol
    else:
        beats_both = vc < min(va, vb) and (ctl_mean - vc) / max(abs(ctl_mean), 1e-9) >= tol

    regressions: List[str] = []
    # Check standard guardrails (lower is better)
    for g in ("frame_p95_ms", "gpu_p50_ms", "replay_p50_ms", "rss_mib"):
        if g == metric:
            continue
        ga, gc, gb = a.get(g), c.get(g), b.get(g)
        if ga is not None and gc is not None and gb is not None:
            g_ref = max(float(ga), float(gb))
            if g_ref > 0 and (float(gc) - g_ref) / g_ref > tol:
                regressions.append(f"{g} regressed {g_ref:.2f} -> {float(gc):.2f}")
    for count_g in ("red_lines", "vuid_count"):
        ga, gc, gb = a.get(count_g, 0) or 0, c.get(count_g, 0) or 0, b.get(count_g, 0) or 0
        if gc > max(ga, gb):
            regressions.append(f"{count_g} increased {max(ga, gb)} -> {gc}")

    if regressions:
        verdict = "REJECT_REGRESSION"
        reason = "; ".join(regressions)
    elif beats_both:
        verdict = "ACCEPT"
        reason = f"beats both controls ({va} & {vb}) at {vc} ({delta_pct:+.2f}%)"
    else:
        verdict = "REJECT_NO_GAIN"
        reason = f"candidate {vc} does not beat both controls ({va}, {vb}) by >= {tol * 100:.1f}%"

    return {
        "verdict": verdict,
        "metric": metric,
        "ctl_a": va,
        "cand": vc,
        "ctl_b": vb,
        "delta_vs_ctl_mean_pct": round(delta_pct, 2),
        "control_drift_pct": round(drift * 100.0, 2),
        "regressions": regressions,
        "reason": reason,
    }


def format_ledger(rows: List[Dict[str, Any]]) -> str:
    cols = [
        ("frame_p50_ms", "p50_ms"),
        ("frame_p95_ms", "p95_ms"),
        ("gpu_p50_ms", "gpu_ms"),
        ("replay_p50_ms", "rpl_ms"),
        ("late_of_64", "late/64"),
        ("red_lines", "RED"),
        ("vuid_count", "VUID"),
        ("rss_mib", "rss_MiB"),
        ("temp_post_c", "temp_C"),
    ]
    lines = ["name".ljust(24) + "".join(lbl.rjust(9) for _, lbl in cols) + "  flags"]
    for r in rows:
        vals = "".join(str(r.get(k, "-"))[:8].rjust(9) for k, _ in cols)
        fl = ",".join(r.get("flags") or [])
        lines.append(r.get("name", "?")[:24].ljust(24) + vals + "  " + fl)
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_inv = sub.add_parser("inventory", help="Read-only Android device & package inventory")
    p_inv.add_argument("--serial", default="")
    p_inv.add_argument("--pkg", default="")
    p_inv.add_argument("--json", action="store_true")

    p_bk = sub.add_parser("backup-saves", help="Back up game saves with SHA-256 manifest")
    p_bk.add_argument("--pkg", required=True)
    p_bk.add_argument("--out", type=Path, required=True)
    p_bk.add_argument("--serial", default="")
    p_bk.add_argument("--remote-path", default=None)

    p_pl = sub.add_parser("parse-log", help="Parse an offline log file into structured metrics")
    p_pl.add_argument("log", type=Path)
    p_pl.add_argument("--preset", default="nx2android", choices=("nx2android", "gfxinfo", "generic"))
    p_pl.add_argument("--skip-first", type=int, default=0)
    p_pl.add_argument("--config", type=Path, default=None, help="Optional config JSON for extra_metrics")
    p_pl.add_argument("--hitch-ms", type=float, default=40.0, help="A long frame over this counts as a hitch (default 40: 30 fps on 60 Hz)")
    p_pl.add_argument("--json", action="store_true")

    p_win = sub.add_parser("windows", help="Cut a log at live-toggle marker lines and summarise each window (same-run A/B)")
    p_win.add_argument("log", type=Path)
    p_win.add_argument("--marker", default=DEFAULT_MARKER, help="Regex of a window's first line; group 1 is its label (default: DM_CTL env lines)")
    p_win.add_argument("--preset", default="nx2android", choices=("nx2android", "generic"))
    p_win.add_argument("--hitch-ms", type=float, default=40.0)
    p_win.add_argument("--json", action="store_true")

    p_run = sub.add_parser("run", help="Run a controlled telemetry capture and append to ledger.jsonl")
    p_run.add_argument("config", type=Path)
    p_run.add_argument("--ledger", type=Path, default=Path("ledger.jsonl"))
    p_run.add_argument("--allow-running", action="store_true")
    p_run.add_argument("--dry-run", action="store_true")

    p_cmp = sub.add_parser("compare", help="Judge a control -> candidate -> control triplet")
    p_cmp.add_argument("ledger", type=Path)
    p_cmp.add_argument("ctl_a")
    p_cmp.add_argument("cand")
    p_cmp.add_argument("ctl_b")
    p_cmp.add_argument("--metric", default="frame_p50_ms")
    p_cmp.add_argument("--higher-is-better", action="store_true")
    p_cmp.add_argument("--tol", type=float, default=0.03)
    p_cmp.add_argument("--max-drift", type=float, default=0.06)
    p_cmp.add_argument("--json", action="store_true")

    p_sh = sub.add_parser("show", help="Print ledger.jsonl table")
    p_sh.add_argument("ledger", type=Path, nargs="?", default=Path("ledger.jsonl"))

    args = ap.parse_args(argv)

    if args.cmd == "inventory":
        if args.json:
            print(json.dumps(run_health(args.serial, args.pkg), indent=2))
        else:
            cmd = ["bash", str(HEALTH_SH)]
            if args.serial:
                cmd.extend(["--serial", args.serial])
            if args.pkg:
                cmd.extend(["--pkg", args.pkg])
            return subprocess.run(cmd).returncode
        return 0

    if args.cmd == "backup-saves":
        res = backup_saves(args.pkg, args.out, serial=args.serial, remote_path=args.remote_path)
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "parse-log":
        extra = None
        if args.config:
            extra = json.loads(args.config.read_text(encoding="utf-8")).get("extra_metrics")
        res = parse_log_text(
            args.log.read_text(encoding="utf-8", errors="replace"),
            preset=args.preset,
            skip_first=args.skip_first,
            extra_metrics=extra,
            hitch_ms=args.hitch_ms,
        )
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "windows":
        rows = window_table(args.log.read_text(encoding="utf-8", errors="replace"), marker=args.marker, preset=args.preset, hitch_ms=args.hitch_ms)
        print(json.dumps(rows, indent=2) if args.json else format_windows(rows))
        return 0

    if args.cmd == "run":
        res = run_experiment(args.config, args.ledger, allow_running=args.allow_running, dry_run=args.dry_run)
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok", True) else 1

    if args.cmd == "compare":
        rows = [json.loads(l) for l in args.ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        res = compare_triplet(
            rows,
            args.ctl_a,
            args.cand,
            args.ctl_b,
            metric=args.metric,
            higher_is_better=args.higher_is_better,
            tol=args.tol,
            max_control_drift=args.max_drift,
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"{res['verdict']}: {res['reason']}")
            if "ctl_a" in res:
                print(
                    f"  {res['metric']}: ctl_a={res['ctl_a']}  cand={res['cand']}  ctl_b={res['ctl_b']}  "
                    f"(delta={res.get('delta_vs_ctl_mean_pct', '-')}%  drift={res.get('control_drift_pct', '-')}%)"
                )
        return 0 if res["verdict"] == "ACCEPT" else 1

    rows = [json.loads(l) for l in args.ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(format_ledger(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
