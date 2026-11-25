# Worked case: steady, cooler 30 fps in *NieR:Automata* combat (Retroid Pocket 6)

A complete Ladder B climb, run hands-off: the user asked for "a stable 30 fps with a cooler chip, the frames dip in combat", and for as little
involvement as possible. One root cause, one fix, three controlled A/Bs and a release check took about 3 hours of agent time. The user
gave two short replies in that time: a go-ahead for the fix, and "automate it".

- **Game:** *NieR:Automata The End of YoRHa Edition*, Switch build 1.0.2, through nx2android (NVN HLE, Vulkan replay).
- **Device:** Retroid Pocket 6 (Snapdragon 8 Gen 2, Adreno 740, bundled Turnip 27.0.0-R2), handheld 1280x720, window 60 Hz, game menu set to 30 fps.
- **Target:** every frame inside 33.33 ms in combat (30 fps on 60 Hz: a frame over that is shown for 50 ms, one judder), lower temperatures, no quality trade.

## 1. Countable baseline (from the user's own play log)

| per 300-frame window | p50 | p95 |
|---|---|---|
| frame time | 33.3-40.2 ms | 44.8-65.5 ms |
| host replay (submit+present) | 8.8-30.5 ms | 26-52 ms |
| "GPU" span | 18.8-33.0 ms | 27.6-50 ms |

- 557 long-frame lines in ~200 s of combat, about 9% of frames.
- In 379 of them neither the GPU span nor the replay was over budget alone: the game's CPU time plus the replay added up past 33 ms. So the
  host's own work, not the GPU, decided the dips.
- The replay line's texture upload counter: **1.4-2.5 uploads, 11-33 MB, 4.7-12.8 ms a frame**.
  33 MB a frame for 20 s is about 20 GB, far more than the game's texture memory: the same textures were being uploaded again and again, not streamed.
- The "GPU" span was not pure GPU work: with early submits on, it runs from a submission's start to its end and includes the GPU waiting for the replay.
- Device state: `settings system performance_mode=2` pinned every CPU cluster at its top clock and the GPU at 615-680 MHz, even with the screen off.

## 2. Ranked levers (written into PLAN.md before any run)

1. Stop re-uploading unchanged textures (code, shared core). Expected 5-13 ms less replay in combat.
2. A faster CPU deswizzle from an upstream PR (byte-identical output).
3. Measure pure GPU time (`DM_VK_SUBMIT_SPLIT=0`) to know the GPU headroom.
4. Lower the device performance mode (the cooling lever), only once the frame has headroom.
5. Only if GPU-bound: the game's own dynamic resolution (a quality trade: ask first).

## 3. Finding the cause: a diagnostic build, not guesses

A debug build with per-texture upload statistics (which textures, how often, and what made them dirty) named three textures:

| texture | size | written |
|---|---|---|
| 512x512, 16 layers, RGBA8 | 16 MB | NVN `WriteTexels`, regions of single layers |
| 512x512, 16 layers, RGBA16F | 32 MB | same |
| 16x16, many layers, RGBA16F | 1.5 MB | same |

The game called `WriteTexels` about **70 times a frame** on them. Each call made the **whole** storage writable and marked it written (72 GB of
page preparation in 20 s), so the next draw deswizzled and uploaded the whole 16 or 32 MB array for a write of one small region.

## 4. The fix (semantic, shared core, behind a kill switch)

- The write path marks only the level and layers it writes (the storage slice of each, from the texture layout).
- A re-upload deswizzles and copies only the levels and layers with a page written since the last upload. The rest of the image already holds them.
- Kept off for GPU-written images, linear storage, CPU-decoded formats and sparse textures (whole uploads, as before).
- Runtime kill switch `DM_VK_PKG_PART` (default on, live), so a regression can be tested with no rebuild.
- Unit test with the swizzler as the oracle: for random 2D / 3D / array / cube / cube-array geometries, every byte the swizzler writes for a
  level of a layer lies inside that slice, slices never overlap, and the write side and the upload side compute the same slices.

