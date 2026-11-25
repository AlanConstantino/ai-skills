# Computing frame budgets and hardware ceilings

Measure the device and scene; never trust SoC spec sheets or burst FPS numbers.
Before turning levers, establish the five hard ceilings below with `scripts/budget.py`.

## 1. Display pacing grid

Frames only pace smoothly without judder at integer divisors of the active display refresh rate:

```text
budget_ms(fps) = 1000.0 / fps
valid_steps(refresh_hz) = { refresh_hz / k : k ∈ {1, 2, 3, 4} }
```

| Display refresh | Clean pacing steps (fps) | Per-frame budgets (ms) | Notes |
|---:|---|---|---|
| 60 Hz | 60, 30, 20 | 16.67, 33.33, 50.00 | 40 fps judders on 60 Hz |
| 90 Hz | 90, 45, 30 | 11.11, 22.22, 33.33 | 60 fps judders on 90 Hz |
| 120 Hz | 120, 60, 40, 30 | 8.33, 16.67, 25.00, 33.33 | **40 fps (25 ms)** is a huge sweet spot for heavy 3D games |
| 144 Hz | 144, 72, 48, 36 | 6.94, 13.89, 20.83, 27.78 | 60 fps judders unless display switches to 120/60 Hz |

Leave **10–15% p95 headroom** for vsync jitter and driver variance: a steady 60 fps target needs p50 ≤ ~14.5 ms and p95 ≤ ~16.0 ms; a steady 40 fps target needs p50 ≤ ~22 ms and p95 ≤ ~24 ms.

## 2. Game engine rate cap

Before chasing 60 fps, check what the game binary itself asks for:

- **Fixed-timestep 30 fps games** (e.g. physics or animation tied to 1/30 s) run in double-speed or slow-motion unless patched with a build-specific FPS/delta cut.
- **Dynamic interval controllers** (e.g. Nintendo `sead`) automatically step from interval 1 (60) to interval 2 (30) when a single frame's `main-loop + GPU fence wait` overshoots. If the game toggles interval 1↔2 constantly, shortening `replay + GPU tail` unlocks 60 fps without touching game logic.
- **Variable-delta 60-presenting games** (presenting at 60 while stepping by elapsed time) can hold 30/40/60 cleanly through host pacing alone.

## 3. CPU replay and gralloc / AHardwareBuffer bandwidth floor

On a native port, frame time on the critical path is bounded by:

```text
serial_frame_ms  ≈ game_cpu_ms + replay_ms + gpu_tail_ms
overlapped_ms    ≈ max(game_cpu_ms, replay_ms, gpu_ms) + submit_sync_overhead_ms
```

- **Big hidden ceiling on Android handhelds:** some kernels/drivers map zero-copy `AHardwareBuffer` / Vulkan `HOST_CACHED` pools **write-combined** on the CPU!
  - Example: Retroid Pocket 5 reads hardware buffers at **1.7–1.9 GB/s** vs **16–21 GB/s** for `malloc`. Reading a 36 MB command pool on CPU cost **24 ms/frame** until moved to cached/lazy pages (**12 ms/frame**).
  - Always compare measured `hwbuf_read_mbs` vs `malloc_read_mbs`. Ratio `< 0.35` means CPU-side reads of GPU pools are a primary ceiling.
- **Per-thread CPU ceiling:** check `/proc/<pid>/task/*/stat` and `scaling_max_freq`. A main or render thread pinned to mid cores at half clock (~1.6–2.0 GHz instead of 3.2 GHz prime) caps FPS even when total SoC CPU is 30%.

## 4. GPU shading floor (Load/Store A/B)

You cannot optimize GPU time below what the game's own fragment/vertex shaders cost at a given resolution and driver. Find that floor directly:

1. **Pin the frame rate** (e.g. `DM_FPS_PIN=30` or `40`) so GPU clocks stay identical across both runs, and disable early split-submission (`DM_VK_SUBMIT_SPLIT=0`) if the replay's `GPU busy` counter includes waits for CPU replay.
2. Measure `gpu_normal_ms` in a heavy, fixed scene.
3. Run a diagnostic **load/store drop** (`DM_VK_X_DEPTH` / `DM_VK_X_COLOR` — draws wrong, measurement only) to measure `gpu_noloadstore_ms`.
4. Interpret the gap:
   - `gpu_normal_ms - gpu_noloadstore_ms` is the upper bound recoverable from **pass merging, UBWC compression, and load/store elimination**.
   - `gpu_noloadstore_ms` is the **shading floor** at this resolution and driver. If dropping all loads/stores saves `< 1 ms`, further backend pass work will yield almost nothing; the remaining levers are **driver choice (e.g. Turnip vs Qualcomm)**, **render resolution / dynamic scale**, or **shader/quality cuts**.

