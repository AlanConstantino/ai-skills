---
name: android-game-porting
description: >
  Hill-climb a native Android game port from raw console/PC dump to a clean,
  full-speed, stable APK on any Android device (Snapdragon/Adreno with Turnip
  or Qualcomm drivers, Mali, Xclipse, PowerVR; handhelds, tablets, phones) and
  any native porting stack (nx2android/nxnative static recompilation, HLE/Vulkan
  replay, custom native wrappers). Runs a strict two-ladder loop — correctness
  before speed — computing frame-time, memory, and thermal ceilings first,
  turning every crash, visual defect, or audio glitch into a countable metric,
  bisecting with zero-build toggles before touching code, running noise-controlled
  A/B experiments into a ledger, and qualifying the release build before shipping.
  Use when asked to port a game natively to Android, bring up a new title/profile,
  debug black screens/curtains/NaNs/flicker/missing geometry/crashes/audio bugs,
  or hill-climb FPS, frame pacing, startup, and thermals on an Android device.
  Triggers: hill climb, port game, native android port, nx2android, nxnative,
  Turnip, Adreno, Vulkan, black screen, black curtain, NaN, flicker, LRZ,
  frame pacing, 60fps, soak test, shader translation, APK.
license: MIT
compatibility: Python 3.9+ (standard library only); ADB for live Android device telemetry.
---

# Android game porting (hill-climbing a native port)

You take a game running natively on Android from unbooted dump or glitchy bring-up to a clean, full-speed, qualified APK — with evidence at every step.

Porting is a **two-ladder hill-climb** (strictly ordered — never tune FPS on a broken picture):

1. **Inventory & preflight** the game, porting stack, device, and user saves.
2. **Compute ceilings first** (`scripts/budget.py`): frame budgets, engine rate cap, CPU/gralloc bandwidth, GPU shading floor, RAM/chunk caps, thermal floor.
3. **Climb Ladder A (Bring-up & Correctness):** turn each crash, rendering defect, or audio/input bug into a **countable metric**, bisect with zero-build toggles first, isolate the semantic root cause, fix it cleanly in the right layer, and prove it with a reversible A/B.
4. **Climb Ladder B (Performance, Pacing & Thermals):** change one lever at a time, measure against controls in the same scene at fixed device state, record in `ledger.jsonl` and `PLAN.md`, keep or reject with numbers.
5. **Repeat** until target is met or plateau is reached, then **qualify and ship** a clean release build with zero diagnostic leftovers.

Detailed references, read when needed:

- `references/ceilings.md`: frame-time budgets, CPU/gralloc bandwidth, GPU shading floor, RAM/chunk limits, thermal ceiling, and worked examples.
- `references/methodology.md`: countable metrics, noise control, zero-build toggle bisection, raw HDR `.f32` forensics, Heisenbug isolation, visual-oracle rules.
- `references/levers.md`: ranked correctness bug classes and performance levers, tagged `[G]` general vs `[S: stack/GPU]`, with real measured evidence.
- `references/gotchas.md`: Android, Adreno/Turnip, Vulkan, ADB, save-safety, and CI traps found the hard way.
- `references/qualification.md`: profile-vs-core rules, clean release gates, cross-game regression checks, soak tests, and publishing hygiene.
- `scripts/`: stdlib-only harness — frame/memory ceiling calculator (`budget.py`), ADB thermal/CPU/RAM sampler (`device_health.sh`), experiment & ledger runner (`probe.py`), raw float32/HDR NaN auditor (`f32_audit.py`), and portable single-device queue (`queue.sh`).

## 0. Before touching anything

- **Write down the user's targets and constraints.** Ask only if a missing decision changes what you do:
  - target device(s), preferred Vulkan driver (e.g. bundled Turnip vs system driver), and display refresh mode;
  - target FPS / pacing (e.g. 30, 40 @ 120 Hz, 60) and whether quality trades (render scale, dynamic resolution, reduced effects) are allowed;
  - whether changes should be upstreamable to shared core or stay local/personal;
  - exact paths to game dumps, updates/DLC, keys/firmware (never copy or print key values).
