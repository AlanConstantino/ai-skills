# ai-skills

A collection of useful skills for AI coding agents (Claude Code, Codex, opencode, pi,
Google Antigravity, T3 Code and anything else that loads `SKILL.md`-style skills).

Each skill is a self-contained folder under [`skills/`](skills/).

## Skills

| Skill | What it does | Author | License |
|---|---|---|---|
| [hill-climbing](skills/hill-climbing/) | Domain-agnostic meta-skill for Hill-Climbing Engineering: ceilings first, countable metrics, locked verifiers, interleaved A/B ratchets, disk-persisted dead ends, and forging domain-specific skills. | [Alan Constantino](https://github.com/AlanConstantino) | MIT |
| [web-searx](skills/web-searx/) | Web search and page reading through your own SearXNG instance. Standard library only. | [Alan Constantino](https://github.com/AlanConstantino) | MIT |
| [inference-optimisation](skills/inference-optimisation/) | Hill-climb the serving speed of a local LLM server: theoretical ceilings, controlled A/B runs, a results ledger, long-context gates. | [Sybil Solutions](https://github.com/sybil-solutions/inference-optimisation-skill) | MIT |
| [android-game-porting](skills/android-game-porting/) | Hill-climb a native Android game port from bring-up to full-speed playable: frame/RAM ceilings, countable bug bisection, controlled device A/B runs, a results ledger, release gates. | [Alan Constantino](https://github.com/AlanConstantino) | MIT |

## Install

```bash
git clone https://github.com/AlanConstantino/ai-skills.git ~/ai-skills
```

Then link the skills you want into your agent's skill folder:

| Agent | Command (per skill) |
|---|---|
| Claude Code | `ln -s ~/ai-skills/skills/<name> ~/.claude/skills/<name>` |
| Codex and other Agent Skills hosts | `ln -s ~/ai-skills/skills/<name> ~/.agents/skills/<name>` |
| opencode | `ln -s ~/ai-skills/skills/<name> ~/.config/opencode/skills/<name>` |
| [pi](https://pi.dev) | `pi install git:github.com/AlanConstantino/ai-skills` (installs all of them) |

## Credits

Skills written by other people are copied here with their license and a `SOURCE.md` that
links the original repository and the exact commit. Credit and copyright stay with their
authors; send fixes for those skills upstream.

- **inference-optimisation**: [sybil-solutions/inference-optimisation-skill](https://github.com/sybil-solutions/inference-optimisation-skill), MIT, © 2026 Sybil Solutions contributors.

## History

`web-searx` previously lived in its own repository (`AlanConstantino/web-searx`). Its
commit history was carried over into this repository.
