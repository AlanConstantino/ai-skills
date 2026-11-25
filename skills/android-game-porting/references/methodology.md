# Measurement and debugging methodology

Most wasted hours in game porting come from either (a) chasing an uncounted visual symptom by guessing at code, or (b) calling a 5–10% thermal/clock swing a "speedup". Every rule below comes from real porting sessions where skipping it led to a wrong conclusion.

## 1. Make every bug countable before editing code

Never work from vague descriptions ("still flickers a bit", "looks darker", "feels smoother"). Convert the symptom into a concrete number first:

| Bug type | Countable metric | How to measure |
|---|---|---|
| Intermittent flicker / 1-frame dropouts | `bad_frames / N` (e.g. `14/300`) | Screen recording + per-block neighbor-agreement spike count (`rec_flicker.py` or frame shots) |
| Moving black curtain / bloom blocks / bright spots | `nan_channels`, `inf_channels`, `nonfinite_pixels` per pass | Raw **float32** HDR dump + `scripts/f32_audit.py` |
| Missing triangles / dark silhouette / broken LUT | `ROI dark-pixel %` or `nonblack %` at fixed camera | Cropped frame shot at identical save & camera angle |
| Driver rejection / Vulkan UB | `VUID count` by unique ID | Single run with Khronos Validation Layer (`DM_VK_VALIDATE=1`) |
| Missing HLE stubs / shader misses / sync stalls | `[RED]` line count by unique message | `scripts/probe.py parse-log` |
| Audio buzz / crackle / stutter | `repeats`, `frame-edge clicks`, `gaps`, `clipping`, `underruns` | Raw PCM dump audit (`pcm_glitches.py`), separate from underrun logs |
| Frame pacing judder / resyncs | `late_on_screen / 64`, `resyncs / 600`, `p95` & `p99` ms | Host timing log + `dumpsys SurfaceFlinger --latency` |

## 2. Zero-build toggle bisection first

Rebuilding an APK, pushing it, and relaunching takes minutes and changes build state. Flipping a runtime switch takes seconds:

