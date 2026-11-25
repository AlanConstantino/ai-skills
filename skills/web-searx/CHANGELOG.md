# Changelog

This project is under development. Until 1.0.0, command options and output format may change between minor versions.

## [0.1.1] - 2026-11-16

Fixes weak results for news questions such as "latest AI news this week", found by testing with a local model in pi.

- Results now show publication dates that news engines send as relative text, as in `Published: 2 days ago (CNBC)`. These were dropped before, so models could not tell new stories from old ones. Relative dates are passed through unchanged, so output still depends only on the SearXNG response.
- When `--time-range` leaves only one search engine answering, `search.py` prints a note to search again without it. Many SearXNG engines skip time-filtered requests, so `--news --time-range week` often returned Bing News results only.
- JSON output adds `source` to each result and a top-level `engines_used` list.
- `SKILL.md`: use `--news` without `--time-range`, search specific names rather than "AI" or "latest", run 2-3 searches for a roundup, and skip results with old dates.

## [0.1.0] - 2026-11-15

First public pre-release.

- `search.py`: queries SearXNG and prints 1-10 cleaned results. It removes duplicates, shows failed engines, and supports `--news`, `--time-range`, `--categories`, `--language`, `--page` and `--json`.
- `fetch.py`: reads a page as clean text in fixed-size pieces, with an exact command for the next piece. It blocks private and local addresses (including after redirects), caps downloads at 5 MB, and reads PDFs when `pypdf` is installed.
- `SKILL.md` for Claude Code, Codex, pi and other Agent Skills hosts.
- Offline test suite with golden output files; optional live tests.
