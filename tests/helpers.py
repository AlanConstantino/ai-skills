"""Test helpers: import path, a local HTTP server, CLI runner and golden-file checks."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "web-searx" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = Path(__file__).resolve().parent / "golden"
sys.path.insert(0, str(SCRIPTS))

BIG_PAGE_BYTES = 5_200_000


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class _Handler(BaseHTTPRequestHandler):
    """Routes for both the fake SearXNG (/search) and the pages fetch.py reads."""

    pages = {}  # path -> (status, content_type, body); tests may change entries

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status: int, content_type: str, body: bytes, headers: dict | None = None):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/search":
            return self._search(urllib.parse.parse_qs(parsed.query))
        if parsed.path == "/redirect":
            return self._send(302, "text/plain", b"", {"Location": "/article.html"})
        if parsed.path == "/loop":
            return self._send(302, "text/plain", b"", {"Location": "/loop"})
        if parsed.path == "/big.txt":
            return self._send(200, "text/plain", b"x" * BIG_PAGE_BYTES)
        if parsed.path in self.pages:
            return self._send(*self.pages[parsed.path])
        self._send(404, "text/plain", b"not found")

    def _search(self, params: dict):
        self.server.last_search_query = params
        q = params.get("q", [""])[0]
        if q.startswith("status"):
            return self._send(int(q[6:]), "text/plain", b"error body")
        if q == "notjson":
            return self._send(200, "text/html", b"<html>not json</html>")
        if q == "slow":
            time.sleep(1.5)
        if q == "news":
            return self._send(200, "application/json", fixture_bytes("searxng_news_week.json"))
        if q == "empty":
            body = {"query": q, "results": [], "suggestions": ["emptier", "empty set"],
                    "corrections": ["empt"], "unresponsive_engines": [["google", "CAPTCHA"]]}
            return self._send(200, "application/json", json.dumps(body).encode())
        self._send(200, "application/json", fixture_bytes("searxng_synthetic.json"))


def default_pages() -> dict:
    article = fixture_bytes("article.html")
    return {
        "/article.html": (200, "text/html; charset=utf-8", article),
        "/plain.txt": (200, "text/plain", b"line one  \r\nline two\r\n\r\n\r\n\r\nline three\n"),
        "/data.json": (200, "application/json", b'{"a": 1}'),
        "/image.png": (200, "image/png", b"\x89PNG\r\n\x1a\n"),
        "/latin1.html": (
            200,
            "text/html",
            '<html><head><meta charset="iso-8859-1"><title>Caf\xe9</title></head>'
            "<body><p>Cr\xe8me br\xfbl\xe9e</p></body></html>".encode("latin-1"),
        ),
        "/js-only.html": (200, "text/html", b'<html><body><div id="app"></div><script>render()</script></body></html>'),
        "/untyped": (200, "", b"<!DOCTYPE html><html><body><p>Sniffed as HTML.</p></body></html>"),
        "/long.html": (
            200,
            "text/html",
            ("<html><body><main>"
             + "".join(f"<p>Paragraph {i} " + "word " * 60 + "</p>" for i in range(40))
             + "</main></body></html>").encode(),
        ),
    }


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # clients that time out on purpose cause broken pipes here


class ServerTestCase(unittest.TestCase):
    """Starts one local server per test class and isolates config and cache per test."""

    @classmethod
    def setUpClass(cls):
        _Handler.pages = default_pages()
        cls.server = _QuietServer(("127.0.0.1", 0), _Handler)
        cls.server.last_search_query = {}
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.pages = default_pages()
        self.tmp = self.enterContext_compat(_temp_dir())
        env = {
            "XDG_CONFIG_HOME": str(Path(self.tmp) / "config"),
            "WEB_SEARX_CACHE_DIR": str(Path(self.tmp) / "cache"),
            "SEARXNG_URL": "",
            "SEARXNG_API_KEY": "",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def enterContext_compat(self, cm):
        # unittest.TestCase.enterContext only exists on Python 3.11+.
        value = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        return value

    def set_page(self, path: str, status: int, content_type: str, body: bytes):
        _Handler.pages[path] = (status, content_type, body)


@contextlib.contextmanager
def _temp_dir():
    import tempfile

    with tempfile.TemporaryDirectory() as path:
        yield path


def run_cli(main, argv: list) -> tuple:
    """Run a script's main() in-process; return (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:  # argparse errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def stable(text: str, base_url: str = "") -> str:
    """Replace machine-specific parts of output with placeholders for golden files."""
    text = text.replace(str(SCRIPTS), "<SCRIPTS>")
    if base_url:
        text = text.replace(base_url, "<SERVER>")
    return text


def assert_golden(case: unittest.TestCase, name: str, actual: str):
    """Compare with tests/golden/<name>; set UPDATE_GOLDEN=1 to rewrite the file."""
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        if os.environ.get("UPDATE_GOLDEN") != "1":
            case.fail(f"golden file {name} did not exist; it was created, review it and rerun")
    case.assertEqual(path.read_text(encoding="utf-8"), actual)
