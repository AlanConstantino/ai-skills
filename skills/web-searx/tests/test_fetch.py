from __future__ import annotations

import json
import random
import socket
import unittest
from unittest import mock

from helpers import ServerTestCase, assert_golden, run_cli, stable
from _common import ToolError
import fetch


def fake_dns(mapping: dict):
    """Patch getaddrinfo so host names resolve to the given addresses."""

    def resolver(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]]

    return mock.patch.object(fetch.socket, "getaddrinfo", side_effect=resolver)


class PageBoundsTest(unittest.TestCase):
    def tile(self, text: str, max_chars: int) -> list:
        pieces, offset = [], 0
        while offset < len(text):
            start, end = fetch.page_bounds(text, offset, max_chars)
            self.assertEqual(start, offset)
            self.assertGreater(end, start)
            self.assertLessEqual(end - start, max_chars)
            pieces.append(text[start:end])
            offset = end
        return pieces

    def test_pieces_tile_text_exactly(self):
        rng = random.Random(1234)  # fixed seed keeps this test deterministic
        alphabet = ["word", " ", "\n", "\n\n", "longerword", ".", "x" * 50]
        for _ in range(200):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 400)))
            max_chars = rng.randint(1, 300)
            self.assertEqual("".join(self.tile(text, max_chars)), text)

    def test_prefers_paragraph_then_line_then_space(self):
        text = "a" * 60 + "\n\n" + "b" * 20 + "\n" + "c" * 5 + " " + "d" * 50
        self.assertEqual(fetch.page_bounds(text, 0, 100), (0, 62))
        self.assertEqual(fetch.page_bounds(text, 62, 30), (62, 83))
        self.assertEqual(fetch.page_bounds(text, 83, 10), (83, 89))

    def test_hard_cut_when_no_separator(self):
        self.assertEqual(fetch.page_bounds("x" * 100, 0, 30), (0, 30))

    def test_last_piece(self):
        self.assertEqual(fetch.page_bounds("short text", 0, 100), (0, 10))


class AddressGuardTest(unittest.TestCase):
    def test_ip_classes(self):
        for address in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.5.4", "169.254.169.254",
                        "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1", "224.0.0.1"):
            self.assertFalse(fetch._ip_is_public(address), address)
        for address in ("93.184.216.34", "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"):
            self.assertTrue(fetch._ip_is_public(address), address)

    def test_any_private_answer_blocks_the_host(self):
        with fake_dns({"mixed.test": ["93.184.216.34", "10.0.0.5"], "public.test": ["93.184.216.34"]}):
            fetch.check_host_allowed("https://public.test/")
            with self.assertRaises(ToolError) as ctx:
                fetch.check_host_allowed("https://mixed.test/")
        self.assertIn("10.0.0.5", ctx.exception.message)

    def test_unresolvable_host(self):
        with mock.patch.object(fetch.socket, "getaddrinfo", side_effect=socket.gaierror("nope")):
            with self.assertRaises(ToolError) as ctx:
                fetch.check_host_allowed("https://no-such-host.test/")
        self.assertIn("cannot resolve", ctx.exception.message)

    def test_redirect_to_private_address_blocked(self):
        handler = fetch._GuardedRedirects(allow_private=False)
        request = fetch.urllib.request.Request("https://public.test/start")
        with fake_dns({"internal.test": ["192.168.0.10"]}):
            with self.assertRaises(ToolError):
                handler.redirect_request(request, None, 302, "Found", {}, "http://internal.test/admin")
        with self.assertRaises(ToolError):
            handler.redirect_request(request, None, 302, "Found", {}, "file:///etc/passwd")

    def test_input_url_normalization(self):
        self.assertEqual(fetch.normalize_input_url(" example.com/a "), "https://example.com/a")
        self.assertEqual(fetch.normalize_input_url("<https://example.com/a>"), "https://example.com/a")
        with self.assertRaises(ToolError):
            fetch.normalize_input_url("ftp://example.com")


class ToTextTest(unittest.TestCase):
    def test_blocked_types(self):
        for content_type in ("image/png", "video/mp4", "application/zip", "application/octet-stream"):
            with self.assertRaises(ToolError):
                fetch.to_text(b"data", content_type, raw=False)

    def test_text_types_pass_through(self):
        self.assertEqual(fetch.to_text(b'{"a": 1}', "application/json", raw=False), ("", '{"a": 1}'))
        self.assertEqual(fetch.to_text(b"a  \r\nb\r\n\r\n\r\nc", "text/plain", raw=False), ("", "a\nb\n\nc"))

    def test_raw_html_is_not_extracted(self):
        html = b"<html><body><p>x</p></body></html>"
        self.assertEqual(fetch.to_text(html, "text/html", raw=True)[1], html.decode())

    def test_charset_from_header_and_meta(self):
        body = "<p>Crème</p>".encode("latin-1")
        self.assertEqual(fetch.to_text(body, "text/html; charset=ISO-8859-1", raw=False)[1], "Crème")
        meta = b'<meta charset="windows-1252"><p>' + "café".encode("cp1252") + b"</p>"
        self.assertEqual(fetch.to_text(meta, "text/html", raw=False)[1], "café")
        bogus = b"<p>ok</p>"
        self.assertEqual(fetch.to_text(bogus, "text/html; charset=not-a-codec", raw=False)[1], "ok")


