# Android, Vulkan, driver and porting gotchas

Every trap below burned real hours on a real device. Re-check on newer drivers/OS versions, but assume they still bite until disproven.

## Device & save-file safety

- **Never force-stop or reinstall over a running game** without checking `adb shell pidof <pkg>` and confirming with the user. Interrupting an active session can corrupt an in-flight save or wipe unsaved progress.
- **Never press Confirm (`A`) blindly** in a user's save. Automated confirm presses advance cutscenes, pick dialog options, and overwrite autosaves. Use a tested gameplay detector (passing twice in a row) or stick/camera-only soak scripts.
- **Always back up saves before risky experiments** (`scripts/probe.py backup-saves`). Reinstalling with a different signing key or uninstalling the user's package deletes `/sdcard/Android/data/<pkg>/`!
- **Install test builds under a separate package** (e.g. `org.nx2a.<name>.test`) whenever possible, and uninstall test packages when done.
- **Reinstalling often resets `env.txt`.** Re-apply or verify `env.txt` after every APK install, and strip all diagnostic switches before handing over a release build.
- **Release builds ignore debug control channels (`ctl.txt`)** and drop debug symbols. Use `--debug` for live probing, and always re-verify the final **release** APK separately.
- **Logs rotate on launch** (`log.txt` → `log.prev.txt`). Pull crash logs *before* relaunching twice or the backtrace is gone forever.
- **Stop your own background input loops.** An orphaned shell loop feeding stick moves through `ctl.txt` looks identical to controller drift; check `DM_CTL: press` counts and kill by PID (never `pkill -f` with a pattern that matches your own shell!).

## Adreno, Turnip & Vulkan drivers

- **Run the Khronos Validation Layer (`DM_VK_VALIDATE=1`) before guessing** on `VK_ERROR_*`, dropped frames, or silent draw limits. A "draw 712 limit" that took hours of bisection was named in one VVL run (an image view created for sampling reused as a render target).
- **`TU_DEBUG=nolrz` can make flicker WORSE.** Do not toggle driver-wide `nolrz` blindly on Turnip; bisect `DM_VK_CB_DRAWS=0` and `DM_VK_LRZ_TOUCH` masks first.
- **Gralloc single-allocation caps abort silently or fatally** (e.g. Adreno 830 aborts at ~812 MiB). Keep zero-copy pool chunks modest (256 MiB default) and bridge cross-chunk buffer bindings cleanly rather than cranking chunk size to 1 GB.
- **Write-combined `AHardwareBuffer` CPU reads:** some handheld kernels (e.g. Retroid Pocket 5) map *all* hardware buffers and even `HOST_CACHED` Vulkan memory write-combined (~1.7 GB/s vs ~18 GB/s `malloc`). Always check the startup memory-read benchmark line!
- **Stale Vulkan pipeline caches survive APK updates.** After driver swaps, shader edits, or Vulkan backend changes, test with `DM_VK_NOPCACHE=1` (or clear app cache) so old pipelines do not mask fixes or cause regressions.
- **APK-only updates do NOT regenerate shader bundles.** A shader translator fix requires rebuilding and pushing the shader data pack, not just `libdmhost.so` / APK!
- **`DM_VK_SPV_ALT` overrides vanish on restart** (unless in `env.txt`) and cached pipelines may not reload modified `.spv` files in place. Always confirm the current PID and the alternate-module load log line.
- **`MUTABLE_FORMAT` kills Adreno UBWC compression** (costing 25–40% GPU time). Use `VkImageFormatListCreateInfo` with exact twins (including sRGB twins) instead.

## Diagnostics & measurement traps

