# Changelog

This project is under development. Until 1.0.0, command options and output format may change between minor versions.

## [0.1.0] - 2026-11-15

First public pre-release.

- `search.py`: queries SearXNG and prints 1-10 cleaned results. It removes duplicates, shows failed engines, and supports `--news`, `--time-range`, `--categories`, `--language`, `--page` and `--json`.
- `fetch.py`: reads a page as clean text in fixed-size pieces, with an exact command for the next piece. It blocks private and local addresses (including after redirects), caps downloads at 5 MB, and reads PDFs when `pypdf` is installed.
- `SKILL.md` for Claude Code, Codex, pi and other Agent Skills hosts.
- Offline test suite with golden output files; optional live tests.
