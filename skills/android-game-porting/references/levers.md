# Levers and bug classes, by area, with measured evidence

Each entry is tagged:

- **[G]**: general across Android native ports and Vulkan backends.
- **[S: Adreno/Turnip]** / **[S: nx2android/NVN]**: observed on a specific GPU/driver or porting stack; verify whether it applies to yours.

Work **Ladder A (Correctness)** before **Ladder B (Performance)**, and try zero-build toggles before writing code.

---

## Ladder A: Rendering & correctness bug classes

### 1. Cross-chunk pool buffer straddling (moving black curtains, missing meshes, NaN normals)
- **[G]** Large guest memory pools (e.g. 512 MiB–2 GiB) exceed Android gralloc single-allocation limits, so backends split them into multiple `AHardwareBuffer`/`VkBuffer` chunks (commonly `ZC_CHUNK = 256 MiB`).
- **Symptom:** specific world meshes vanish or turn into a **moving black curtain / bloom void** whenever certain geometry enters the frustum.
- **Root cause:** binding only the first chunk's `VkBuffer` makes out-of-bounds vertex/index/indirect reads past chunk end return zeros (`(0,0,0)` normals → `0.0 * InverseSqrt(0.0) = NaN` → HDR `B10G11R11` target poisoned → bloom spreads `NaN` across the screen → tonemap turns it black).
- **Evidence:** *NieR:Automata* City Ruins allocated a 1.30 GB pool across six 256 MiB chunks; Stream 1 (normals + lightmap UVs) straddled the 512 MiB chunk boundary by ~3.5 MiB. *Pokémon Violet* academy library vertices crossed 256 MiB in a 350 MiB pool.
- **Semantic fix:** LRU cache of `DEVICE_LOCAL` bridge `VkBuffer`s stitched across spanned chunks via GPU `vkCmdCopyBuffer` before pass commit (16-byte aligned base, headroom for tail growth, coherent via CPU dirty-page protection / dynamic refresh for hot streaming rings, GPU/SSBO write invalidation, inline deferred updates, and pool teardown). Verified in *NieR:Automata* at both 256 MiB (1 copy, 0 NaN, 58–60 fps) and 64 MiB stress chunks (21 chunks, 4 bridged buffers, 0 NaN, 60 fps).
- **Zero-build confirmation:** temporarily raising `DM_VK_ZC_CHUNK_MB` (e.g. 384/512) shifts the boundary and moves/cures the symptom — proving chunk straddling without relying on huge unsafe gralloc chunks in production.

### 2. HDR NaN / Inf bloom poisoning (persistent blocks or black curtains)
- **[G]** Any `NaN` or `Inf` written into an HDR float target (`B10G11R11`, `RGBA16F`) propagates exponentially through separable bloom downsamples/blurs and turns into huge black or neon blocks after tonemapping.
- **Evidence:**
  - *NieR:Automata*: zeroed normals past chunk boundary → `InverseSqrt(0) = NaN`.
  - *Super Mario Odyssey*: reversed perspective multiply/reciprocal in an alpha depth variant caused depth/material coverage disagreement → retained HDR amplified to `Inf` → bloom NaNs.
- **Workflow:** never "fix" with a global NaN clamp or forced color clear. Use raw float32 dumps + `scripts/f32_audit.py` → single-pixel draw/depth history → inspect exact shader instruction + input buffers → fix the semantic source.

### 3. Color-space / sRGB twin view mismatch (crushed/dark or washed-out grey textures)
- **[G]** When a game creates a linear image and samples or renders it via an sRGB view (or vice versa), missing or mismatched sRGB twin pairing in `VkImageFormatListCreateInfo` / view creation causes hardware to silently skip sRGB↔linear conversion (or fall back to the base format).
- **Evidence:** *NieR:Automata* dark/grey materials cured by fixing `srgb_twin()` mapping so sRGB views resolve cleanly alongside UBWC format lists.

### 4. Same-pass color feedback / self-sampling render targets
- **[G]** Console APIs (like NVN/GX2) allow draws to sample the currently bound color render target inside the same pass (for refraction, distortion, decals, or post-FX). Vulkan forbids reading and writing the same image layout concurrently without a barrier/snapshot.
- **Evidence:** *NieR:Automata* visual artifacts on feedback passes resolved by snapshotting the color target before self-sampling draws (`DM_NVN_FB_SNAP`, ported from `seedds/nxnative`).

