---
name: hill-climbing
description: >
  Domain-agnostic meta-skill for Hill-Climbing Engineering: turning any fuzzy,
  noisy, continuous optimization or systems-engineering problem (inference speed,
  game ports, compiler/build speed, database/API latency, memory, shaders,
  accuracy-under-constraints) into a climbable mountain that any AI model can
  push toward its physical ceiling with evidence. Computes theoretical ceilings
  first, turns every defect into a countable metric, locks an uncheatable
  correctness-before-speed verifier, sweeps zero-code levers before editing
  source, ratchets every change through noise-controlled interleaved A/B runs
  (A->B->A or ABAB), persists state and dead ends on disk so runs are
  model-agnostic, detects plateaus honestly, and qualifies clean releases. Also
  includes meta-skill mode to distill a finished climb into a reusable
  domain-specific skill. Triggers: hill climb, hill-climbing, hill climbing
  engineering, optimize toward ceiling, squeeze performance, benchmark A/B,
  build a verifier, create a hill-climb skill, meta-skill.
license: MIT
compatibility: Python 3.9+ (standard library only); POSIX bash for queue.sh.
---

# Hill-Climbing Engineering (meta-skill)

> **Don't engineer the climber — engineer the hill.**

Loop engineering gets an agent to *passing tests* (binary). Graph engineering wires multiple loops together (topology). **Hill-climbing engineering** is what you use when "done" is a **continuous, noisy reality** — FPS, tok/s, latency, memory, accuracy, error counts — where measurements drift, averages hide regressions, and models will cheerfully celebrate noise as a win or quietly game their own checks.

Put the rigor in **files, ceilings, and a locked verifier**, not in clever prompts, and *any* model can climb the mountain — or pick up cold where a different model stopped.

This skill works in two modes:

- **Mode 1 — Run a climb (default):** build the hill, climb it with a strict noise-controlled ratchet, stop cleanly at the target or plateau, and qualify the result.
- **Mode 2 — Forge a domain skill (meta-skill):** after climbing a problem class, distill its ceilings, countable metrics, ranked levers, gotchas, and harness scripts into a permanent domain-specific skill (see `references/meta-skill-guide.md`).

Detailed references, read when needed:

- `references/building-the-hill.md`: Phase 1 deep dive — ceiling math, countable metrics, locked verifiers, multi-ladder ordering, and anti-cheating guards.
- `references/methodology.md`: noise sources, interleaved `A → B → A` and `ABAB` ratchets, sample sizing, guardrail budgets, and measurement traps.
- `references/meta-skill-guide.md`: turning a completed climb into a standalone reusable skill (following the pattern of `android-game-porting` and `inference-optimisation`).
- `scripts/climb.py`: zero-dependency Python 3.9+ harness (`init`, `lock-verifier`, `check-verifier`, `record`, `compare`, `compare-abab`, `plateau`, `show`).
- `scripts/queue.sh`: portable macOS/Linux lock-file queue that serializes exclusive-hardware runs.
- `examples/`: starter templates (`objective-template.md`, `plan-template.md`, `dead-ends-template.md`, `verify-template.sh`).

---

## The whole loop at a glance

```text
  [Phase 1: Build the hill]               [Phase 2: Climb the hill]
  vague goal → countable metric           pick cheapest lever → 1 change
             + physical ceiling                 ↑                 ↓
             + locked verifier          update ledger /       interleaved A/B
             + human sign-off           DEAD_ENDS / best  ←── keep or revert
                                                ↓
                                   3 tries in noise? → plateau → [Phase 3: Qualify clean]
```

---

## Phase 1 — Build the hill (before editing target code)

A bad verifier makes an agent climb the wrong mountain at full confidence. Never start climbing until the hill exists on disk and the user agrees on the finish line.

1. **Initialize the workspace** (`python3 scripts/climb.py init <work-dir>`):
   - `OBJECTIVE.md`: target metric, direction (`higher` or `lower`), finish line, hard correctness gates, guardrail tolerances, and whether lossy/quality trades are allowed (default: **no**).
   - `THEORY.md`: measured hardware/system inventory and theoretical ceilings.
   - `PLAN.md`: ranked lever backlog, experiment log, and `STATUS` block.
   - `DEAD_ENDS.md`: every rejected hypothesis, its exact numbers, and *why* it failed.
   - `ledger.jsonl`: append-only machine-readable record of every run.
   - `configs/`: one JSON/env file per experiment, derived from `best-config`.
   - `verify.sh`: single read-only command that runs correctness gates + benchmark and prints one JSON metrics object.

