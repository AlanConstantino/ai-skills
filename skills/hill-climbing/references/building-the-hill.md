# Phase 1: Building the Hill

Most failed optimization runs don't fail because the AI model wasn't smart enough. They fail because nobody built a climbable hill before telling the model to "make it faster" or "fix it."

A climbable hill has five pieces: a **physical ceiling**, a **countable metric**, **two ordered ladders**, a **locked verifier**, and **disk-persisted state**.

---

## 1. Compute the physical ceiling first (`THEORY.md`)

Optimization without a ceiling is wandering in fog. Before changing code, calculate the hard upper bound dictated by physics or architecture:

| Domain | Typical physical ceiling |
|---|---|
| LLM decode | `measured_memory_bandwidth / bytes_read_per_token` |
| LLM prefill / GEMM | `0.5 × measured_peak_FLOPS / FLOPs_per_token` |
| Real-time rendering / games | `1000 / target_fps` vs. irreducible shading floor (load/store A/B) and sustained thermal clock |
| Database / search queries | `index_or_table_bytes / sequential_storage_or_RAM_bandwidth` + irreducible network RTT |
| Compilers / builds | Critical-path DAG length ÷ available cores + disk read floor |
| Compression / codecs | Entropy floor or SIMD throughput limit per byte |

**Rules for ceilings:**

- **Measure the real machine**, never trust marketing spec sheets. Run a 2-second memory copy, GEMM, disk read, or steady-state thermal check to get actual numbers.
- **Report every run as `% of practical ceiling`.** Going from `30% → 78%` of ceiling is a clear win; going from `92% → 94%` tells you you're nearly out of headroom.
- **Tell the user early if their target sits above the ceiling.** Show the arithmetic before spending hours chasing an impossible number.

---

## 2. Make every problem countable

Never climb a vibe (*"feels smoother"*, *"less janky"*, *"cleaner output"*, *"faster startup"*). Convert every symptom into an objective number before editing anything:

- **Latency dips / stutters:** count of frames or requests exceeding `1.2× budget` per fixed window (not just mean or median!).
- **Rendering / numerical glitches:** count of `NaN`/`Inf` channels in raw float buffers, count of flickering frames per 300, or dark-pixel % inside a fixed ROI.
- **Correctness / compatibility:** `passing_cases / total_cases`, max logit delta, bitwise match boolean, or zero validation errors.
- **Resource leaks / pressure:** peak resident memory (including GPU/driver allocations on unified-memory hardware, which bypass cgroups!), thermal steady-state °C.

**Watch out for deceptive averages:** a fix that cuts 90-second combat hitches from `75 → 1` can leave median frame time completely unchanged (`33.4 ms → 33.4 ms`). Pick the metric that actually captures the user's pain.

---

## 3. Enforce the two-ladder rule

Never optimize speed on top of broken behavior. Split work into two strictly ordered ladders:

- **Ladder A — Bring-up & Correctness:** crashes, wrong outputs, visual/numerical defects, missing semantics, validation errors. Every defect becomes a countable metric and drops to `0` (or passes its exact oracle) before speed tuning starts.
- **Ladder B — Performance, Pacing & Resources:** throughput, latency, tail hitches, memory, startup, thermals — always gated by Ladder A so no speed patch is ever allowed to break correctness.

If a Ladder B experiment introduces even one Ladder A regression, **revert it immediately**.

---

## 4. Lock an uncheatable verifier

Models optimize whatever score you hand them — including accidentally (or creatively) gaming the test. Guard against reward hacking with four rules:

1. **Separate the grader from the climber.** Verification lives in a script (`verify.sh`) or independent evaluator, not in the agent's self-assessment.
2. **SHA-256 lock the verifier and fixtures.** Run `climb.py lock-verifier <dir> verify.sh <fixtures...>` once the user approves the hill. Check `.verifier.sha256` before accepting any run. The climber may *read* and *run* the verifier, never edit it.
3. **Hold out hidden checks when practical.** Keep at least one prompt panel, scene, or test slice out of the agent's inner tuning loop and run it at qualification time to catch overfitting.
4. **Sanity-check outputs, not just scores.** Check for degenerate outputs (empty responses, repeated loops, skipped work, dropped draw calls, silent precision cuts). A suspiciously massive speedup is almost always a broken verifier.

---

## 5. Put state on disk so the climb is model-agnostic

Chat context compacts, sessions crash, and models get swapped. Put every piece of climb memory into plain files inside the working directory:

- `OBJECTIVE.md` — target metric, direction, finish line, hard gates, allowed/forbidden trades.
- `THEORY.md` — hardware inventory and ceiling arithmetic.
- `PLAN.md` — ranked lever queue, human-readable experiment log, and `STATUS` summary.
- `DEAD_ENDS.md` — every rejected lever, its numbers, and the exact reason it failed.
- `ledger.jsonl` — append-only machine-readable log (one JSON object per run).
- `configs/` + `best-config` — reproducible config snapshot for every single run.

**Why `DEAD_ENDS.md` matters most:** positive wins are already in git / `best-config`. Without a dead-ends log, the next session — or a fresh model — will happily re-try the same obvious-looking ideas that already failed.
