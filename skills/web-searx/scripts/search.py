#!/usr/bin/env python3
"""Search the web through a SearXNG instance and print a short, fixed-format result list.

    python3 search.py "query" [--max-results 5] [--time-range week] [--news] [--json]

Every decision after the HTTP response is deterministic: the same SearXNG response
always produces byte-identical output.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

from _common import (
    EXIT_OK,
    EXIT_USAGE,
    USER_AGENT,
    VERSION,
    ToolError,
    canonical_url,
    clean_inline,
    report_error,
    resolve_api_key,
    resolve_base_url,
    script_command,
    truncate_words,
)

MIN_RESULTS = 1
MAX_RESULTS = 10
DEFAULT_RESULTS = 5
SNIPPET_CHARS = 300
INFOBOX_CHARS = 400
MAX_ANSWERS = 2
MAX_SUGGESTIONS = 5
DEFAULT_TIMEOUT = 15.0
TIME_RANGES = ("day", "week", "month", "year")
TIME_RANGE_LABELS = {"day": "past day", "week": "past week", "month": "past month", "year": "past year"}

_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def build_search_url(
    base_url: str,
    query: str,
    *,
    time_range: str = "",
    categories: str = "",
    language: str = "",
    page: int = 1,
) -> str:
    # Fixed parameter order keeps the request itself reproducible.
    params = [("q", query), ("format", "json"), ("safesearch", "0")]
    if time_range:
        params.append(("time_range", time_range))
    if categories:
        params.append(("categories", categories))
    if language:
        params.append(("language", language))
    if page > 1:
        params.append(("pageno", str(page)))
    return f"{base_url}/search?{urllib.parse.urlencode(params)}"


def _status_hint(status: int) -> str:
    if status == 401:
        return "the proxy in front of SearXNG rejected the Bearer token; check SEARXNG_API_KEY"
    if status == 403:
        return "SearXNG has JSON output disabled; add 'json' under search.formats in its settings.yml"
    if status == 429:
        return "SearXNG is rate-limiting this client; wait a minute or relax its limiter settings"
    if status >= 500:
        return "SearXNG itself failed; check its logs"
    return ""


def request_json(url: str, api_key: str, timeout: float) -> dict:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    base = url.split("/search?", 1)[0]
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise ToolError(f"SearXNG returned HTTP {exc.code}", hint=_status_hint(exc.code)) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise ToolError(
                f"SearXNG did not answer within {timeout:g}s",
                hint="the instance or its engines are slow; retry once, or pass a larger --timeout",
            ) from exc
        raise ToolError(
            f"cannot reach SearXNG at {base} ({exc.reason})",
            hint="check that SearXNG is running and that SEARXNG_URL is right",
        ) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ToolError(
            f"SearXNG did not answer within {timeout:g}s",
            hint="the instance or its engines are slow; retry once, or pass a larger --timeout",
        ) from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ToolError(
            "SearXNG did not return JSON",
            hint="the URL may not point at SearXNG, or 'json' is missing from search.formats in settings.yml",
        ) from exc
    if not isinstance(payload, dict):
        raise ToolError("SearXNG returned an unexpected JSON value")
    return payload


# ---------------------------------------------------------------------------
# Normalization (pure functions; the deterministic core)
# ---------------------------------------------------------------------------


def _published_date(item: dict) -> str:
    for key in ("publishedDate", "pubdate"):
        value = item.get(key)
        if isinstance(value, str):
            match = _DATE_PREFIX.match(value.strip())
            if match:
                return match.group(1)
    return ""


def _engines(item: dict) -> list:
    names = [str(name) for name in item.get("engines") or [] if name]
    engine = item.get("engine")
    if engine and engine not in names:
        names.insert(0, str(engine))
    return names


def normalize_results(raw_results: list, max_results: int) -> list:
    results = []
    seen = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        key = canonical_url(url)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "rank": len(results) + 1,
                "title": clean_inline(str(item.get("title") or "")) or "(no title)",
                "url": url,
                "snippet": truncate_words(clean_inline(str(item.get("content") or "")), SNIPPET_CHARS),
                "published": _published_date(item),
                "engines": _engines(item),
            }
        )
        if len(results) >= max_results:
            break
    return results


def normalize_answers(raw_answers: list) -> list:
    answers = []
    for item in raw_answers or []:
        if isinstance(item, dict):
            text = clean_inline(str(item.get("answer") or ""))
            url = str(item.get("url") or "").strip()
        else:
            text, url = clean_inline(str(item)), ""
        if text and all(text != existing["text"] for existing in answers):
            answers.append({"text": truncate_words(text, SNIPPET_CHARS), "url": url})
        if len(answers) >= MAX_ANSWERS:
            break
    return answers


def normalize_infobox(raw_infoboxes: list) -> dict | None:
    for item in raw_infoboxes or []:
        if not isinstance(item, dict):
            continue
        title = clean_inline(str(item.get("infobox") or ""))
        content = truncate_words(clean_inline(str(item.get("content") or "")), INFOBOX_CHARS)
        if not (title and content):
            continue
        url = str(item.get("id") or "").strip()
        if not url.startswith(("http://", "https://")):
            url = ""
            for link in item.get("urls") or []:
                if isinstance(link, dict) and str(link.get("url") or "").startswith(("http://", "https://")):
                    url = str(link["url"])
                    break
        return {"title": title, "content": content, "url": url}
    return None


def normalize_suggestions(payload: dict) -> list:
    out = []
    for key in ("corrections", "suggestions"):
        for item in payload.get(key) or []:
            text = clean_inline(str(item))
            if text and text not in out:
                out.append(text)
    return out[:MAX_SUGGESTIONS]


def normalize_failed_engines(raw: list) -> list:
    failed = {}
    for item in raw or []:
        if isinstance(item, (list, tuple)) and item:
            name = str(item[0])
            reason = clean_inline(str(item[1])) if len(item) > 1 else "no response"
            failed.setdefault(name, reason)
    return [{"engine": name, "reason": failed[name]} for name in sorted(failed)]


def normalize_response(payload: dict, query: str, max_results: int, filters: dict) -> dict:
    return {
        "ok": True,
        "query": query,
        "filters": filters,
        "results": normalize_results(payload.get("results") or [], max_results),
        "answers": normalize_answers(payload.get("answers") or []),
        "infobox": normalize_infobox(payload.get("infoboxes") or []),
        "suggestions": normalize_suggestions(payload),
        "failed_engines": normalize_failed_engines(payload.get("unresponsive_engines") or []),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _filter_label(filters: dict) -> str:
    parts = []
    if filters.get("time_range"):
        parts.append(TIME_RANGE_LABELS[filters["time_range"]])
    if filters.get("categories"):
        parts.append(filters["categories"])
    if filters.get("language"):
        parts.append(filters["language"])
    if filters.get("page", 1) > 1:
        parts.append(f"page {filters['page']}")
    return f" ({', '.join(parts)})" if parts else ""


def render_text(data: dict) -> str:
    query = data["query"]
    results = data["results"]
    lines = []
    count = len(results)
    if count:
        noun = "result" if count == 1 else "results"
        lines.append(f'Search results for "{query}"{_filter_label(data["filters"])}: {count} {noun}')
    else:
        lines.append(f'No results found for "{query}"{_filter_label(data["filters"])}.')
    lines.append("")

    for answer in data["answers"]:
        source = f" ({answer['url']})" if answer["url"] else ""
        lines.append(f"Direct answer: {answer['text']}{source}")
    infobox = data["infobox"]
    if infobox:
        source = f" ({infobox['url']})" if infobox["url"] else ""
        lines.append(f"Summary - {infobox['title']}: {infobox['content']}{source}")
    if data["answers"] or infobox:
        lines.append("")

    for item in results:
        lines.append(f"{item['rank']}. {item['title']}")
        lines.append(f"   {item['url']}")
        if item["published"]:
            lines.append(f"   Published: {item['published']}")
        if item["snippet"]:
            lines.append(f"   {item['snippet']}")
        lines.append("")

    if not results and data["suggestions"]:
        lines.append("Try instead: " + "; ".join(f'"{s}"' for s in data["suggestions"]))
        lines.append("")
    if data["failed_engines"]:
        failed = "; ".join(f"{e['engine']} ({e['reason']})" for e in data["failed_engines"])
        lines.append(f"Note: some search engines failed, so results may be incomplete: {failed}")
        lines.append("")
    if results:
        lines.append("Snippets are previews. To read a page in full, run:")
        lines.append(f"  {script_command('fetch.py', 'URL')}")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search the web through SearXNG.",
        epilog='Example: search.py "rust 1.90 release notes" --time-range month',
    )
    parser.add_argument("query", nargs="+", help="search words; quotes are optional")
    parser.add_argument(
        "--max-results",
        type=int,
        default=DEFAULT_RESULTS,
        help=f"{MIN_RESULTS}-{MAX_RESULTS}, default {DEFAULT_RESULTS} (out-of-range values are clamped)",
    )
    parser.add_argument("--time-range", choices=TIME_RANGES, default="", help="only results from this period")
    parser.add_argument("--news", action="store_true", help="search news sources (use for 'latest' / 'this week')")
    parser.add_argument("--categories", default="", help="SearXNG categories, comma-separated (e.g. it,science)")
    parser.add_argument("--language", default="", help="language code such as en or de")
    parser.add_argument("--page", type=int, default=1, help="result page, default 1")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"seconds, default {DEFAULT_TIMEOUT:g}")
    parser.add_argument("--base-url", default=None, help="SearXNG URL (default: $SEARXNG_URL, then pi's config)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser.parse_args(argv)


def _categories(args: argparse.Namespace) -> str:
    names = [c.strip() for c in args.categories.split(",") if c.strip()]
    if args.news and "news" not in names:
        names.insert(0, "news")
    return ",".join(names)


def run(args: argparse.Namespace) -> dict:
    query = " ".join(" ".join(args.query).split())
    if not query:
        raise ToolError("the search query is empty", exit_code=EXIT_USAGE)
    if args.page < 1:
        raise ToolError("--page must be 1 or more", exit_code=EXIT_USAGE)
    if args.timeout <= 0:
        raise ToolError("--timeout must be more than 0", exit_code=EXIT_USAGE)
    max_results = min(max(args.max_results, MIN_RESULTS), MAX_RESULTS)
    filters = {
        "time_range": args.time_range,
        "categories": _categories(args),
        "language": args.language.strip(),
        "page": args.page,
    }
    base_url = resolve_base_url(args.base_url)
    url = build_search_url(base_url, query, **filters)
    payload = request_json(url, resolve_api_key(), args.timeout)
    return normalize_response(payload, query, max_results, filters)


def main(argv: list | None = None) -> int:
    args = parse_args(argv)
    try:
        data = run(args)
    except ToolError as err:
        return report_error(err, args.json)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(render_text(data))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
