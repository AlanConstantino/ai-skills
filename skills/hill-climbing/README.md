# hill-climbing

A domain-agnostic **meta-skill** for **Hill-Climbing Engineering**: turning messy, continuous, noisy optimization and systems-engineering problems into a climbable mountain that *any* AI model can push toward its physical ceiling — with receipts.

## Why hill-climbing?

| Paradigm | Core question | Where it hits a wall |
|---|---|---|
| **Prompt Engineering** | *How do I word this?* | Breaks on multi-step state |
| **Context Engineering** | *What should the model see?* | Doesn't control execution |
| **Harness Engineering** | *What tools & guardrails surround it?* | Doesn't decide how to optimize |
| **Loop Engineering** | *How does it retry until tests pass?* | Assumes "done" is a **boolean** |
| **Graph Engineering** | *How do multiple agents route work?* | Organizes flow, not measurement |
| **Hill-Climbing Engineering** | *How do we push a noisy metric toward its physical limit without fooling ourselves?* | Needs a trustworthy verifier + countable signal |

Loop engineering tells an agent to keep trying until the test suite turns green. **Hill-Climbing Engineering** is what you need when tests already pass (or bugs are fuzzy), hardware drifts ±10–20%, averages hide regressions, and you want to push a real system toward its physical limit without models hallucinating progress or gaming their own benchmarks.

The core idea in one line:

> **Don't engineer the climber — engineer the hill.**  
> Compute the physical ceiling first, lock an uncheatable verifier, sweep zero-code levers before touching source, bracket every experiment against noise (`A → B → A` or `ABAB`), and write every win and dead end to disk so *any* model can resume the climb cold.

## Born from real climbs in two different worlds

This meta-skill distills the exact shared structure behind two sibling skills in this repository and the real climbs that produced them:

- **[`inference-optimisation`](../inference-optimisation/)** (inspired by Sybil Solutions) + local Metal LLM inference climbs: 15 zero-code switch sweeps proved stock defaults already beat every env knob; interleaved `ABAB` controls caught and reverted 4 tempting drafter fine-tunes that regressed sampled prose; lossless M5 Pro Metal 4 kernels then cleared a bitwise `logit_compare` gate for `+6.6%` greedy / `+9.2%` sampled (`+11.2%` bench) speedups.
- **[`android-game-porting`](../android-game-porting/)** + native Switch-to-Android climbs (*NieR:Automata* on Retroid Pocket 6): turning "dips in combat" into countable hitches (`>40 ms`) cut hitches from **66–75 → 1** per 90s live-toggled window (even while median `p50` barely moved); batching GS splits collapsed render passes by **91–94%** (`1,049 → 59` in the heaviest hub) to lock 5 of 6 zones at **99.8–100% 60 fps** with byte-identical user saves.

Different languages, different hardware, different bugs — **identical hill-climbing machinery**.

## Layout

```text
hill-climbing/
├── SKILL.md                          # Master runbook (Mode 1: Climb, Mode 2: Forge a domain skill)
├── README.md                         # This overview
├── LICENSE                           # MIT
├── references/
│   ├── building-the-hill.md          # Phase 1: ceilings, countable metrics, locked verifiers, anti-cheating
│   ├── methodology.md                # Noise control, A->B->A & ABAB ratchets, guardrails, measurement traps
│   └── meta-skill-guide.md           # How to turn a finished climb into a standalone domain skill
├── scripts/
│   ├── climb.py                      # Stdlib CLI: init, lock/check-verifier, record, compare, compare-abab, plateau, show
│   └── queue.sh                      # Portable macOS/Linux lock-file queue for serialized hardware runs
├── examples/
│   ├── objective-template.md         # Starter OBJECTIVE.md
│   ├── plan-template.md              # Starter PLAN.md
│   ├── dead-ends-template.md         # Starter DEAD_ENDS.md
│   └── verify-template.sh            # Verifier script contract template
└── tests/
    └── test_climb.py                 # Offline stdlib unit tests (Python 3.9+)
```

## Quickstart

Zero external dependencies — Python 3.9+ standard library and POSIX bash only.

```bash
# 1. Scaffold a hill-climbing workspace
python3 scripts/climb.py init ./my-climb

# 2. Write your read-only verify.sh (and fixtures), then SHA-256 lock them
python3 scripts/climb.py lock-verifier ./my-climb ./my-climb/verify.sh

# 3. Verify integrity before/during runs
python3 scripts/climb.py check-verifier ./my-climb

# 4. Record baseline and candidate runs into ledger.jsonl
python3 scripts/climb.py record ./my-climb/ledger.jsonl \
  --name ctl-a --role control --metric tps --samples 74.2,75.1,74.8 --gate-pass

python3 scripts/climb.py record ./my-climb/ledger.jsonl \
  --name cand-1 --role candidate --metric tps --samples 80.9,81.4,81.0 --gate-pass \
  --lever "qmm8t2p2 kernel" --guardrail prefill_tps=745.0

python3 scripts/climb.py record ./my-climb/ledger.jsonl \
  --name ctl-b --role control --metric tps --samples 74.5,75.0,74.9 --gate-pass

# 5. Judge the triplet (A -> B -> A) with strict noise & guardrail checks
python3 scripts/climb.py compare ./my-climb/ledger.jsonl ctl-a cand-1 ctl-b \
  --metric tps --direction higher --min-gain-pct 3.0 \
  --guardrail prefill_tps:higher:3.0

# 6. Or judge an interleaved ABAB sequence (A1 B1 A2 B2)
python3 scripts/climb.py compare-abab ./my-climb/ledger.jsonl \
  --metric frame_p95_ms --direction lower --min-gain-pct 3.0 \
  --legs ctl-1,cand-1,ctl-2,cand-2

# 7. Show scoreboard (% of physical ceiling) and check for plateau
python3 scripts/climb.py show ./my-climb/ledger.jsonl --metric tps --direction higher --ceiling 95.0
python3 scripts/climb.py plateau ./my-climb/ledger.jsonl --n 3

# 8. Run the offline unit test suite
python3 -m unittest discover -s tests -v
```

## Credits

Methodology synthesizes lessons from [Alan Constantino](https://github.com/AlanConstantino)'s `android-game-porting` and local Kapitsa Metal inference climbs, alongside Sybil Solutions' [`inference-optimisation`](https://github.com/sybil-solutions/inference-optimisation-skill) skill (MIT, © 2026 Sybil Solutions contributors).
