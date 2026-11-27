# Hill-Climb Objective — <Project / Target>

- **Goal:** <plain-English statement of what we are optimizing or fixing>
- **Primary metric:** `<metric_name>` (`higher` | `lower` is better)
- **Finish line:** `<target value, or plateau within X% of physical ceiling>`
- **Physical ceiling (`THEORY.md`):** `<theoretical / practical ceiling>`
- **Noise floor:** `<measured baseline spread, e.g. ±3.0%>`

## Hard Correctness Gates (Ladder A — non-negotiable)

Every candidate MUST pass all gates below before speed counts:

1. `<e.g. bitwise/exact output equivalence check passes>`
2. `<e.g. unit + integration test suite passes (0 failures)>`
3. `<e.g. zero validation/sanitizer errors>`

## Guardrails (Secondary Metrics)

Candidate is rejected if any guardrail regresses by more than its budget:

| Guardrail metric | Direction | Max allowed regression |
|---|---|---:|
| `<e.g. p95_latency_ms>` | `lower` | `3%` |
| `<e.g. secondary_throughput>` | `higher` | `3%` |
| `<e.g. peak_memory_mib>` | `lower` | `hard cap: <N> MiB` |

## Allowed & Forbidden Trades

- **Allowed:** <e.g. lossless config/algorithmic/kernel optimizations>
- **Forbidden without explicit sign-off:** <e.g. lossy quantization, reduced visual quality, skipped checks>
- **Locked verifier files:** `verify.sh`, `<fixtures...>` (locked in `.verifier.sha256` — climber may run, never edit)
