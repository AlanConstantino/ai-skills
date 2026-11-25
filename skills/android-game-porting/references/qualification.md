# Qualifying, upstreaming and shipping a port

A port fix or tuning win only counts when a clean **release** build reproduces it on the device with zero diagnostic hacks, intact user saves, green CI, and honest documentation.

## 1. Decide where every change belongs

| What changed | Right home | Never do |
|---|---|---|
| Package name, env defaults, orientation, overlay layout, required Vulkan features, tested status | **Game profile** (data only, e.g. `android.tsv` / `game.tsv`) | Hardcoding title IDs or game names in shared C/C++ runtime |
| Per-build binary fix (60 fps cut, frame-clock drift fix, game OOB bug) | **Build-ID-keyed patch** (e.g. `patches.py`) | Applying blindly to unverified game updates |
| Graphics/OS/Vulkan/driver/API rule (cross-chunk buffers, sRGB twin, color feedback, write-back, LRZ, HLE stub, shader translator rule) | **Shared core** as a general semantic rule, with a comment citing the evidence | Per-game `if` branches or permanent diagnostic clamps/overrides |

## 2. Clean build, unit tests & CI

- **Commit to a clean branch** (separate personal fork branch vs upstream PR worktree off `origin/main`).
- **Write targeted unit tests for every core fix** (e.g. C harness / Python test covering boundary conditions, cache coherence, teardown, and regression cases — just like `test_vk_cross_chunk_buffers.py`).
- **Verify all build & CI targets pass:**
  - host unit tests across OS/compilers (`linux-gcc`, `linux-clang`, `macos`, `windows`);
  - architecture / layer-rule checks (no game IDs in core, valid settings tables, regenerated doc tables);
  - Android NDK host builds — **both debug and release**;
  - if shader translator or readers changed, verify unaffected games stay byte-identical (or intentionally improved with regenerated tables).

## 3. Clean runtime state & release APK

Before running final acceptance gates:

1. **Build and install the release APK** (not `--debug`), bumping `versionCode` cleanly if replacing an earlier build so Android updates in place without touching saves.
2. **Audit `env.txt`:** remove every temporary debug switch (`DM_CTL`, `DM_VK_VALIDATE`, `DM_VK_PASSDUMP*`, `DM_VK_PIXEL_HISTORY`, `DM_VK_TRACE_*`, `DM_VK_SPV_ALT`, `DM_VK_PASS_TIMES`, `DM_VK_ZC_CHUNK_MB` stress values, `DM_FPS_PIN`). Leave **only** shipping profile / user-chosen settings.
3. **Delete temporary device artifacts:** remove dumped `pass_*.ppm`, `pass_*.f32`, `pixel_history.tsv`, `frame_*.ppm`, `aud.pcm`, and `/data/local/tmp/` scratch recordings.
4. **Verify user saves are untouched** (compare file list & SHA-256 against the pre-session backup from `scripts/probe.py backup-saves`).

## 4. Acceptance gates (required before calling a port "playable")

Run all gates on the **clean release build** (plus one validation check on debug):

| Gate | Requirement |
|---|---|
| **1. Boot & menus** | Clean launch, logos/movies, title screen, menus, pause menu, and on-screen/controller/touch navigation work cleanly. |
| **2. Multi-scene rendering** | Visually verified across **multiple distinct zones/chapters** (including heavy scenes, camera rotation, combat/effects, UI/minimap). Zero black curtains, missing geometry, or flicker. |
| **3. Vulkan validation (debug check)** | 0 new VUIDs in gameplay + camera turns under Khronos Validation Layer (`DM_VK_VALIDATE=1`), then validation turned back off. |
| **4. Audio & input** | Clean music/SFX/voice (no buzz, repeats, or clicks), working controller mapping, rumble, and motion/gyro if used. |
| **5. Save & load round-trip** | Create/update a save in gameplay, exit cleanly, relaunch, and reload that exact save without error or corruption. |
| **6. Performance & pacing** | Meets agreed FPS target (median p50/p95 + late presents) in the agreed benchmark scenes at steady-state thermals. |
| **7. Stability soak** | **30-minute** gameplay soak (or recorded user play session): **same PID throughout**, 0 crashes/LMK kills, 0 new `[RED]` lines, stable temperatures. |
| **8. Cross-game regression** | If shared core code changed, verify at least one other ported title (plus full unit suite) shows no visual or performance regression. |

Promote status honestly:
- `experimental`: builds/extracts, not yet cleanly booting.
- `boots`: reaches title/menus/some gameplay, but rendering defects, saving, or long-session stability remain unverified.
- `playable`: passes **all 8 gates** above on the documented device, driver, and game version.

## 5. Cleanup & hygiene

- Uninstall temporary test packages (`org.nx2a.<name>.test`) and verify `pm list packages` matches what the user expects.
- Restore any device settings you temporarily altered (fan/performance profile, refresh rate, `DM_FPS_PIN`).
- Delete temporary `.gamedump/<App>/` folders only after verifying every ROM file has hard-link count `>= 2` (so the user's original ROM is never deleted!). Report any remaining folders > 1 GB.
- Grep commits, profile comments, and reports for keys (`prod.keys`), tokens, emails, and local `/Users/...` or `C:\...` paths before pushing.

## 6. Final report format

Always hand back a concise summary containing:

1. **Results table:** metric, baseline, final, ceiling/target, measurement conditions (device, SoC, GPU driver, game ID & update version, scene, release vs debug).
2. **Root causes fixed & levers applied** (with commit hashes and CI run link).
3. **Exact device & repo state left behind:** installed package + `versionCode`, bundled driver, `env.txt` contents, save-file status, git branch/commit, remaining disk folders.
4. **Honest gaps:** list explicitly what was *not* tested (e.g. later chapters, other GPUs/drivers, New Game from start, 30-min soak if skipped).
