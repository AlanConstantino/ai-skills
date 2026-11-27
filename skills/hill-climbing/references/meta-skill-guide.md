# Meta-Skill Mode: Forging a Domain-Specific Hill-Climbing Skill

Use this guide when you have climbed a problem class once or twice (e.g., porting Android games, tuning local LLM servers, speeding up a Rust compiler, tuning Postgres queries, optimizing web Core Web Vitals) and want to turn the hard-won lessons into a standalone, reusable skill.

Good examples already in this collection:
- `skills/inference-optimisation/` — hill-climbing local LLM servers
- `skills/android-game-porting/` — hill-climbing native Android game ports

---

## 1. Keep the universal skeleton intact

Every domain hill-climb skill should keep the same five-section backbone in its `SKILL.md`:

1. **Inventory & preflight (Section 0):** targets, constraints, machine/environment inventory, safety backups, working directory (`PLAN.md`, `THEORY.md`, `ledger.jsonl`, `configs/`, `best-config`).
2. **Ceilings first (Section 1):** domain-specific formulas for physical/practical upper bounds — and a zero-dependency script that computes them from measured inputs.
3. **Non-negotiable ground rules (Section 2):** correctness before speed, zero-build switches first, one change per experiment, interleaved noise control, user/data safety, dead-ends logging.
4. **The loop + ranked levers (Section 3):** Ladder A (correctness/countable bug classes) → Ladder B (performance levers ordered by payoff ÷ cost) → failure diagnostics → plateau rule.
5. **Qualification & honest reporting (Sections 4–5):** clean pinned rebuild, soak/equivalence gates, environment restore, baseline-vs-best-vs-ceiling report, and explicit "not verified" section.

---

## 2. What to specialize for the new domain

Don't write generic advice — fill each reference file with concrete, measured facts from real climbs:

- **`references/ceilings.md`:** exact formulas for that domain's physical limits, with at least one fully worked numeric example.
- **`references/methodology.md`:** the domain's specific countable metrics table, exact noise sources and confounders, and measurement traps that actually fooled you.
- **`references/levers.md`:** ranked bug classes (Ladder A) and optimization levers (Ladder B), tagged `[G]` general vs. `[S: stack/hardware]`, with **real measured ranges** (e.g., `-25–40% GPU`, `2× c1 decode`) and when a lever *doesn't* work.
- **`references/gotchas.md`:** the sharp edges, broken counters, driver/runtime quirks, and safety hazards discovered the hard way.
- **`references/qualification.md`:** the exact clean-build, regression, soak, and release checklist for that domain.

---

## 3. Build tiny, zero-dependency scripts + offline tests

Follow three rules for scripts:

1. **Standard library only** (Python 3.9+ / POSIX bash) so any agent on any machine can run them with zero setup.
2. **Automate the boring, error-prone parts:** ceiling math, environment health checks, log parsing, interleaved A/B comparison, ledger formatting, and hardware lock queuing.
3. **Ship offline unit tests (`tests/test_*.py`)** using synthetic logs, configs, and fixtures so CI validates every script without needing live GPUs, phones, or production servers.

---

## 4. Include one real worked case study

Put at least one real, end-to-end climb in `examples/case-*.md`:
- countable baseline numbers;
- ranked lever hypothesis list;
- diagnostic proof of root cause;
- real interleaved A/B numbers (including dead ends if instructive);
- final clean qualification and reusable lessons.

One honest case study teaches an agent more about discipline than ten pages of abstract prose.

---

## 5. Hygiene check before publishing

- Strip all personal paths, secrets, proprietary dumps, and machine-specific hostnames.
- Make sure every example config and fixture runs cleanly offline.
- Verify `SKILL.md` frontmatter (`name`, `description` with clear trigger phrases, `license`, `compatibility`).
- Run the offline unit test suite (`python3 -m unittest discover -s tests -v`).
