# Port Hill-Climb Plan — <Game Title>

- **Game:** `<Title>` (`<Title ID>`, `<Version / Build ID>`, `<Engine / Graphics API>`)
- **Device:** `<Device>` (`<SoC>`, `<GPU>`, `<Driver>`, `<Android OS>`, `<Display Hz>`, `<RAM>`)
- **Target:** `<e.g. playable, 0 VUIDs/NaNs, 60 fps (16.67 ms) or 40 fps @ 120 Hz (25.0 ms)>`
- **Constraints:** `<lossless visuals | allowed quality trades | upstreamable core fixes>`
- **Save backup:** `<path + sha256 verified date>`

## Ceilings (`THEORY.md` summary)

- Display pacing steps: `<e.g. 120 / 60 / 40 / 30 fps>`
- Engine internal rate cap: `<30 locked | 60 native | patched>`
- Host vs AHardwareBuffer read BW: `<X MB/s vs Y MB/s>`
- GPU shading floor (load/store A/B): `<X ms normal → Y ms no-load/store (delta Z ms)>`
- Largest pool / chunk count: `<X MiB / 256 MiB = N chunks>`
- Safe RAM ceiling: `<X MiB available - 1536 MiB headroom>`

## Ladder A: Bring-up & Correctness Log

| Date | Symptom (countable) | Hypothesis | Probe / Toggle | Result | Verdict & Fix |
|---|---|---|---|---|---|
| YYYY-MM-DD | e.g. 142k NaN px in pass 30 | Cross-chunk vertex stream | `ZC_CHUNK_MB=384` | NaNs dropped to 0 | **ACCEPTED** — implemented `vk_cross_buf` (`<sha>`) |

## Ladder B: Performance & Pacing Log

| Run | Lever (single change) | Scene / FPS Pin | Frame p50 / p95 (ms) | GPU p50 (ms) | Replay (ms) | Late/300 | Temp (°C) | Verdict & Reason |
|---|---|---|---:|---:|---:|---:|---:|---|
| `ctl-a` | Baseline | Fixed scene, pin 60 | | | | | | Control |
| `cand-1` | e.g. `DM_VK_MT_REPLAY=1` | Fixed scene, pin 60 | | | | | | Compare vs `ctl-a` & `ctl-b` |
| `ctl-b` | Baseline repeat | Fixed scene, pin 60 | | | | | | Control |

## Negative Results (Never Repeat Without New Evidence)

- `<Date>` — `<Lever>`: `<exact numbers + why rejected>`

## STATUS (Update on Plateau or Handback)

- **Current status:** `experimental | boots | playable`
- **Baseline → Best → Ceiling:** `<summary>`
- **Limiting bottleneck (with evidence):** `<summary>`
- **Remaining ranked levers:** `1. ... 2. ...`
- **Not yet verified:** `<explicit list>`