class FetchCliTest(ServerTestCase):
    def fetch(self, path, *argv):
        return run_cli(fetch.main, [self.base_url + path, "--allow-private", *argv])

    def test_article_matches_golden(self):
        code, out, err = self.fetch("/article.html")
        self.assertEqual((code, err), (0, ""))
        assert_golden(self, "fetch_article.txt", stable(out, self.base_url))

    def test_output_is_deterministic(self):
        runs = {self.fetch("/article.html", "--refresh")[1] for _ in range(3)}
        self.assertEqual(len(runs), 1)

    def test_paging_reassembles_the_page(self):
        code, out, _ = self.fetch("/long.html", "--json", "--max-chars", "1000")
        first = json.loads(out)
        self.assertEqual(first["start"], 0)
        self.assertEqual(code, 0)
        total, offset, pieces = first["total_chars"], 0, []
        while offset is not None:
            data = json.loads(self.fetch("/long.html", "--json", "--max-chars", "1000", "--offset", str(offset))[1])
            pieces.append(data)
            offset = data["next_offset"]
        self.assertGreater(len(pieces), 3)
        self.assertEqual(pieces[-1]["end"], total)
        for piece in pieces:
            self.assertTrue(piece["text"].startswith("Paragraph"), piece["text"][:30])  # cut at paragraph breaks

    def test_footer_gives_exact_next_command(self):
        _, out, _ = self.fetch("/long.html", "--max-chars", "1000")
        self.assertIn("more characters. To continue reading, run:", out)
        command = out.split("To continue reading, run:\n  ", 1)[1].rstrip("]\n")
        argv = command.split(" ")[2:]  # drop "python3 <path>"
        self.assertEqual(argv[1:3], ["--offset", argv[2]])
        self.assertIn("--max-chars", argv)
        self.assertIn("--allow-private", argv)
        code, out2, _ = run_cli(fetch.main, argv)
        self.assertEqual(code, 0)
        self.assertIn(f"Showing: characters {int(argv[2]):,}-", out2)

    def test_cache_keeps_pieces_consistent(self):
        self.fetch("/article.html")
        self.set_page("/article.html", 200, "text/html", b"<p>changed</p>")
        self.assertIn("Widget 2.0", self.fetch("/article.html")[1])
        self.assertIn("changed", self.fetch("/article.html", "--refresh")[1])

    def test_redirect_is_reported(self):
        code, out, _ = self.fetch("/redirect")
        self.assertEqual(code, 0)
        self.assertIn(f"Redirected to: {self.base_url}/article.html", out)

    def test_redirect_loop(self):
        code, _, err = self.fetch("/loop")
        self.assertEqual(code, 1)
        self.assertIn("redirects", err)

    def test_errors(self):
        cases = {
            "/missing": "HTTP 404",
            "/image.png": "cannot read image/png",
            "/js-only.html": "no readable text",
        }
        for path, message in cases.items():
            code, out, err = self.fetch(path)
            self.assertEqual(code, 1, path)
            self.assertIn(message, err)
            self.assertIn("HINT:", err)
            self.assertEqual(out, "")

    def test_other_content(self):
        self.assertIn("line one\nline two\n\nline three", self.fetch("/plain.txt")[1])
        self.assertIn('{"a": 1}', self.fetch("/data.json")[1])
        out = self.fetch("/latin1.html")[1]
        self.assertIn("Title: Café", out)
        self.assertIn("Crème brûlée", out)
        self.assertIn("Sniffed as HTML.", self.fetch("/untyped")[1])

    def test_large_download_is_capped(self):
        code, out, _ = self.fetch("/big.txt", "--max-chars", "500")
        self.assertEqual(code, 0)
        self.assertIn("only the first part was read", out)
        self.assertIn(f"of {fetch.MAX_DOWNLOAD_BYTES:,}", out)

    def test_private_address_blocked_without_flag(self):
        code, _, err = run_cli(fetch.main, [self.base_url + "/article.html"])
        self.assertEqual(code, 2)
        self.assertIn("private or local address", err)

    def test_usage_errors(self):
        self.assertEqual(self.fetch("/article.html", "--offset", "-1")[0], 2)
        self.assertEqual(self.fetch("/article.html", "--offset", "999999")[0], 2)
        self.assertEqual(run_cli(fetch.main, ["ftp://example.com"])[0], 2)


if __name__ == "__main__":
    unittest.main()