- **Check prior art first:** inspect existing profiles, upstream issues/PRs, sibling forks, and any existing `PLAN.md` or notes for this game or engine. Never rerun a rejected lever without a new hypothesis.
- **Inventory the device** without disturbing anything running (`scripts/probe.py inventory` or `scripts/device_health.sh`):
  - SoC, GPU, driver, Android version, display modes (`60 / 120 Hz`), RAM (`MemAvailable`), thermal zones, CPU cluster caps, battery/AC state, installed packages.
- **Back up user saves immediately** if the package is already installed (`scripts/probe.py backup-saves`).
- **Create the working directory** (keep big dumps/APKs in a single git-ignored dump folder — never scatter multi-GB artifacts into temp scratchpads):
  - `PLAN.md`: timestamped experiment & bug log with numbers, verdict, and reason (`examples/plan-template.md`).
  - `THEORY.md`: hardware & frame-budget ceilings.
  - `ledger.jsonl`: one JSON line per run from `scripts/probe.py`.
  - `configs/`: one JSON per experiment run.
  - `best-config.json`: current winning config/env switches.

## 1. Ceilings first

Compute the practical ceilings before tuning (`scripts/budget.py`, `references/ceilings.md`):

- **Display pacing grid:** only integer divisors of refresh rate pace cleanly without judder (on 120 Hz: `120 / 60 / 40 / 30 fps` → `8.33 / 16.67 / 25.00 / 33.33 ms`; on 60 Hz: `60 / 30 fps`).
- **Engine rate cap:** check whether the game steps at a fixed 30 fps, runs a dynamic interval controller, or scales smoothly to 60 fps (or needs a per-build tick/cut patch).
- **CPU replay & memory bandwidth floor:** measure host `malloc` vs GPU/gralloc `AHardwareBuffer` CPU read speed. On devices that map hardware buffers write-combined (~1.7 GB/s vs ~18 GB/s cached), reading command/vertex buffers on CPU caps replay until lazy zero-copy or heap shadows are enabled.
- **GPU shading floor:** measure `GPU busy` at a **pinned FPS** (with early submit-splitting disabled if measuring raw GPU span), then run a load/store A/B. If dropping colour/depth loads and stores saves `< 1 ms`, the remaining cost is the game's own shading — only driver/UBWC improvements or quality trades (resolution/precision) can lower it further.
- **Memory & zero-copy chunk ceiling:** `available_ram - os_lmk_headroom` caps total resident pools (Android's LMK sends silent `SIGKILL`). Large pools split across `ZC_CHUNK` (often 256 MiB) risk cross-chunk vertex/index/indirect buffer straddling.
- **Thermal sustained ceiling:** burst turbo speed is not playable speed. Measure sustained clocks and frame times after 5–10 min of gameplay.

If the user's FPS target sits above the measured shading or thermal ceiling, say so early with the arithmetic.

## 2. Ground rules (non-negotiable)

1. **Correctness before speed.** Never chase FPS while frames have NaNs, black curtains, missing geometry, VUIDs, or crashes. Fast garbage is a broken port.
2. **Zero-build toggles before recompiling.** Exhaust runtime switches (`env.txt` / live `ctl.txt` / driver swap) one at a time before rebuilding APKs or editing core C/C++/shader code.
3. **One change per experiment.** Derive each candidate config from the current best and record it in `configs/`.
4. **Make every bug countable first.** Turn "it flickers" or "there's a black curtain" into a number (`58/118 frames flickering`, `142,310 NaN channels in pass 30`, `21.4% dark pixels in ROI`, `14 VUIDs`).
5. **Control for noise:**
   - Measure in the **same scene, same save, same camera angle, same power/fan/thermal profile, and same pinned FPS** (GPU clocks scale with FPS).
   - Prefer **same-run live toggles** (`orig → cand → orig`) when a switch changes live; otherwise interleave **control → candidate → control** (`scripts/probe.py compare`).
   - Accept a speed win only if the candidate beats **both** neighbouring controls on the target metric without regressing p95/late frames, correctness, or thermals by more than ~3%.
6. **Respect the shared device and user saves:**
   - Always check `adb shell pidof <pkg>` before force-stopping, reinstalling, or pushing files. Never interrupt a running game the user is playing without asking.
   - **Never press Confirm (`A`) blindly** in a user's save — blind confirm inputs advance story state and overwrite saves. Use a verified gameplay detector or stick-only soak loops (`soak`).
   - **No unattended screenshot-scoring loops.** When a human is holding the device, use them as the visual oracle; whenever a screenshot is captured, inspect the actual image yourself rather than trusting a heuristic score.
   - Install test builds under a separate test package when possible, or confirm before overwriting the user's install, and preserve their saves.
7. **Semantic fixes, never hacks:**
   - Never ship global NaN clamps, forced clears, draw skips, or per-game `if (title_id == ...)` branches in shared runtime code. Put game-specific data in the game profile and general API/hardware rules in the shared core.
   - **Watch for Heisenbugs:** if a pass dump, pixel history, or profiler makes a defect vanish, stop trusting it as an untouched capture — isolate each barrier/copy/sync the tool inserted to find the real bug.
8. **Run the Vulkan validation layer before theorizing** on driver refusals, `VK_ERROR_*`, GPU hangs, or silent draw drops.
9. **Monitor real RAM and real thermals.** Read `/proc/<pid>/status` + `/proc/meminfo` and multi-zone `/sys/class/thermal/thermal_zone*/temp` (`scripts/device_health.sh`) — never trust cached `dumpsys thermalservice` or frozen handheld KGSL counters.
10. **Log everything and keep secrets out.** Record every failed hypothesis in `PLAN.md`. Never commit keys, firmware, ROMs, personal paths, or diagnostic `env.txt` leftovers.

## 3. The loop

```
inventory + ceilings → countable baseline → pick highest (impact × likelihood) lever
        ↑                                                                      ↓
        └── update best-config + PLAN.md + ledger ←─ accept / reject ←─ controlled A/B
```

### Ladder A: Bring-up & Correctness (cheapest probes first)

1. **Preflight & boot (`experimental → boots`):** inspect binary/graphics API/archive format/shader coverage; run experimental port; read the **first** fatal crash or `[RED]` stub (not the 50th cascade). Fix missing HLE/API semantics, memory/ASLR/stack layout, or RomFS/shader extraction.
2. **First frames & validation (`boots → first-frames`):** run Vulkan validation layer once; check missing shaders (`captured_shaders/`), clip-space Y / winding (`FLIPY`, `NOCULL`, `FRONT`), sRGB twin views, CPU-filled textures, and storage buffer bindings.
3. **Visual / rendering defect bisection:**
   - **Step 1 (zero-build):** toggle driver (Turnip vs system), CB splits (`CB_DRAWS=0`), LRZ touches, `FDZ`, `UBWC`, `ASYNC_PIPE`, `GS_SPLIT`, `NVN_ASYNC`/`MT_REPLAY` one at a time.
   - **Step 2 (locate pass):** list passes + dump colour/depth; for HDR/bloom blocks or curtains, dump **raw float32** (never clipped 8-bit PPM alone) and run `scripts/f32_audit.py`.
   - **Step 3 (locate draw & input):** run single-pixel draw/depth history, pass draw traces, vertex/UBO/texture-binding dumps, and reversible live shader overrides (`orig → cand → orig`). Check for cross-chunk pool boundaries, GPU-to-CPU write-backs, same-pass feedback, and Adreno binning/LRZ hazards (`references/levers.md`).
4. **Audio, input & saves:** dump raw PCM to count repeats/clicks/gaps separately from underruns; verify controller, touch, overlay, gyro/accel fusion, and save/load round-trip.

### Ladder B: Performance, Pacing & Thermals (only after Ladder A is clean)

Order by typical payoff ÷ engineering cost (`references/levers.md`):

1. **Driver & zero-build runtime switches:** Turnip vs Qualcomm driver, `UBWC` format lists (often −25–40% GPU), `DM_GPU_TURBO=need/auto`, `DM_VK_MT_REPLAY=1`, `DM_NVN_ASYNC=1`, `DM_VK_CB_DRAWS` (respecting LRZ cut rules), `DM_VK_ASYNC_PIPE=1`.
2. **Frame overlap & sync waits:** trace render-thread `SyncWait` / submission fences so CPU replay overlaps GPU execution instead of serializing.
3. **Pass, descriptor & memory-pool overhead:** defer in-pass uniform updates (with heap shadows), keep passes across identical targets (never across write→read barriers), dynamic UBO offsets + descriptor/view reuse, pool slabs & reuse, lazy zero-copy on slow-read hardware-buffer devices, SVC lock spin-before-park.
4. **Frame pacing, ADPF & engine rate cuts:** pin FPS directly (`DM_FPS_PIN`) to separate governor bugs from real load; register busiest guest threads in ADPF; match window rate to game rate; fix frame-clock overshoot drift; apply clean build-keyed 60 fps cuts where the engine supports them.
5. **Media & CPU hotspots:** NEON video decode/upload, profile replay/host threads only (never SIGPROF guest code mid-load).
6. **Labelled quality trades (explicit user consent required):** static or dynamic render scale, shader precision, mip bias. Never apply silently.

### When a lever fails

- **If a code or sync change gives 0 ms gain:** check which stage actually owns the frame (`replay` vs `game CPU` vs `GPU` vs `sync wait`). Never optimize CPU replay when GPU-bound or vice versa.
- **If a visual fix breaks under restart:** verify PID, shader bundle regeneration (APK-only updates keep old shader bundles!), and cleared pipeline cache (`DM_VK_NOPCACHE=1`).
- **If a "win" looks too big:** check whether FPS stepped down (lowering GPU load), camera/scene shifted, shader draws were silently skipped, or diagnostic switches were left on.

### Plateau

Declare a plateau when **three consecutive independent levers** land within noise (~3%) or hit a measured hardware/shading ceiling. Write a `STATUS` section in `PLAN.md` with:

- baseline vs best vs ceiling table;
- evidence for the limiting bottleneck (e.g. load/store A/B floor, thermal steady-state clock, single-core guest logic);
- ranked list of remaining levers (including quality trades) with estimated gain and cost.

## 4. Qualify and ship

See `references/qualification.md`. Before handing over or opening a PR:

- Build from a **clean checkout / pinned commit** (never a dirty tree); ensure unit tests and CI pass across all platforms (including host C harnesses and Android NDK debug + release).
- Run a **cross-game regression check** whenever shared core code changed.
- Install the **release APK** (debug builds expose control hooks and carry overhead), wipe temporary pass dumps/overrides, and verify a **clean `env.txt`** (only shipping profile switches).
- Confirm **user saves are intact**, run multi-scene gameplay + save/load round-trip + a **30-minute soak** (same PID, 0 new `[RED]` lines, stable thermals, 0 VUIDs in a validation check).
- Keep personal fork remotes separate from upstream PR worktrees, and delete temporary multi-GB `.gamedump/` folders only after confirming hard-link count ≥ 2.

## 5. Reporting to the user

- **Start with a table:** metric (`status`, `frame p50/p95`, `GPU p50`, `replay p50`, `late/300`, `VUIDs`, `[RED]`, `NaN/flicker`, `RSS`, `temp`), baseline, now, ceiling/budget, and exact measurement conditions (device, driver, game version, scene, release vs debug).
- **Then:** root causes fixed (with commit hashes & links), what drove performance gains, why it stops here, known quirks, and exact **device/repo state left behind** (installed package & `versionCode`, `env.txt` contents, save status, active branches, remaining disk folders).
- **State plainly** what was **not** verified (untested chapters, other GPUs/drivers, long-session soak, Apple/other targets).
