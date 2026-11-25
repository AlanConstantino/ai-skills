# android-game-porting

A structured, evidence-driven **hill-climbing skill** for porting console and PC games natively to Android — from initial dump inspection and bring-up through rendering/audio debugging, FPS & frame-pacing optimisation, and clean release qualification.

Inspired by Sybil Solutions' [`inference-optimisation`](../inference-optimisation/) skill: ceilings first, countable metrics, one lever per experiment, noise-controlled A/B runs into a ledger, honest plateau reporting, and zero-dependency Python 3.9+ / shell scripts.

## Why two ladders?

Game porting fails when you optimize performance on top of broken rendering, or when you guess at visual glitches without making them countable. This skill enforces two strictly ordered ladders:

1. **Ladder A — Bring-up & Correctness:** turn crashes, black curtains, HDR NaNs, flicker, missing geometry, and audio glitches into numbers first; bisect with zero-build runtime/driver toggles before rebuilding; isolate semantic root causes (cross-chunk buffers, sRGB twins, color feedback snapshots, GPU-to-CPU write-backs, Adreno LRZ/FDZ/GS hazards); fix cleanly in profile or shared core.
2. **Ladder B — Performance, Frame-Pacing & Thermals:** compute display pacing, CPU/gralloc bandwidth, GPU shading floor, RAM/chunk, and thermal ceilings first; run controlled same-scene A/B experiments (`control → candidate → control` or live toggle) into `ledger.jsonl`; qualify the clean release APK before shipping.

## Layout

```text
android-game-porting/
├── SKILL.md                      # Master two-ladder runbook
├── README.md                     # This overview
├── LICENSE                       # MIT
├── references/
│   ├── ceilings.md               # Frame budgets, CPU/GPU/RAM/thermal ceilings + worked examples
│   ├── methodology.md            # Countable bugs, noise control, HDR .f32 forensics, visual-oracle rules
│   ├── levers.md                 # Ranked correctness bug classes & perf levers with real evidence
│   ├── gotchas.md                # Android, Adreno/Turnip, Vulkan, ADB, save-safety & CI traps
│   └── qualification.md          # Profile vs core, CI/unit tests, clean release gates, soak & cleanup
├── scripts/
│   ├── budget.py                 # Frame-time, CPU/GPU bottleneck, RAM/chunk ceiling calculator
│   ├── device_health.sh          # Single-call ADB thermal, CPU-freq, RAM, battery & PID sampler
│   ├── probe.py                  # Device inventory, save backup, log parser, per-toggle windows, A/B comparator & ledger
│   ├── ab_windows.sh             # Hands-off live A/B: guarded input loop, value windows, thermal CSV, restore
│   ├── f32_audit.py              # Raw float32 HDR/depth NaN/Inf auditor & before/after batch diff
│   └── queue.sh                  # Portable macOS/Linux serialized single-device experiment queue
├── examples/
│   ├── device-rp6-nier.json      # Worked ceiling input (Retroid Pocket 6 + NieR:Automata)
│   ├── exp-baseline.json         # Example experiment config for probe.py
│   ├── case-nier-30fps-combat-dips.md  # Worked hands-off climb: combat dips + heat, root cause to release
│   └── plan-template.md          # Starter PLAN.md template for a new game port
└── tests/
    └── test_scripts.py           # Offline stdlib unit tests for budget.py, probe.py, f32_audit.py & ab_windows.sh
```

## Quickstart

All scripts use Python 3.9+ standard library only (no `pip install` needed).

```bash
# 1. Compute frame, bandwidth, memory & chunk ceilings
python3 scripts/budget.py examples/device-rp6-nier.json

# 2. Check connected Android device thermals, CPU policy caps, RAM & battery
bash scripts/device_health.sh

# 3. Back up existing game saves before testing
python3 scripts/probe.py backup-saves --pkg org.nx2a.mygame --out ./save-backup

# 4. Run an offline log parse or controlled A/B ledger comparison
python3 scripts/probe.py parse-log path/to/log.txt --preset nx2android
python3 scripts/probe.py compare ledger.jsonl ctl-a cand-1 ctl-b --metric frame_p50_ms
python3 scripts/probe.py show ledger.jsonl

# 5. Hands-off live A/B: a guarded input loop plays while a switch takes each value (on, off, on, off), then cut the log per value
scripts/ab_windows.sh --check-steps steps.txt
PKG=org.nx2a.mygame STEPS=steps.txt RESTORE=1 OUT=runs scripts/ab_windows.sh DM_VK_PKG_PART 90 1 0 1 0
python3 scripts/probe.py windows runs/ab-<time>.txt

# 6. Audit raw RGB32F / R32F GPU dumps for NaNs and infinities
python3 scripts/f32_audit.py scan ./passes/
python3 scripts/f32_audit.py compare ./passes-before/ ./passes-after/

# 7. Run the offline test suite
python3 -m unittest discover -s tests -v
```

## Real-world origins

The levers, diagnostic workflows, and guardrails in this skill were distilled directly from bringing up and hill-climbing native Switch-to-Android ports (including *NieR:Automata The End of YoRHa Edition* and *The Legend of Zelda: Tears of the Kingdom* on Retroid Pocket 6 / Snapdragon 8 Gen 2 / Adreno 740 / Turnip, plus upstream `nx2android` ports across Adreno 6xx/7xx/8xx devices). Everything is written to stay generic across games, drivers, and native Android porting stacks.

[`examples/case-nier-30fps-combat-dips.md`](examples/case-nier-30fps-combat-dips.md) is one full climb end to end. It took *NieR:Automata*'s
combat hitches from 66-75 to 1 per 90 s, and the chip about 3 °C cooler, with the user out of the loop.

## Credits

Structure and methodology take inspiration from [`inference-optimisation`](https://github.com/sybil-solutions/inference-optimisation-skill) (MIT, © 2026 Sybil Solutions contributors).