## 5. Memory (LMK) and zero-copy chunk ceiling

Android's Low Memory Killer (`lmkd`) terminates oversized processes with **silent `SIGKILL`** (no crash backtrace in app logs).

```text
usable_ram_mib = mem_available_at_idle_mib - lmk_headroom_mib   # keep ≥ 1,024–1,536 MiB free
```

- **Zero-copy memory pools** backed by `AHardwareBuffer` cannot be swapped out by Android. If a game allocates 3–5 GB of pools upfront while only touching 512 MB, it will be LMK-killed unless lazy backing (`DM_VK_ZEROCOPY_LAZY`) is enabled.
- **Gralloc single-allocation cap & chunking:** drivers/gralloc abort on huge single allocations (e.g. Adreno 830 aborts at ~812 MiB), so backends split large pools into `ZC_CHUNK` slices (commonly **256 MiB**).
  - `chunks = ceil(largest_pool_mib / chunk_mib)`
  - Whenever `chunks >= 2`, **any vertex, index, UBO, SSBO, or indirect buffer can straddle a chunk boundary**. Either the backend must bridge cross-chunk buffers cleanly (copying spanned slices into a cached `DEVICE_LOCAL` bridge buffer) or you will hit out-of-bounds zeros/NaNs/missing geometry as soon as large scenes load.

## 6. Thermal sustained ceiling

Handhelds and phones run 20–45% faster during the first 60 seconds than at minute 15:

- **Burst ceiling:** cold SoC with `DM_GPU_TURBO=1` and active fan/max profile.
- **Sustained ceiling:** steady-state `frame_p50` / `gpu_p50` after **10 minutes** of continuous play in a heavy area.
- Example: *Tears of the Kingdom* open world on Adreno 740 with turbo pushed GPU from 53 °C → 75 °C in 60 s; 60 fps was achievable in shrines (15 ms GPU) and in short open-world bursts, while **40 fps @ 120 Hz (25 ms budget)** was the realistic sustained open-world target without heavy resolution drops.

---

## Worked examples

### Example A: *NieR:Automata* (1.0.2/1.0.3) on Retroid Pocket 6 (Snapdragon 8 Gen 2, Adreno 740, Turnip 27.0.0-R2, 120 Hz)

- **Display:** 120 Hz → clean targets `60 fps (16.67 ms)`, `40 fps (25.00 ms)`, `30 fps (33.33 ms)`.
- **Engine:** Switch build ships locked at 30 fps; with clean 60 fps binary cuts + display-mode FPS menu, engine scales cleanly to 60 fps.
- **Pool geometry:** single **1.30 GB** (`0x53840000`) NVN pool split into **6 × 256 MiB** chunks → high cross-chunk straddle risk (confirmed in City Ruins: Stream 1 normal buffer at `0x6f1f8fad50 + 0xadd780` straddled `0x6f20000000` by ~3.5 MiB).
- **Measured after Ladder A fixes (sRGB twin + color feedback snapshot + cross-chunk bridging):**
  - City Ruins heavy view (default 256 MiB chunks): **58–60 fps**, 0 NaN/inf across all passes, 1 bridge copy on load.
  - 64 MiB chunk stress test (21 chunks): 4 bridged buffers, **60 fps**, 0 NaN/inf.

### Example B: *Tears of the Kingdom* (1.4.3) on Retroid Pocket 6 (120 Hz, 12 GB RAM, Turnip 27.0.0-R2)

- **Display:** 120 Hz → **40 fps (25.0 ms)** chosen as open-world target (beats 30 fps smoothness while staying inside sustained thermal budget).
- **CPU replay lever:** `DM_VK_MT_REPLAY=1` cut replay from **11.0 → 7.6 ms** (lifting shrine/pond scenes cleanly under 16.7 ms).
- **Correctness blockers cleared first:** `DM_VK_MINIMAL_BACK=1` (260×260 RG8 terrain tile write-back stopped grass vanishing ~40 s after load), disabling `DM_VK_X_CUTANY` and testing `DM_VK_CB_DRAWS=0` for Adreno LRZ camera-turn flashes.

Use `scripts/budget.py examples/device-rp6-nier.json` to run this arithmetic for any new game and device.
