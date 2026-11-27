# Hill-Climb Plan — <Project / Target>

- **Objective:** see `OBJECTIVE.md`
- **Ceilings:** see `THEORY.md`
- **Current best:** `<baseline -> best (% of ceiling)>`

## Ranked Lever Backlog (cheapest first)

1. `[zero-code]` <runtime flags / env switches / cache warm-up> — expected gain: `<est>`
2. `[algorithmic]` <remove redundant work / batching / layout> — expected gain: `<est>`
3. `[concurrency]` <overlap / sync wait removal> — expected gain: `<est>`
4. `[hot-path]` <kernel / SIMD / compiler flags> — expected gain: `<est>`
5. `[opt-in trade]` <labelled quality trade — ask user first> — expected gain: `<est>`

## Ladder A — Correctness Log

| Date | Countable symptom | Hypothesis | Probe / Toggle | Result | Verdict & Fix |
|---|---|---|---|---|---|
| YYYY-MM-DD | | | | | |

## Ladder B — Experiment Log

| Run | Lever (single change) | Control A | Candidate | Control B | Gain (min neighbor) | Guardrails | Verdict & Reason |
|---|---|---:|---:|---:|---:|---|---|
| `exp-000` | Baseline | | | | — | PASS | Baseline |

## STATUS (Update on Plateau or Handback)

- **Baseline → Best → Ceiling:** `<baseline> → <best> / <ceiling> (<%>%)`
- **Limiting bottleneck (with evidence):** `<what profiling proved>`
- **Remaining levers (ranked):** `1. ...`
- **Not verified:** `<explicit list of untested environments/workloads>`
