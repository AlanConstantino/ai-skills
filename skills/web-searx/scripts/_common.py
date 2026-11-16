"""Shared helpers for search.py and fetch.py: config, errors, URL handling, text cleanup.

Standard library only; runs on Python 3.9+.
"""

from __future__ import annotations

import html
import json
import os
import re
import shlex
import sys
import urllib.parse
from pathlib import Path

VERSION = "0.1.1"
USER_AGENT = "Mozilla/5.0 (compatible; web-searx-skill/0.1)"
SCRIPTS_DIR = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_FAILED = 1  # network, upstream, or content problem
EXIT_USAGE = 2  # bad arguments

SEARXNG_URL_ENV = "SEARXNG_URL"
SEARXNG_API_KEY_ENV = "SEARXNG_API_KEY"

# Query parameters that only track the visitor; dropped when comparing URLs.
TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref_src", "_ga"}
TRACKING_PREFIXES = ("utm_",)

_WHITESPACE_RUN = re.compile(r"\s+")


class ToolError(Exception):
    """A failure the calling model should see as one clear line plus an actionable hint."""

    def __init__(self, message: str, hint: str = "", exit_code: int = EXIT_FAILED):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.exit_code = exit_code


def report_error(err: ToolError, as_json: bool) -> int:
    """Print the error where the caller will see it and return the exit code."""
    if as_json:
        payload = {"ok": False, "error": err.message}
        if err.hint:
            payload["hint"] = err.hint
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        lines = [f"ERROR: {err.message}"]
        if err.hint:
            lines.append(f"HINT: {err.hint}")
        print("\n".join(lines), file=sys.stderr)
    return err.exit_code


# ---------------------------------------------------------------------------
# Config: SearXNG URL and optional Bearer key
# ---------------------------------------------------------------------------


def rpiv_config_path() -> Path:
    """Config file written by the pi plugin's /web-tools command (shared so both agree)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "rpiv-web-tools" / "config.json"


def _read_rpiv_config() -> dict:
    try:
        data = json.loads(rpiv_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _config_value(section: str) -> str:
    value = _read_rpiv_config().get(section)
    if isinstance(value, dict):
        entry = value.get("searxng")
        if isinstance(entry, str):
            return entry.strip()
    return ""


def resolve_base_url(cli_value: str | None) -> str:
    """--base-url, then $SEARXNG_URL, then the pi plugin's config file."""
    for candidate in (cli_value or "", os.environ.get(SEARXNG_URL_ENV, ""), _config_value("baseUrls")):
        candidate = candidate.strip()
        if candidate:
            return validate_http_url(candidate, label="SearXNG URL").rstrip("/")
    raise ToolError(
        "no SearXNG URL configured",
        hint=f"set {SEARXNG_URL_ENV} (e.g. export {SEARXNG_URL_ENV}=http://localhost:8080) or pass --base-url",
        exit_code=EXIT_USAGE,
    )


def resolve_api_key() -> str:
    """Optional Bearer token for instances behind an auth proxy."""
    return os.environ.get(SEARXNG_API_KEY_ENV, "").strip() or _config_value("apiKeys")


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def validate_http_url(url: str, label: str = "URL") -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"{label} must start with http:// or https:// (got: {url})", exit_code=EXIT_USAGE)
    if not parsed.hostname:
        raise ToolError(f"{label} has no host name (got: {url})", exit_code=EXIT_USAGE)
    return url


def canonical_url(url: str) -> str:
    """Comparison key for duplicate detection. Never shown to the model."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith(TRACKING_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    # http and https versions of a page count as the same result.
    return urllib.parse.urlunsplit(("", host, path, urllib.parse.urlencode(sorted(query)), ""))


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def clean_inline(text: str) -> str:
    """Decode entities and collapse all whitespace to single spaces."""
    return _WHITESPACE_RUN.sub(" ", html.unescape(text or "")).strip()


def truncate_words(text: str, limit: int) -> str:
    """Cut at the last word boundary before `limit` characters and mark the cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-") + "…"


def script_command(script: str, *args: str) -> str:
    """An exact, copy-pasteable command line for one of the sibling scripts."""
    return " ".join(["python3", shlex.quote(str(SCRIPTS_DIR / script)), *(shlex.quote(a) for a in args)])