### 5. GPU-to-CPU write-backs of render targets (disappearing grass, fog, portraits)
- **[G]** Games sometimes render data on the GPU into a texture (block-linear or linear) and later read or `memcpy`-compact it on the CPU. Because `DEVICE_LOCAL` Vulkan images never update guest RAM automatically, CPU reads see zeros.
- **Evidence:**
  - *Tears of the Kingdom*: GPU renders 260×260 `RG8` (`MINIMAL_LAYOUT`) terrain/grass maps at load, then CPU compacts the terrain pool ~30–40 s later — grass vanished across Hyrule Field until batched staging write-backs (`DM_VK_MINIMAL_BACK=1`) copied and swizzled rendered tiles back to guest RAM before the submission fence signaled.
  - *Super Mario Odyssey*: ground fog reads its `LINEAR_RENDER_TARGET` wave surface (`DM_VK_LIN_BACK`).
  - *Pokémon Violet*: CPU encodes a GPU-copied linear portrait to BC1 (`DM_VK_LIN_COPY_BACK=1`).

### 6. Adreno LRZ (Low-Resolution Z) & Early-Z culling hazards (flashes, dropouts, pop-in)
- **[S: Adreno/Turnip]** Adreno's tiled binning hardware caches a low-res depth pyramid (LRZ) that goes stale across:
  1. command-buffer cuts splitting passes that share a depth buffer (`DM_VK_CB_DRAWS`);
  2. reloading a depth-stencil attachment in the same command buffer;
  3. switching away from a depth buffer to another pass;
  4. early-Z testing against a depth buffer written via `gl_FragDepth`.
- **Evidence:**
  - *Hades II* (`6–23/600 → 0/600`) & *Animal Crossing* (`6–22/60 → 0/60`): cured by cutting CBs only before passes with a new depth image and/or `DM_VK_CB_DRAWS=0` + `lrz_touch` 1-texel depth write-back.
  - *Tears of the Kingdom* RP6: disabling `DM_VK_X_CUTANY=1` immediately stabilized camera-turn flashes; note **`TU_DEBUG=nolrz` made flicker WORSE** — always A/B `DM_VK_CB_DRAWS=0` and `DM_VK_LRZ_TOUCH` bitmasks instead.
  - *Hades II* periodic light dropouts: cured by fragment-depth passthrough variants (`gl_FragDepth = gl_FragCoord.z`, `DM_VK_FDZ=1`).

### 7. Adreno geometry-shader side-effect multiplication
- **[S: Adreno/Turnip]** Multi-output software/hardware geometry passthrough shaders that perform buffer atomics/stores can execute side effects **3× per triangle** on both Turnip and Qualcomm drivers, overflowing indirect index buffers and dropping distant geometry.
- **Evidence:** *NieR:Automata* Desert Zone: splitting a discarded single-point memory-effects pass from a pure raster pass (`DM_VK_GS_SPLIT=1`) reduced distant-rock dark-pixel corruption from **21.37% → 0.00%** in live same-camera A/B (676 triangles repeated 3× → 674 unique triangles, 0 repeats).

### 8. Subgroup & dynamic UBO component hazards
- **[G]** Vulkan drivers only support subgroup operations in the stages listed in `subgroupSupportedStages` (e.g. Qualcomm Adreno 830 lists fragment + compute only; vertex subgroup ops are UB and can hang the GPU).
  - **Fix:** rewrite unsupported stages as 1-lane subgroups at module creation (`DM_VK_SUBGROUP_ONE_LANE`).
- **[S: Turnip]** Dynamic-index vector component loads from UBOs failed to diverge per-lane on Turnip Adreno 740 (*Pokémon Violet* staircase white hair waves).
  - **Fix:** load the full vector and extract with `OpVectorExtractDynamic` (`DM_VK_UBO_COMPONENT=1`, 0 measurable GPU cost vs +9% GPU busy for a wave loop).