- **Pass dumps and pixel history are Heisenbugs.** Both insert barriers, pass cuts, and transfer reads that can hide LRZ or synchronization bugs. Always verify the defect still reproduces in the screenshot *during* the capture!
- **`DM_VK_PASS_TIMES=1` disturbs Adreno binning** and inflates total frame time (e.g. 18 ms frame summing to 26 ms of timed passes). Compare timestamped runs only against timestamped runs, never against clean baselines.
- **`dumpsys thermalservice` lies:** on some Android tablets/handhelds it reports hours-old cached temperatures (93 °C on a cold device). Always read `/sys/class/thermal/thermal_zone*/temp` across multiple zones (`scripts/device_health.sh`).
- **Handheld KGSL GPU counters freeze** in "High Performance" modes (reading identical percentages for minutes). Use replay GPU-span timing with `DM_VK_SUBMIT_SPLIT=0` instead.
- **In-process `SIGPROF` (`DM_VK_PROF`) can crash guest code during loads.** Arm profiling *after* loading completes, or profile host/replay threads only (`DM_NVN_ASYNC=1`).
- **8-bit PPM/PNG screenshots hide HDR NaN/Inf origins.** Always dump raw `.f32` (`DM_VK_PASSDUMP_RAW=1`) and run `scripts/f32_audit.py`.
- **`adb shell input tap` is too short for game touchscreens** (fine for Android UI/pause menus, missed by 30/60 Hz game polling). Use `adb shell input swipe x y x y 150` or held `DM_CTL` presses (`0.4 s` safe default).
- **A "shown late" display-timing counter can stay 0 at 30 fps on a 60 Hz window** even with 50 ms frames (seen on nx2android's
  `display timing` line). Count long frames over ~1.2× the budget (`probe.py parse-log --hitch-ms 40`) instead.
- **Frame p50 hides dips.** p50 stayed 33.4 ms in both arms of an A/B where hitches went 66-75 → 1. Judge "dips" by hitch count per window.
- **Single stationary screenshots miss async/LRZ flicker.** Always validate async replay, CB splits, and LRZ fixes with camera turns and movement recordings.

## Host, ADB, disk & CI traps

- **ADB compression wedges large pushes (`RomFS` / data packs).** Use chunked pushes with compression disabled (`adb push -Z` / `push_chunked.py`).
- **App-folder permissions on Android:** Android only grants the app's UID/GID ownership to `/sdcard/Android/data/<pkg>/files` after the app launches once. Creating it via `adb shell mkdir` before first launch can leave pushed files unreadable by the app.
- **Disk hygiene:** game dumps, extracted RomFS, and pass dumps easily consume 50–300 GB. Keep all dumps under one git-ignored folder using hard links (`ln`) so original ROMs are never copied or modified, and check link count ≥ 2 before deleting anything.
- **Cross-platform CI stubs:** adding a global symbol or helper in shared Vulkan/C runtime code can break standalone unit-test C harnesses (e.g. indirect compute or readiness harnesses on `linux-gcc`, `linux-clang`, `macos`, `windows`) unless every sibling test harness stubs or links it cleanly. Run the full test suite and check all CI matrix targets.
- **Git remotes & branches:** keep personal/fork experiments strictly on your personal fork remote; keep upstream PRs in clean worktrees branched off `origin/main` with zero personal mods mixed in.
- **An A/B script that ends on a candidate value leaves it live.** The next experiment then runs with it silently. Always restore
  (`ab_windows.sh` `RESTORE=`) and grep the log's last toggle line before trusting a later run.
- **Release builds ignore `ctl.txt`.** `adb shell input gamepad keyevent <KEYCODE_BUTTON_*>` reaches the game as a controller button there, through
  the *user's button remap* (check the app's settings first: on one RP6 Switch A was `KEYCODE_BUTTON_B`). It has no stick input, so movement loops need a debug build.
- **Window markers written as unknown switches log warnings.** Mark a device-setting window with a name the host does not parse (e.g.
  `env PERF_performance_mode=1`), not a fake `DM_` switch, or the red-line count of every window goes up.
- **Device-wide settings changed over adb outlive the game** (`settings put system performance_mode`, refresh rate, fan mode). Record the value first and restore it.
- **Shell aliases leak into scripts:** a colour `ls` alias put escape codes into a path and made a byte-identical save compare say "differs". Use `command ls` / `cmp` on explicit paths.
- **Windows / Git Bash paths:** set `MSYS_NO_PATHCONV=1` before passing `/sdcard/...` or `/data/local/tmp/...` to `adb`, and use response files for long Clang link lines (`WinError 206`).
- **Secrets & copyright hygiene:** never commit `prod.keys`, `title.keys`, console firmware, game binaries/assets, or absolute home-directory paths into any repo, profile comment, or bug report.
