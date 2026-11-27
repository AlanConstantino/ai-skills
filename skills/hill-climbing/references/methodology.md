# Measurement Methodology & Noise Control

Most "wins" and "regressions" in real-world optimization are measurement errors. Every rule below exists because skipping it causes false acceptances or wasted hours.

---

## 1. Know your noise floor

Real machines drift by **±3–10%** on desktops/servers and **±8–20%** on laptops and mobile devices. Common confounders:

- **Thermal drift & DVFS clock ramp:** chips start cool at boost clocks and throttle 2–5 minutes later; clocks also take seconds to ramp up after idle. Always discard warm-up runs and track temperatures/clocks before and after every run.
- **Power & scheduler profiles:** battery vs. AC power, balanced vs. performance governors, or light background CPU work can move throughput 10–30%. Pin the environment and void the run if it changed.
- **Caching illusions:** cold page cache vs. warm cache, prefix cache hits, JIT/shader pipeline caches, or OS disk buffers can fake a 2–5× speedup. Measure cold and warm explicitly and separately.
- **Workload drift:** changing prompts, camera angles, random seeds, or input sizes turns comparisons into pure noise. Use a **fixed, seeded panel** or **fixed repeatable route**.
- **Shared hardware contention:** never run two experiments simultaneously on the same CPU/GPU/device. Serialize runs with `scripts/queue.sh`.

---

## 2. Bracket every experiment (never trust bare before/after)

A single `before → after` pair cannot tell a real +4% win from a −5% thermal drift an hour apart. Use one of two controlled designs:

### Design A — Bracketed triplet (`ctl-a → candidate → ctl-b`)
Use when switching requires a rebuild, restart, or process launch.
- Run control (`ctl-a`), immediately run candidate (`cand`), immediately run control again (`ctl-b`).
- **Control drift check:** if `|ctl-b − ctl-a| / ctl-a > max_drift` (default `10%`), **VOID** the triplet — the machine drifted mid-test.
- **Ratchet rule:** accept `cand` *only* if it beats **both** `ctl-a` and `ctl-b` by at least `min_gain_pct` (default `3%`, or your measured noise floor) AND regresses no guardrail metric past tolerance.

### Design B — Interleaved multi-leg (`A → B → A → B` or `A → B → B → A`)
Use when you can toggle live inside one process (same PID, same cache, same memory layout) or run quick back-to-back legs.
- Same-run live toggles eliminate startup, shader-cache, and allocation variance completely.
- `ABAB` or `ABBA` cancels monotonic thermal drift (warming up across the session affects both arms symmetrically).
- Accept *only* if `B` beats its neighboring `A` in **every** paired leg (or clears the aggregate median gain threshold with zero losing legs) and breaks no guardrails.

---

## 3. Samples, medians, and tails

- **Never judge on a single sample.** Take multiple samples per leg (e.g., `≥ 5–6` decode runs, or `≥ 60–90 s` continuous windows) and summarize with the **median**, not the mean (a single GC pause or OS interrupt wrecks a mean).
- **Guard the tails separately.** Track `p95` / `p99` or hitch counts as guardrails alongside your primary median metric. A lever that improves median by 5% while doubling `p99` spikes is usually a net loss.
- **Repeat surprising wins.** If a tiny change claims a +20% win, rerun the full bracket before keeping it.

---

## 4. Guardrails and secondary metrics

A hill-climb optimizes one **primary objective**, bounded by explicit **guardrails** so gains aren't stolen from somewhere else:

- Example (LLM serving): primary = `decode tok/s` (`higher`), guardrails = `prefill tok/s` (no drop > 3%), `sampled T=0.6 tok/s` (no drop > 3%), `peak RSS` (under hard cap), `logit/needle gate` (100% pass).
- Example (game porting): primary = `frame p50 / hitches` (`lower`), guardrails = `frame p95` (no regression > 3%), `late frames`, `VUIDs == 0`, `NaN pixels == 0`, `SoC temp` (no spike past cap).

Never let a candidate pass by winning on one narrow slice (e.g., greedy coding prompts) while quietly tanking another (e.g., sampled prose).

---

## 5. Lever ordering (cheapest first)

Always search from lowest engineering cost to highest:

1. **Zero-code runtime/config/env sweeps:** takes minutes, zero code risk, often confirms whether defaults already dominate.
2. **Algorithmic / data-flow / redundant-work elimination:** stop doing wasted work (redundant uploads, unnecessary copies, O(N²) loops, unbatched passes).
3. **Overlap, caching, and scheduling:** hide latency by overlapping independent stages, reusing allocations/descriptors, persisting compiled artifacts.
4. **Hot-path / kernel / SIMD / ISA tuning:** only after profiling proves the outer layers are clean and one dominant kernel owns the cycle.
5. **Lossy / quality trade-offs:** strictly opt-in, explicitly labelled, never slipped in as a "free speedup."

---

## 6. Common measurement traps

- **Counting the wrong unit:** counting streamed SSE chunks instead of tokenized tokens; comparing debug builds against release builds; comparing runs with profiler timestamps on against runs without them.
- **Hidden work shifts:** a "faster GPU time" caused by FPS dropping (letting the GPU downclock) or draws being silently culled; a "faster decode" caused by a model looping a trivial numbered list or truncating early.
- **Stale or lying counters:** cached OS thermal services, frozen GPU utilization counters, or cgroup memory stats that miss unified-memory GPU allocations. Cross-check counters against wall-clock behavior.
- **Heisenbugs:** adding instrumentation changes barriers, timing, or memory layout and makes a bug vanish. When a bug vanishes under a probe, treat that as a clue and isolate each side effect the probe introduced.