### 9. CPU-filled textures, GPU-written BC stand-ins, winding & missing shaders
- **[G]** **CPU-filled uncompressed 2D textures:** if the game writes a LUT directly in guest RAM without an explicit upload call, first-use upload recovers black composition (*NieR:Automata* 256×16 RGBA8 color LUT: title region **0% → 100%** nonblack).
- **[G]** **GPU-written block-compressed textures:** drawing into BC images through uncompressed block-sized views reads zeros on some Adreno/Turnip builds → use an uncompressed stand-in copied back after the pass (`DM_VK_BC_DRAW=1`, *Breath of the Wild* BC4 noise).
- **[G]** **Clip-space Y & winding:** test `DM_VK_NOCULL=1` and `DM_VK_FRONT=0|1|2` immediately when floors are missing or closed meshes are black/inside-out; check both docked and handheld (an even number of Y-flips hides a wrong clip-space convention).
- **[G]** **Missing runtime-compiled shaders:** check logs for `shader … is not in the bundle`; enable capture (`captured_shaders/`) and re-port to fold them into the bundle.

---

## Ladder B: Performance, frame-pacing & thermal levers

Ordered from highest payoff ÷ effort to deepest engineering work.

### 1. Driver & zero-build runtime switches (try first)
- **[G] Swap Vulkan driver (Turnip vs system Qualcomm/Mali):** often the single biggest lever for both correctness and CPU replay cost.
  - *Tears of the Kingdom* (Adreno 830): Turnip 26.2.6 cut replay to **~2.6 µs/draw** vs **~6.1 µs/draw** on Qualcomm's stock driver.
- **[S: Adreno] UBWC compression via format lists (`DM_VK_UBWC=3`):** avoid `MUTABLE_FORMAT` / `EXTENDED_USAGE` on color/depth targets so Adreno keeps Universal Bandwidth Compression enabled.
  - *Super Mario Bros. Wonder*: GPU **11.8 → 8.7 ms** (−26%).
  - *Unicorn Overlord* (Adreno 730): **−35–40% GPU**.
  - Check logs for `[RED] view format … of a format-listed image` (signals a game needing bit 4 / `=7` or `=2`/`0`).
- **[G] Background async pipeline compilation (`DM_VK_ASYNC_PIPE=1`):** eliminates shader-compile stutters on first-seen effects (*Super Mario Odyssey* cap throw: **6 frames >50 ms → 0**). Trade-off: if camera-turn geometry pops in late, verify with `DM_VK_ASYNC_PIPE=0` and check `skipped (no pipeline)` in logs.
- **[S: Adreno] Demand-driven GPU turbo (`DM_GPU_TURBO=need` or `auto`):** engages max GPU clock only when frames miss display vsync, releasing it in menus to save battery/heat.
  - *Super Mario Bros. Wonder* (Fold7): **31–115 dropped/300 → ~3/300**.
  - *Tears of the Kingdom* (Turnip): shrine GPU **18 → 15 ms** (60 fps held); open world 30 → 60 burst (watch thermals: 53 → 75 °C in 60 s).

### 2. CPU/GPU overlap & multithreaded replay
- **[G] Split command-buffer submissions (`DM_VK_CB_DRAWS=128`, respecting LRZ cut rules):** overlaps CPU command recording with GPU execution.
  - *NEO: TWEWY*: frame time **36.7 → 15.7 ms** (−57%).
- **[G] Asynchronous / two-stage multithreaded replay (`DM_NVN_ASYNC=1`, `DM_VK_MT_REPLAY=1`):** decouples game render thread from `vkCmd*` driver calls.
  - *Tears of the Kingdom* (Great Sky Island pond): `MT_REPLAY=1` cut replay **11.0 → 7.6 ms**, p50 **19.85 → 16.69 ms** (locked 60).
  - *Always validate with camera-turning recordings, never stationary shots alone!* Async replay can expose missing acquire-waits or texture-ID versioning races.
- **[G] Eliminate false sync/acquire fences:** trace render-thread waits (`DM_NVN_SEQ=N`). Fencing window acquire on *all* submitted work instead of the texture's last present serialized *Xenoblade Chronicles 3* (**37 → 33 ms** docked once fixed).

### 3. Pass, descriptor & memory-pool overhead
- **[G] Cut render passes:** defer inline uniform updates (`DM_VK_UBD_HEAP=1` with cached host shadows — avoiding uncached GPU scratch reads saved **17–33% replay CPU** in *Breath of the Wild*) and merge consecutive passes sharing identical attachments (never across write→read barriers).
  - *NEO: TWEWY*: **145 passes → 28 passes** for 148 draws.