## 5. Live A/B (same run, same scripted loop, two pairs of 90 s windows, debug build)

| | fix on | fix off | fix on | fix off |
|---|---|---|---|---|
| uploads a frame | 0.62 MB | 5.29 MB | 0.49 MB | 7.60 MB |
| replay | 9.33 ms | 10.73 ms | 9.38 ms | 11.51 ms |
| frame p95 | 37.3 ms | 41.2 ms | 37.5 ms | 40.6 ms |
| **hitches (frames over 40 ms)** | **1** | **75** | **1** | **66** |

`python3 scripts/probe.py windows <log>` prints this table straight from the pulled log, cut at each live toggle.

Pure GPU (`DM_VK_SUBMIT_SPLIT=0`, same loop): **20.6 ms p50 / 26.9 ms p95** at 615-680 MHz. After the fix the frame has ~6 ms of GPU
headroom at p95, which is what the cooling lever spends.

## 6. Cooling lever: device performance mode (4-minute windows, two pairs, fix on)

| | mode 1 | mode 2 (user's) |
|---|---|---|
| CPU max temperature (mean) | 77.6, 77.4 °C | 80.8, 80.5 °C |
| GPU max temperature (mean) | 63.3, 64.1 °C | 65.7, 66.1 °C |
| GPU clock | mostly 550 MHz | mostly 615 MHz |
| hitches | 0, 0 | 2, 0 |
| frames 34-38 ms | 16, 10 | 28, 57 |

Mode 1 is ~3 °C cooler and at least as smooth. It is a device-wide setting (every other game too), so it was measured, recommended, and
restored to the user's value: the user decides.

## 7. Release qualification

- Release APK from the committed tree, the user's original `env.txt` (no diagnostic switches), installed in place (same `versionCode`, saves kept).
- In game: uploads 0.47 MB / 0.35 ms a frame, 2,300-2,600 draws, frame p95 37-39 ms, no new `[RED]` lines.
- Saves exported before the first run and after the last: byte-identical (`cmp`), all three files.

## 8. What made it hands-off

1. **A bench save slot** the agent may load and play in, never the user's story slot.
2. **A stick-and-attack input loop** (`scripts/ab_windows.sh`, `STEPS` file): run in a circle with the camera turning, light attack, fire,
   run back. No Confirm, no menu buttons: the script refuses them (`FORBID`). It stops itself if the game's PID changes.
3. **Live toggles in pairs** (`on → off → on → off`) inside one run: same scene, same shader cache, same thermals drift for both values.
4. **A countable hitch metric** (frames over 40 ms) instead of an average, and the log cut per window by its toggle lines.
5. **Temperatures and GPU clock every ~10 s** into a CSV per run, read from `/sys/class/thermal` (not `dumpsys`).
6. **Restore everything at the end:** the switch (`RESTORE=`), the device setting, the user's `env.txt`, the release build, and a save compare.
7. **Screenshots only to check menus before any press**, inspected by the agent itself, never scored by a heuristic.

## 9. Lessons that carry to other games

- **MB uploaded a frame × seconds ≫ texture memory ⇒ re-uploads, not streaming.** It is the quickest detector of this bug class, and it is in the replay line already.
- **Partial CPU writes + whole-resource dirty tracking = whole-resource re-uploads.** Track writes at the granularity the API writes
  (level and layer, or page) and upload at that granularity.
- **"Dips" are a count, not an average:** p50 stayed 33.4 ms in both arms of the A/B while hitches went 66-75 → 1.
- **Measure pure GPU before cooling:** the performance-mode lever is safe only because the GPU had ~6 ms of p95 headroom after the fix.
- **A device performance mode that pins clocks costs heat for nothing once the frame has headroom**; measure it, then let the user choose.
- **Keep a kill switch on every shipped fix.** A later user report in the same area can be checked with one `env.txt` line and no rebuild.
