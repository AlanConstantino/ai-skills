#!/usr/bin/env python3
"""Raw float32 GPU dump auditor (RGB32F HDR colour or R32F depth) for NaN / Inf forensics.

Standard library only (Python 3.9+); requires no numpy or Pillow.

  python3 f32_audit.py file pass_30.ppm.f32 --channels 3 --width 1280 --height 720
  python3 f32_audit.py scan ./passes/
  python3 f32_audit.py compare ./passes-before/ ./passes-after/
"""
from __future__ import annotations

import argparse
from array import array
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

RAW_DIM_RE = re.compile(r"pass dump (\d+) RGB32F:\s*(\d+)x(\d+)")
PASS_NUM_RE = re.compile(r"(\d+)")


def guess_channels(path: Path) -> int:
    name = path.name.lower()
    if name.endswith(".ppm.f32") or "rgb" in name or "hdr" in name:
        return 3
    if "depth" in name or name.endswith("_d.f32"):
        return 1
    return 1 if (path.stat().st_size // 4) % 3 != 0 else 3


def audit_file(
    path: Path,
    channels: int = 3,
    width: Optional[int] = None,
    height: Optional[int] = None,
    limit: int = 16,
) -> Dict[str, Any]:
    size = path.stat().st_size
    if size % 4 != 0:
        raise ValueError(f"{path}: size {size} is not a multiple of 4 bytes")
    n_floats = size // 4
    if channels not in (1, 3):
        raise ValueError(f"channels must be 1 or 3, got {channels}")
    if n_floats % channels != 0:
        raise ValueError(f"{path}: {n_floats} floats not divisible by {channels} channels")
    n_pixels = n_floats // channels
    if width is not None and height is not None and width * height != n_pixels:
        raise ValueError(f"{path}: expected {width}x{height}={width * height} pixels, got {n_pixels}")

    vals = array("f")
    with path.open("rb") as fh:
        vals.fromfile(fh, n_floats)
    if sys.byteorder != "little":
        vals.byteswap()

    nan_ch = pos_inf = neg_inf = neg_fin = finite_ch = bad_px = 0
    fmin = math.inf
    fmax = -math.inf
    ftotal = 0.0
    examples: List[Dict[str, Any]] = []

    if channels == 3:
        for px in range(n_pixels):
            base = px * 3
            rgb = (vals[base], vals[base + 1], vals[base + 2])
            bad = False
            for v in rgb:
                if math.isnan(v):
                    nan_ch += 1
                    bad = True
                elif math.isinf(v):
                    if v > 0:
                        pos_inf += 1
                    else:
                        neg_inf += 1
                    bad = True
                else:
                    finite_ch += 1
                    if v < 0.0:
                        neg_fin += 1
                    if v < fmin:
                        fmin = v
                    if v > fmax:
                        fmax = v
                    ftotal += v
            if bad:
                bad_px += 1
                if len(examples) < limit:
                    ex: Dict[str, Any] = {
                        "pixel": px,
                        "rgb": [round(v, 5) if math.isfinite(v) else str(v) for v in rgb],
                    }
                    if width:
                        ex["x"] = px % width
                        ex["y"] = px // width
                    examples.append(ex)
    else:
        for px in range(n_pixels):
            v = vals[px]
            if math.isnan(v):
                nan_ch += 1
                bad_px += 1
                if len(examples) < limit:
                    ex = {"pixel": px, "val": "nan"}
                    if width:
                        ex["x"] = px % width
                        ex["y"] = px // width
                    examples.append(ex)
            elif math.isinf(v):
                if v > 0:
                    pos_inf += 1
                else:
                    neg_inf += 1
                bad_px += 1
                if len(examples) < limit:
                    ex = {"pixel": px, "val": str(v)}
                    if width:
                        ex["x"] = px % width
                        ex["y"] = px // width
                    examples.append(ex)
            else:
                finite_ch += 1
                if v < 0.0:
                    neg_fin += 1
                if v < fmin:
                    fmin = v
                if v > fmax:
                    fmax = v
                ftotal += v

    return {
        "file": path.name,
        "channels": channels,
        "pixels": n_pixels,
        "width": width,
        "height": height,
        "nan_channels": nan_ch,
        "pos_inf_channels": pos_inf,
        "neg_inf_channels": neg_inf,
        "nonfinite_pixels": bad_px,
        "negative_finite_channels": neg_fin,
        "finite_min": round(fmin, 6) if finite_ch else None,
        "finite_max": round(fmax, 6) if finite_ch else None,
        "finite_mean": round(ftotal / finite_ch, 6) if finite_ch else None,
        "examples": examples,
    }


def load_log_dims(directory: Path) -> Dict[int, Tuple[int, int]]:
    dims: Dict[int, Tuple[int, int]] = {}
    for name in ("dump.log", "log.txt"):
        p = directory / name
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = RAW_DIM_RE.search(line)
            if m:
                dims[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    return dims


def scan_dir(directory: Path, pattern: str = "*.f32", channels_opt: str = "auto", limit: int = 8) -> Dict[str, Any]:
    dims = load_log_dims(directory)
    files = sorted(directory.glob(pattern))
    results: List[Dict[str, Any]] = []
    earliest_bad: Optional[str] = None
    total_nonfinite = 0

    for f in files:
        ch = guess_channels(f) if channels_opt == "auto" else int(channels_opt)
        w = h = None
        m = PASS_NUM_RE.search(f.name)
        if m and int(m.group(1)) in dims and ch == 3:
            w, h = dims[int(m.group(1))]
            if w * h * 12 != f.stat().st_size:
                w = h = None
        r = audit_file(f, channels=ch, width=w, height=h, limit=limit)
        results.append(r)
        if r["nonfinite_pixels"] > 0:
            total_nonfinite += r["nonfinite_pixels"]
            if earliest_bad is None:
                earliest_bad = r["file"]

    return {
        "directory": str(directory),
        "files_audited": len(results),
        "files_with_nonfinite": sum(1 for r in results if r["nonfinite_pixels"] > 0),
        "total_nonfinite_pixels": total_nonfinite,
        "earliest_bad_file": earliest_bad,
        "files": results,
    }


def compare_dirs(dir_a: Path, dir_b: Path, pattern: str = "*.f32", channels_opt: str = "auto") -> Dict[str, Any]:
    a = scan_dir(dir_a, pattern=pattern, channels_opt=channels_opt, limit=4)
    b = scan_dir(dir_b, pattern=pattern, channels_opt=channels_opt, limit=4)
    map_a = {r["file"]: r for r in a["files"]}
    map_b = {r["file"]: r for r in b["files"]}
    names = sorted(set(map_a) | set(map_b))
    rows = []
    for n in names:
        ra, rb = map_a.get(n), map_b.get(n)
        na = ra["nonfinite_pixels"] if ra else None
        nb = rb["nonfinite_pixels"] if rb else None
        rows.append({"file": n, "before_nonfinite": na, "after_nonfinite": nb})
    return {
        "before_dir": str(dir_a),
        "after_dir": str(dir_b),
        "before_total_nonfinite": a["total_nonfinite_pixels"],
        "after_total_nonfinite": b["total_nonfinite_pixels"],
        "clean_after": b["total_nonfinite_pixels"] == 0 and b["files_audited"] > 0,
        "rows": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_file = sub.add_parser("file", help="Audit a single .f32 file")
    p_file.add_argument("path", type=Path)
    p_file.add_argument("--channels", default="auto", choices=("auto", "1", "3"))
    p_file.add_argument("--width", type=int)
    p_file.add_argument("--height", type=int)
    p_file.add_argument("--limit", type=int, default=16)
    p_file.add_argument("--json", action="store_true")

    p_scan = sub.add_parser("scan", help="Audit all .f32 files in a directory")
    p_scan.add_argument("directory", type=Path)
    p_scan.add_argument("--glob", default="*.f32")
    p_scan.add_argument("--channels", default="auto", choices=("auto", "1", "3"))
    p_scan.add_argument("--limit", type=int, default=8)
    p_scan.add_argument("--json", action="store_true")

    p_cmp = sub.add_parser("compare", help="Compare two pass-dump directories (before vs after)")
    p_cmp.add_argument("before", type=Path)
    p_cmp.add_argument("after", type=Path)
    p_cmp.add_argument("--glob", default="*.f32")
    p_cmp.add_argument("--channels", default="auto", choices=("auto", "1", "3"))
    p_cmp.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "file":
        ch = guess_channels(args.path) if args.channels == "auto" else int(args.channels)
        res = audit_file(args.path, channels=ch, width=args.width, height=args.height, limit=args.limit)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(
                f"{res['file']}: ch={res['channels']} px={res['pixels']} "
                f"nonfinite_px={res['nonfinite_pixels']} (NaN={res['nan_channels']}, +Inf={res['pos_inf_channels']}, -Inf={res['neg_inf_channels']}) "
                f"finite=[{res['finite_min']}, {res['finite_max']}] mean={res['finite_mean']}"
            )
            for ex in res["examples"][:8]:
                print("  bad:", ex)
        return 0 if res["nonfinite_pixels"] == 0 else 1

    if args.cmd == "scan":
        res = scan_dir(args.directory, pattern=args.glob, channels_opt=args.channels, limit=args.limit)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(
                f"Scanned {res['files_audited']} files in {res['directory']}: "
                f"{res['files_with_nonfinite']} with non-finites (total {res['total_nonfinite_pixels']} px)"
            )
            if res["earliest_bad_file"]:
                print(f"Earliest poisoned file: {res['earliest_bad_file']}")
            for r in res["files"]:
                flag = "BAD" if r["nonfinite_pixels"] > 0 else "ok "
                print(
                    f"  [{flag}] {r['file']:24s} ch={r['channels']} nonfin={r['nonfinite_pixels']:8d} "
                    f"nan={r['nan_channels']:8d} inf={r['pos_inf_channels'] + r['neg_inf_channels']:6d} "
                    f"max={r['finite_max']}"
                )
        return 0 if res["total_nonfinite_pixels"] == 0 else 1

    res = compare_dirs(args.before, args.after, pattern=args.glob, channels_opt=args.channels)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(
            f"Before: {res['before_total_nonfinite']} non-finite px -> "
            f"After: {res['after_total_nonfinite']} non-finite px "
            f"({'CLEAN' if res['clean_after'] else 'STILL POISONED'})"
        )
        for row in res["rows"]:
            if (row["before_nonfinite"] or 0) > 0 or (row["after_nonfinite"] or 0) > 0:
                print(f"  {row['file']:24s}  {str(row['before_nonfinite']):>10s} -> {str(row['after_nonfinite']):>10s}")
    return 0 if res["clean_after"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