- **[G] Cached `VkRenderPass` + imageless framebuffers over dynamic rendering:** Adreno's `vkCmdBeginRendering` adds ~40 µs/pass (`DM_VK_RP=1` default).
- **[G] Dynamic UBO offsets & descriptor/view caches:** *NEO: TWEWY* descriptor reuse **6% → 86%**.
- **[G] Pool lookup slots, slabs & reuse:**
  - 512-slot page lookup table (`DM_VK_POOL_SLOTS`): *Breath of the Wild* (>4,096 live pools) lookup CPU **13% → 4%**.
  - Small-pool slabs (`DM_VK_POOL_SLABS`) & buffer reuse (`DM_VK_POOL_REUSE`): eliminates ~0.9 ms/pool gralloc churn during loads (*Pokémon Scarlet*: 1,000–2,000 small pools/sec).
- **[G] Lazy zero-copy backing (`DM_VK_ZEROCOPY_LAZY`):** mandatory on write-combined `AHardwareBuffer` devices (Retroid Pocket 5: 1.7 GB/s vs 18 GB/s `malloc` → *Pokémon Legends: Z-A* replay **24 → 12 ms**) and on games reserving multi-GB sparse/unused pools to avoid LMK kills.
- **[G] SVC lock spin-before-park (`DM_SVC_SPIN`):** cuts job-thread handoff stalls dramatically (*Pokémon Scarlet* capture stall **~630 → ~360 ms**).

### 4. Frame pacing, ADPF, clock drift & FPS cuts
- **[G] Pin FPS directly (`DM_FPS_PIN=40|60`) before blaming hardware:** separates timid adaptive governors from real GPU/CPU limits (*NEO* crowd held 40 fps pinned, GPU **20.6 → 15.2 ms** once clocks rose).
- **[G] Register busiest guest threads in ADPF:** if the game's main thread runs outside ADPF on half-clocked mid cores while prime cores idle, re-register top threads periodically.
- **[G] Lock window rate to game rate (`DM_VK_WINDOW_GAME_RATE=1`):** stops LTPO/VRR displays from dropping to 30 Hz during brief dips and permanently trapping 60 fps recovery trials.
- **[G] Fix frame-clock overshoot drift:** if `VK_GOOGLE_display_timing` shows steady present-margin decay (`3 ms → 0 ms` every 30–60 s) with 0 late GPU frames, the game's limiter waits from *actual* frame end instead of *planned* end (*Fire Emblem: Three Houses*: **19 resyncs / 20 min → 0**).
- **[G] Build-keyed 60 fps cuts & 40 fps @ 120 Hz pacing:** unlock 30-locked games cleanly via build-ID-keyed patches (`patches.py`), and offer 40 fps on 120 Hz handhelds when 60 fps exceeds sustained thermals.

### 5. Audio, video & input bottlenecks
- **[G] Raw PCM glitch auditing over underrun logs:** 0 underruns does NOT mean clean audio! *Fire Emblem: Three Houses* double-updated its audio renderer <1 ms apart, causing catch-up renders to loop refill buffers (**46% repeated blocks** → fixed with `DM_AUDREN_CATCHUP_FRAMES=200`).
- **[G] NEON video decode + GOB block-linear upload (`DM_MOVIE_FAST=1`):** *MMBN Legacy Collection* 1080p launcher went **30 → 60 fps** (NV12→RGBA 9.9 → 3.3 ms, WriteTexels 5.0 → 2.5 ms).
- **[G] Six-axis gravity fusion:** fuse accelerometer gravity with gyro via a Mahony filter so motion aiming does not drift diagonally.

### 6. Labelled quality trades (explicit user approval required)
- **[G]** Only after load/store A/B proves you are at the game's shading floor:
  - Static render scale (`DM_RENDER_SCALE=0.75–0.90`) or dynamic resolution (`DM_DYN_SCALE=low|medium|high`).
  - Note diminishing returns: *Tears of the Kingdom* pond `1.0 → 0.6` saved only **2.6 ms** of GPU p90 because geometry/CPU/fixed passes dominated — always measure the actual gain before trading clarity!
