# web-searx

> **Status: under development (v0.1.0, pre-release).** It works and is tested, but command
> options and output format may still change before 1.0. Pin a tag if you depend on it.

A web search skill for AI agents, frontier and local, backed by your own
[SearXNG](https://docs.searxng.org/) instance. No API keys and no third-party search
service; only Python's standard library.

It gives an agent two commands:

```bash
python3 scripts/search.py "rust 1.90 release notes" --time-range month
python3 scripts/fetch.py https://blog.rust-lang.org/
```

- **`search.py`** asks SearXNG and prints 1-10 results (title, URL, date, short snippet).
  Duplicates are removed and failed search engines are listed.
- **`fetch.py`** reads one page as clean text: navigation, sidebars, cookie banners and
  scripts are removed, and headings, lists, tables and code blocks are kept. Long pages
  come in fixed-size pieces, and each piece ends with the exact command for the next one.

## Why

Search tools built for large models often return big, loosely formatted output, which
small local models handle badly. Here the scripts make every decision that doesn't need
judgment: result count, snippet length, duplicate removal, text extraction and where long
pages are split. The model only picks the search words and which page to read.

The output is deterministic: the same SearXNG response or the same page HTML always
produces byte-identical output. The web itself still changes, so results do too.

## Requirements

- Python 3.9 or newer (the macOS system `python3` works).
- A SearXNG instance with JSON output enabled. In SearXNG's `settings.yml`:

  ```yaml
  search:
    formats:
      - html
      - json
  ```

  Without it, SearXNG answers `403 Forbidden` and `search.py` says so.
- Optional: `pypdf`, to read PDF files.

## Install

Clone the repository, then make the `skills/web-searx` folder visible to your agent.

```bash
git clone https://github.com/AlanConstantino/web-searx.git ~/web-searx
```

| Agent | Install |
|---|---|
| Claude Code | `ln -s ~/web-searx/skills/web-searx ~/.claude/skills/web-searx` |
| Codex and other Agent Skills hosts | `ln -s ~/web-searx/skills/web-searx ~/.agents/skills/web-searx` |
| [pi](https://pi.dev) | `pi install git:github.com/AlanConstantino/web-searx@v0.1.0`, or the `~/.agents/skills` link above |
| Anything that runs shell commands | Point the model at `skills/web-searx/SKILL.md` |

## Configure

Tell the scripts where SearXNG is:

```bash
export SEARXNG_URL=http://localhost:8080
```

The address is taken from, in order:

1. `--base-url` on the command line
2. `$SEARXNG_URL`
3. `baseUrls.searxng` in `~/.config/rpiv-web-tools/config.json`, the file written by the
   pi plugin [rpiv-web-tools](https://github.com/juicesharp/rpiv-mono/tree/main/packages/rpiv-web-tools),
   so pi users who already configured it need nothing else

If SearXNG sits behind a proxy that checks a Bearer token, set `SEARXNG_API_KEY`.

## Usage

### search.py

```
python3 scripts/search.py QUERY [options]

  --news                       news sources; use for "latest" / "this week" questions
  --time-range day|week|month|year
  --max-results N              1-10, default 5
  --categories it,science      SearXNG categories
  --language en                language code
  --page N                     result page
  --timeout S                  seconds, default 15
  --json                       structured output
```

Example output:

```
Search results for "python 3.14 release date": 2 results

1. Python Release Python 3.14.0 | Python.org
   https://www.python.org/downloads/release/python-3140/
   Python 3.14.0 is the newest major release of the Python programming language...

2. PEP 745 – Python 3.14 Release Schedule | peps.python.org
   https://peps.python.org/pep-0745/
   Python 3.14 will receive bugfix updates approximately every two months...

Note: some search engines failed, so results may be incomplete: duckduckgo (CAPTCHA)

Snippets are previews. To read a page in full, run:
  python3 /path/to/scripts/fetch.py URL
```

### fetch.py

```
python3 scripts/fetch.py URL [options]

  --max-chars N      characters per piece, default 8000 (500-100000)
  --offset N         where to start; the footer of each piece gives the next value
  --raw              page source instead of extracted text
  --refresh          ignore the 15-minute cache
  --allow-private    allow pages on this machine or the local network
  --timeout S        seconds, default 20
  --json             structured output
```

Pages of one URL are cached for 15 minutes, so every piece comes from the same download.
Set `WEB_SEARX_CACHE_DIR` to choose the cache folder.

### Errors

On failure, scripts exit with a non-zero code and print `ERROR:` and `HINT:` lines to
stderr, or `{"ok": false, ...}` with `--json`. Exit code 1 means a network or site
problem; 2 means bad arguments or a blocked address.

## Safety

- Page text is printed between markers that tell the model it is untrusted data.
- `fetch.py` refuses private, loopback, link-local and other non-public addresses. It
  resolves the host name and checks it again on every redirect. A DNS answer can still
  change between the check and the connection, so use a firewall in front if the agent
  runs unattended.
- Downloads stop at 5 MB. Only http and https URLs are accepted.

## Tests

```bash
cd tests
python3 -m unittest                                # offline: local server and fixtures
WEB_SEARX_LIVE=1 python3 -m unittest test_live     # against your SearXNG and the public web
UPDATE_GOLDEN=1 python3 -m unittest                # rewrite golden files after an intended change
```

## Credits

The tool design follows the SearXNG path of
[rpiv-web-tools](https://github.com/juicesharp/rpiv-mono/tree/main/packages/rpiv-web-tools)
by juicesharp. This project is an independent Python implementation.

## License

[MIT](LICENSE)