1. **Test one switch at a time** (driver swap, `CB_DRAWS=0`, `FDZ`, `UBWC`, `ASYNC_PIPE`, `NVN_ASYNC`, `MT_REPLAY`, `GS_SPLIT`, `LRZ_TOUCH`, etc.).
2. **Prefer same-run live toggles** (e.g. `ctl.txt` `env KEY=VALUE`) so the camera, scene, shader cache, and memory allocations do not move between A and B.
3. **Always run reversible `orig → cand → orig` checks** for visual fixes: if turning a candidate on cures a defect, turning it back off at the same camera angle must bring the defect back (warning: a forced clear can erase retained NaN color permanently so it doesn't return until you move the camera!).
4. Only edit C/C++/shader source after zero-build bisection tells you **which subsystem** owns the bug.

## 3. Noise control on Android

Run-to-run frame-time variance on mobile SoCs is easily **±8–20%**. Major confounders:

- **DVFS clock ramp:** clocks take **3–5 seconds** to settle after a rate change or scene load. Discard the first 120–300 frames after load or mode change.
- **FPS-coupled GPU clocks:** a GPU at 30 fps downclocks so `gpu_busy` looks *higher* per frame (or lower efficiency) than at 60 fps! **Always pin FPS (`DM_FPS_PIN`) when comparing GPU time across builds.**
- **Thermal throttling & fan/power profile:** stay plugged in (or above a fixed battery floor with fixed handheld performance/fan mode). Record multi-zone temperatures **before and after** every run (`scripts/device_health.sh`); void the run if max SoC temp drifted > 5 °C or CPU `scaling_max_freq` dropped mid-run.
- **Scene drift:** walking a few meters changes draw count by 2×. Anchor benchmarks to a **fixed save + stationary camera** (for micro/pass A/B) PLUS a **repeatable movement/turn route** (for streaming, async replay, and pop-in checks — never trust stationary shots alone for async/LRZ bugs!).
- **Interleaved controls:** when you cannot toggle live in one run, always run **`ctl-a → candidate → ctl-b`**. Accept a speedup only if the candidate beats **both** controls and regresses no guardrail (p95, late frames, `[RED]`, VUIDs, RSS, temp) by more than ~3% (`scripts/probe.py compare`).

## 4. Visual oracle and human-in-the-loop discipline

- **Never run unattended automated launch → capture → heuristic-score loops** while a human is holding the handheld: they burn time/tokens, fight the user for control, and heuristic image metrics routinely miss real bugs (or false-positive on animated clouds/UI).
- **Always visually inspect raw captured screenshots yourself** whenever screenshots are used as evidence.
- **Coordinate with the human:** tell the user *before* enabling any diagnostic probe that alters colors/draws, wait for them to position the camera on a natural incident, and ask before restarting or pushing over an active session.

## 5. Raw HDR `.f32` forensics (never trust 8-bit PPMs alone)

8-bit PPM/PNG dumps clip HDR values to `[0, 1]` and turn `NaN` into black or arbitrary bytes — hiding whether an HDR target overflowed before tonemapping or bloom.

1. Identify the HDR render target pass (`DM_VK_TRACE_PASS=-2`, matching resolution + float format such as `B10G11R11` = 122 or `RGBA16F` = 97 — pass ordinals shift as menus open or objects stream!).
2. Capture bounded **raw float32** dumps (`DM_VK_PASSDUMP_RAW=1`, optionally cropped to a region around the artifact to keep dump sizes manageable).
3. Run `scripts/f32_audit.py scan <dir>` to pinpoint the **earliest pass** where `nan_channels > 0` or `inf_channels > 0`, and grab exact `(x, y)` coordinates of poisoned pixels.
4. Trace **one poisoned pixel** across every draw in that pass (`DM_VK_PIXEL_HISTORY=WxH:X,Y`) to find the exact draw call, vertex/fragment shader keys, vertex/index buffer addresses, and UBOs that first wrote `NaN`/`Inf` (or mismatched depth vs color).
5. Dump that draw's vertex/index buffers, UBOs, and SPIR-V (`disassemble → read the exact instruction`, e.g., `InverseSqrt(0.0)` from zeroed out-of-bounds vertex normals!).
6. Verify the root-cause fix by confirming **`0 NaN / 0 Inf`** across every pass in `scripts/f32_audit.py compare before/ after/`.

## 6. Live SPIR-V probe discipline

When you suspect a specific shader instruction or input:

- Dump and disassemble the SPIR-V module, write a targeted diagnostic probe (e.g. color-coding `isnan(normal)` or `gl_PrimitiveID`), and load it via live override (`DM_VK_SPV_DIR` + `DM_VK_SPV_ALT=1`).
- **Two classic traps:**
  1. Pipeline caches keep already-built pipelines; changing a `.spv` file on disk may not rebuild cached pipelines without a restart or `DM_VK_NOPCACHE=1`.
  2. Live overrides vanish on restart unless set in `env.txt`. Always check the log for the actual alternate-module load line and current PID!
- **Never ship a diagnostic shader override.** Once the probe proves *why* the value was bad (e.g., vertex stream truncated at chunk boundary, wrong UBO component, missing sRGB view), delete the override and fix the runtime or shader translator cleanly.

## 7. Heisenbug isolation

Debug tools change GPU synchronization and memory layout:
- Pass dumps insert **barriers and transfer reads** (which can silently flush Adreno LRZ or wait on unwritten storage buffers, making flicker disappear!).
- `DM_VK_PASS_TIMES=1` inserts timestamp queries that **disturb Adreno binning** (only compare timestamped runs against timestamped runs, never against untimestamped baselines).
- `DM_VK_PIXEL_HISTORY` splits passes per draw.

**Rule:** whenever turning on a debug tool makes a bug vanish, **do not trust the capture as clean** — instead, test each side effect the tool added one by one (extra barrier, pass cut, 1-texel depth read, transfer write-back). That almost always reveals the exact root cause!

## 8. Telemetry traps on retail Android devices

- **`dumpsys thermalservice`** can report hours-old cached temperatures (e.g. 93 °C on a cool tablet). Always read multiple `/sys/class/thermal/thermal_zone*/temp` nodes (`scripts/device_health.sh`).
- **Policy CPU caps vs heat:** a handheld can cap `scaling_max_freq` to 2.0 GHz at 55 °C due to Android thermal/power policy, not silicon throttling. Check both `scaling_cur_freq` AND `scaling_max_freq` against `cpuinfo_max_freq`.
- **Static KGSL GPU counters:** on several Qualcomm handhelds in High Performance mode, `gpubusy` / `devfreq/gpu_load` freeze at a constant value for minutes. Infer GPU load from replay/GPU frame spans with `DM_VK_SUBMIT_SPLIT=0` and `DM_NVN_SEQ` waits.
- **Profilers:** `simpleperf` is often blocked on retail kernels, and in-process `SIGPROF` sampling of guest game threads can crash guest code during loads. Profile host/replay threads only (or arm after load).
- **Split-submit GPU spans:** when command buffers submit early, `GPU busy` measures first-submit-start to last-submit-end, which *includes* gaps where the GPU sat waiting on CPU replay. Disable early splits briefly when measuring true GPU work time.
