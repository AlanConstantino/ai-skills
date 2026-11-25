#!/usr/bin/env python3
"""Frame-time, bandwidth, memory, and zero-copy chunk ceiling calculator for native Android game ports.

Standard library only (Python 3.9+).

  python3 budget.py examples/device-rp6-nier.json
  python3 budget.py examples/device-rp6-nier.json --json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def pacing_divisors(display_hz: float) -> List[Dict[str, float]]:
    out = []
    for k in (1, 2, 3, 4):
        fps = display_hz / k
        if fps >= 20:
            out.append({
                "divisor": k,
                "fps": round(fps, 2),
                "budget_ms": round(1000.0 / fps, 2),
                "p95_safe_ms": round(0.92 * 1000.0 / fps, 2),
            })
    return out


def analyze(cfg: Dict[str, Any]) -> Dict[str, Any]:
    display_hz = float(cfg.get("display_hz", 60))
    target_fps = float(cfg.get("target_fps", 60))
    engine_cap = float(cfg.get("engine_cap_fps", target_fps))
    budget_ms = 1000.0 / target_fps
    p95_safe_ms = budget_ms * 0.92

    divisors = pacing_divisors(display_hz)
    ratio = display_hz / target_fps
    clean_divisor = abs(ratio - round(ratio)) < 0.02

    warnings: List[str] = []
    if not clean_divisor:
        warnings.append(
            f"target_fps ({target_fps:g}) does not evenly divide display_hz ({display_hz:g}): expect vsync judder"
        )
    if target_fps > engine_cap:
        warnings.append(
            f"target_fps ({target_fps:g}) exceeds engine_cap_fps ({engine_cap:g}): requires an engine FPS/delta patch"
        )

    m = cfg.get("measured", {})
    game_cpu = m.get("game_cpu_ms")
    replay = m.get("replay_ms")
    gpu = m.get("gpu_ms")
    gpu_nols = m.get("gpu_noloadstore_ms")
    gpu_sustained = m.get("sustained_gpu_ms")
    hwbuf_mbs = m.get("hwbuf_read_mbs")
    malloc_mbs = m.get("malloc_read_mbs")

    stages = {
        k: float(v)
        for k, v in (("game_cpu", game_cpu), ("replay", replay), ("gpu", gpu))
        if v is not None
    }
    serial_ms = round(sum(stages.values()), 2) if stages else None
    overlapped_ms = round(max(stages.values()), 2) if stages else None
    bottleneck = max(stages, key=stages.get) if stages else None

    if overlapped_ms is not None and overlapped_ms > budget_ms:
        warnings.append(
            f"overlapped critical path ({overlapped_ms:.2f} ms, {bottleneck}) exceeds {target_fps:g} fps budget ({budget_ms:.2f} ms)"
        )
    elif serial_ms is not None and serial_ms > budget_ms and overlapped_ms is not None and overlapped_ms <= budget_ms:
        warnings.append(
            f"serial frame ({serial_ms:.2f} ms) exceeds budget ({budget_ms:.2f} ms), but overlapped ({overlapped_ms:.2f} ms) fits: prioritize CPU/GPU overlap (CB splits, async/MT replay)"
        )

    shading_floor: Dict[str, Any] = {}
    if gpu is not None and gpu_nols is not None:
        ls_delta = round(float(gpu) - float(gpu_nols), 2)
        at_floor = ls_delta < 1.0
        shading_floor = {
            "gpu_ms": float(gpu),
            "gpu_noloadstore_ms": float(gpu_nols),
            "load_store_recoverable_ms": ls_delta,
            "at_shading_floor": at_floor,
        }
        if at_floor:
            warnings.append(
                f"GPU is at its shading floor (load/store drop saves only {ls_delta:.2f} ms): remaining GPU levers are driver choice, UBWC, or quality/resolution trades"
            )
        if float(gpu_nols) > budget_ms:
            warnings.append(
                f"GPU shading floor ({float(gpu_nols):.2f} ms) exceeds target budget ({budget_ms:.2f} ms): lossless {target_fps:g} fps is unreachable at current resolution/driver"
            )

    thermal: Dict[str, Any] = {}
    if gpu is not None and gpu_sustained is not None:
        drift_pct = round(((float(gpu_sustained) - float(gpu)) / float(gpu)) * 100.0, 1) if float(gpu) > 0 else 0.0
        thermal = {
            "burst_gpu_ms": float(gpu),
            "sustained_gpu_ms": float(gpu_sustained),
            "thermal_slowdown_pct": drift_pct,
            "sustained_fits_budget": float(gpu_sustained) <= budget_ms,
        }
        if float(gpu_sustained) > budget_ms:
            warnings.append(
                f"sustained GPU ({float(gpu_sustained):.2f} ms, +{drift_pct:.1f}% thermal slowdown) exceeds {target_fps:g} fps budget ({budget_ms:.2f} ms)"
            )

    bw: Dict[str, Any] = {}
    if hwbuf_mbs is not None and malloc_mbs is not None and float(malloc_mbs) > 0:
        bw_ratio = round(float(hwbuf_mbs) / float(malloc_mbs), 3)
        wc_trap = bw_ratio < 0.35
        bw = {
            "hwbuf_read_mbs": float(hwbuf_mbs),
            "malloc_read_mbs": float(malloc_mbs),
            "ratio": bw_ratio,
            "write_combined_trap": wc_trap,
        }
        if wc_trap:
            warnings.append(
                f"write-combined AHardwareBuffer reads detected ({hwbuf_mbs:g} vs {malloc_mbs:g} MB/s, {bw_ratio:.2f}x): enable lazy zero-copy and heap uniform shadows"
            )

    mem_cfg = cfg.get("memory", {})
    mem: Dict[str, Any] = {}
    if mem_cfg:
        idle_avail = mem_cfg.get("idle_available_mib")
        headroom = float(mem_cfg.get("lmk_headroom_mib", 1536))
        peak_rss = mem_cfg.get("peak_rss_mib")
        largest_pool = mem_cfg.get("largest_pool_mib")
        chunk_mib = float(mem_cfg.get("zc_chunk_mib", 256))
        gralloc_abort = mem_cfg.get("gralloc_abort_mib")

        if idle_avail is not None:
            usable = max(0.0, float(idle_avail) - headroom)
            mem["usable_ram_mib"] = round(usable, 1)
            if peak_rss is not None:
                margin = round(usable - float(peak_rss), 1)
                mem["peak_rss_mib"] = float(peak_rss)
                mem["lmk_margin_mib"] = margin
                if margin < 0:
                    warnings.append(
                        f"peak RSS ({peak_rss:g} MiB) exceeds safe RAM ceiling ({usable:.0f} MiB): high Android LMK SIGKILL risk; enable lazy zero-copy or lower pool retention"
                    )
        if largest_pool is not None and chunk_mib > 0:
            chunks = int(math.ceil(float(largest_pool) / chunk_mib))
            mem["largest_pool_mib"] = float(largest_pool)
            mem["zc_chunk_mib"] = chunk_mib
            mem["pool_chunks"] = chunks
            mem["cross_chunk_risk"] = chunks >= 2
            if chunks >= 2:
                warnings.append(
                    f"largest pool ({largest_pool:g} MiB) spans {chunks} chunks of {chunk_mib:g} MiB: backend must bridge cross-chunk vertex/index/indirect buffers"
                )
        if gralloc_abort is not None and chunk_mib >= float(gralloc_abort) * 0.8:
            warnings.append(
                f"zc_chunk_mib ({chunk_mib:g} MiB) is close to gralloc abort limit ({gralloc_abort:g} MiB)"
            )

    return {
        "game": cfg.get("game", "unknown"),
        "device": cfg.get("device", "unknown"),
        "display_hz": display_hz,
        "target_fps": target_fps,
        "engine_cap_fps": engine_cap,
        "clean_pacing_divisor": clean_divisor,
        "budget_ms": round(budget_ms, 2),
        "p95_safe_ms": round(p95_safe_ms, 2),
        "pacing_steps": divisors,
        "stages_ms": stages,
        "serial_ms": serial_ms,
        "overlapped_ms": overlapped_ms,
        "bottleneck": bottleneck,
        "overlapped_headroom_ms": round(budget_ms - overlapped_ms, 2) if overlapped_ms is not None else None,
        "shading_floor": shading_floor,
        "thermal": thermal,
        "memory_bandwidth": bw,
        "memory": mem,
        "warnings": warnings,
    }


def render_text(res: Dict[str, Any]) -> str:
    lines = [
        f"Game:   {res['game']}",
        f"Device: {res['device']}",
        f"Target: {res['target_fps']:g} fps on {res['display_hz']:g} Hz "
        f"(budget {res['budget_ms']:.2f} ms, p95 safe {res['p95_safe_ms']:.2f} ms, "
        f"clean divisor: {'yes' if res['clean_pacing_divisor'] else 'NO'})",
        "",
        "Clean display pacing steps:",
    ]
    for s in res["pacing_steps"]:
        mark = " <- target" if abs(s["fps"] - res["target_fps"]) < 0.5 else ""
        lines.append(
            f"  /{s['divisor']}  {s['fps']:6.1f} fps  budget {s['budget_ms']:6.2f} ms  (p95 safe {s['p95_safe_ms']:5.2f} ms){mark}"
        )

    if res["stages_ms"]:
        lines.append("")
        lines.append("Critical path (measured):")
        for k, v in res["stages_ms"].items():
            lines.append(f"  {k:12s} {v:6.2f} ms")
        lines.append(
            f"  serial sum   {res['serial_ms']:6.2f} ms  |  overlapped max {res['overlapped_ms']:6.2f} ms "
            f"(bottleneck: {res['bottleneck']}, headroom: {res['overlapped_headroom_ms']:+.2f} ms)"
        )

    if res["shading_floor"]:
        sf = res["shading_floor"]
        lines.append("")
        lines.append(
            f"GPU shading floor: {sf['gpu_noloadstore_ms']:.2f} ms "
            f"(load/store recoverable: {sf['load_store_recoverable_ms']:.2f} ms, "
            f"at floor: {'YES' if sf['at_shading_floor'] else 'no'})"
        )

    if res["thermal"]:
        th = res["thermal"]
        lines.append(
            f"Thermal sustained: burst {th['burst_gpu_ms']:.2f} ms -> sustained {th['sustained_gpu_ms']:.2f} ms "
            f"({th['thermal_slowdown_pct']:+.1f}%, fits budget: {'yes' if th['sustained_fits_budget'] else 'NO'})"
        )

    if res["memory_bandwidth"]:
        bw = res["memory_bandwidth"]
        lines.append(
            f"CPU pool read BW:  hwbuf {bw['hwbuf_read_mbs']:.0f} MB/s vs malloc {bw['malloc_read_mbs']:.0f} MB/s "
            f"(ratio {bw['ratio']:.2f}x, write-combined trap: {'YES' if bw['write_combined_trap'] else 'no'})"
        )

    if res["memory"]:
        mem = res["memory"]
        lines.append("")
        lines.append("Memory & zero-copy chunks:")
        if "usable_ram_mib" in mem:
            margin = mem.get("lmk_margin_mib")
            m_str = f", peak RSS {mem['peak_rss_mib']:.0f} MiB (LMK margin {margin:+.0f} MiB)" if margin is not None else ""
            lines.append(f"  usable RAM ceiling: {mem['usable_ram_mib']:.0f} MiB{m_str}")
        if "pool_chunks" in mem:
            lines.append(
                f"  largest pool: {mem['largest_pool_mib']:.0f} MiB / {mem['zc_chunk_mib']:.0f} MiB chunks = "
                f"{mem['pool_chunks']} chunks (cross-chunk risk: {'YES' if mem['cross_chunk_risk'] else 'no'})"
            )

    if res["warnings"]:
        lines.append("")
        lines.append("Ceiling alerts:")
        for w in res["warnings"]:
            lines.append(f"  [!] {w}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", type=Path, help="Path to device+game ceiling JSON")
    ap.add_argument("--json", action="store_true", help="Emit JSON output")
    args = ap.parse_args(argv)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    res = analyze(cfg)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(render_text(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