2. **Check prior art and dead ends first:** read any existing `PLAN.md`, `DEAD_ENDS.md`, upstream issues, or git history. Never rerun a rejected idea without a concrete new hypothesis.

3. **Compute the ceiling first (`THEORY.md`):**
   - Measure the real machine — never trust spec sheets alone (actual memory bandwidth, actual compute/IO floor, real clock under sustained load, real available RAM).
   - Derive the **physical or theoretical ceiling** for the target metric (e.g., `bandwidth ÷ bytes_per_op`, frame budget `1000 / target_fps` vs. irreducible floor, Amdahl's law bound).
   - Report progress as **% of ceiling**. If the user's target sits past the physical ceiling, show the arithmetic immediately before spending hours climbing.

4. **Make every defect countable:**
   - Replace vague descriptions (*"feels janky"*, *"occasionally breaks"*, *"too slow"*) with exact counts (`hitches > 1.2× budget`, `non-finite pixels`, `failed assertions`, `p95 ms`, `tok/s`).
   - Prefer tail/count metrics over averages when hunting glitches or dips — medians often stay flat while hitches drop 50×.

5. **Lock the verifier (`python3 scripts/climb.py lock-verifier <work-dir> <files...>`):**
   - The verifier must check **correctness before speed** (exact/bitwise equivalence, tolerance checks, invariants, retrieval needles, visual/save integrity).
   - Lock the verifier scripts, benchmark inputs, and reference baselines with SHA-256 hashes (`.verifier.sha256`). Run `check-verifier` before recording any result.
   - **Hard rule:** the climbing agent may *run* the verifier, never *edit* it (or its fixtures/tolerances) to make a score look better. If the verifier itself has a bug, pause, document why, get human approval, re-lock, and re-baseline.

6. **Freeze a baseline and measure the noise floor:**
   - Run the unmodified baseline multiple times in fixed conditions to learn its natural run-to-run spread (often ±3–10%, up to ±20% on thermally throttling hardware). That spread sets your minimum acceptance threshold.

---

## Ground rules (non-negotiable)

1. **Correctness before speed (Two-ladder rule).** Never optimize speed on a broken output, failing invariant, or unstable crash. Clear **Ladder A (correctness)** completely before entering **Ladder B (performance)**. Any speed candidate that fails a correctness gate is immediately rejected.
2. **Zero-code levers before source edits.** Exhaust runtime flags, config switches, driver/compiler flags, and environment knobs one at a time before touching source code or writing custom kernels.
3. **One change per experiment.** Derive every candidate from the current `best-config` with one isolated change. Bundling three changes hides which one helped and which one silently regressed.
4. **Control for noise with interleaved runs.** Single before/after measurements lie due to thermal drift, clock ramp, caching, and background load.
   - Keep the environment fixed (AC power, fixed performance profile, idle host, serialized via `scripts/queue.sh`, discard cold warm-up samples).
   - Bracket every candidate between controls: **`ctl-a → candidate → ctl-b`** (or same-process live toggles **`on → off → on → off`** / **`ABAB`**).
   - Void the pair if the two controls drifted past the drift cap (default 10%), or if environment state changed mid-run.
5. **Strict ratchet.** Accept a candidate *only* if:
   - verifier integrity check passed;
   - all hard correctness gates passed;
   - candidate beats **both** neighboring controls (or all interleaved legs) by at least the noise margin;
   - no guardrail metric (tail latency, secondary throughput, memory cap, thermals, quality) regressed beyond tolerance (default ~3%).
   - Otherwise **revert cleanly** — no matter how elegant the patch looks.
6. **Verify suspiciously big wins.** A massive sudden speedup is usually a broken measurement, a cache hit, skipped work, dropped output, or a looping/degenerate response. Audit the output before celebrating.
7. **State lives on disk, not in chat history.** Update `ledger.jsonl`, `PLAN.md`, `best-config`, and `DEAD_ENDS.md` after every experiment. A fresh session — or a completely different AI model — must be able to read those files cold and resume without repeating a single dead end.
8. **Respect user state and shared hardware.** Back up user data before testing, never interrupt active user workloads without asking, never ship quality/lossy trades silently, and restore every temporary system/device setting at the end.
9. **Never leave secrets in logs or repos.** Never print or commit credentials, keys, tokens, or personal paths.

---

## Phase 2 — Climb the hill

Repeat until the finish line is reached, the budget/timebox expires, or a plateau is declared:

1. **Profile the current best** to identify the dominant bottleneck (CPU vs. GPU vs. memory bandwidth vs. I/O vs. lock contention vs. algorithmic work). Never optimize a stage that owns 5% of total time.
2. **Pick the next lever** with the highest `(ceiling gap × likelihood) ÷ engineering cost`, working strictly from cheapest to most invasive:
   1. Persistent caches & obvious misconfigurations
   2. Zero-code runtime/flag/environment sweeps
   3. Algorithmic / structural / batching / allocation fixes in the dominant stage
   4. Concurrency, overlap, and synchronization fixes
   5. Low-level hot-path / kernel / assembly work (only after higher layers plateau)
   6. Labelled quality/lossy trades (**explicit user consent required**)
3. **Run the controlled A/B** (serialized with `scripts/queue.sh` when touching shared hardware).
4. **Record and judge** with `scripts/climb.py record` and `scripts/climb.py compare` (or `compare-abab`).
5. **Ratchet:**
   - **ACCEPT:** commit or save new `best-config`, update `PLAN.md`, and re-profile if the dominant bottleneck shifted.
   - **REJECT / VOID:** revert code/config immediately, append the exact numbers and root reason to `DEAD_ENDS.md`, and move to the next hypothesis.

### When a lever surprises you

- **0% gain on an expected win:** you optimized a non-bottleneck stage (e.g., CPU work when GPU-bound, or kernel fusion inside an already-bandwidth-saturated loop). Re-profile before guessing again.
- **Better proxy metric, worse real metric:** never optimize a proxy blindly (e.g., higher draft acceptance that slows step time, or lower median latency that spikes p99). Always judge by the primary objective + guardrails.
- **Heisenbug (defect disappears under profiling/logging):** the diagnostic tool added sync, memory barriers, or timing shifts. Isolate each side effect one at a time — it usually points straight at the root cause.

### Declaring a plateau

Run `python3 scripts/climb.py plateau <work-dir>/ledger.jsonl --n 3`. Declare a **plateau** when **3 consecutive independent candidates** land inside noise or measurements prove you are at the physical ceiling.

Write a `STATUS` section in `PLAN.md` covering:

- baseline vs. best vs. ceiling table (% of ceiling reached);
- hard evidence for the limiting bottleneck;
- ranked list of remaining levers (including out-of-scope or quality-trade options) with estimated gain and cost.

---

## Phase 3 — Qualify and ship clean

A hill-climb isn't finished on a dirty dev tree with diagnostic flags still turned on.

1. **Rebuild from a clean, pinned checkout** (no dirty files, no debug hooks, pinned dependencies/commits).
2. **Run full acceptance & soak gates** (unit/integration tests, cross-workload regression check, long-run/thermal/memory soak, output equivalence).
3. **Final clean A/B** proving the clean release build matches the winning development build.
4. **Restore the environment** (power/fan profiles, temp files, user data verified intact).
5. **Report honestly to the user:**
   - Start with a **summary table**: metric, baseline, final, physical ceiling (% of ceiling), and exact measurement conditions/harness.
   - Explain **what actually drove the gains**, **what failed** (the top dead ends), **why it stopped here**, and the exact repo/system state left behind.
   - State plainly **what was NOT verified** (untested hardware, workloads, long soaks, edge cases).

---

## Mode 2 — Forge a domain-specific skill (meta-skill mode)

When the user asks to turn a completed climb (or problem domain) into a reusable skill, follow `references/meta-skill-guide.md`:

1. Keep the universal skeleton (`ceilings → Ladder A/B → ground rules → loop → plateau → qualify → report`).
2. Replace generic placeholders with domain-specific **ceiling formulas**, **countable bug classes**, **ranked lever ladders tagged with real measured evidence**, **gotchas found the hard way**, and **zero-dependency scripts + offline tests**.
3. Ship one worked end-to-end case study from a real climb with every number included.
