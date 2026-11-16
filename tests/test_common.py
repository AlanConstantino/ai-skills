from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts the scripts directory on sys.path)
from _common import (
    EXIT_USAGE,
    ToolError,
    canonical_url,
    clean_inline,
    resolve_api_key,
    resolve_base_url,
    truncate_words,
)


class CanonicalUrlTest(unittest.TestCase):
    def test_same_page_variants_share_a_key(self):
        key = canonical_url("https://www.example.com/a/?utm_source=x&b=2&a=1#frag")
        for variant in (
            "http://example.com/a?a=1&b=2",
            "https://EXAMPLE.com/a/?b=2&a=1&fbclid=zzz",
            "https://www.example.com:443/a?a=1&b=2",
        ):
            self.assertEqual(canonical_url(variant), key, variant)

    def test_different_pages_differ(self):
        self.assertNotEqual(canonical_url("https://example.com/a"), canonical_url("https://example.com/b"))
        self.assertNotEqual(canonical_url("https://example.com/a?id=1"), canonical_url("https://example.com/a?id=2"))
        self.assertNotEqual(canonical_url("https://example.com:8080/a"), canonical_url("https://example.com/a"))


class TextTest(unittest.TestCase):
    def test_clean_inline(self):
        self.assertEqual(clean_inline("  a &amp;\n\t b c "), "a & b c")
        self.assertEqual(clean_inline(""), "")

    def test_truncate_words(self):
        self.assertEqual(truncate_words("short", 10), "short")
        self.assertEqual(truncate_words("alpha beta gamma delta", 14), "alpha beta…")
        self.assertEqual(truncate_words("x" * 20, 10), "x" * 10 + "…")
        self.assertEqual(truncate_words("one, two, three", 10), "one, two…")


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = Path(self.tmp.name) / "rpiv-web-tools" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({
            "provider": "searxng",
            "baseUrls": {"searxng": "http://from-config:8456/"},
            "apiKeys": {"searxng": "config-key"},
        }))
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "SEARXNG_URL": "", "SEARXNG_API_KEY": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_precedence_cli_then_env_then_config(self):
        self.assertEqual(resolve_base_url(None), "http://from-config:8456")
        with mock.patch.dict(os.environ, {"SEARXNG_URL": "http://from-env:1"}):
            self.assertEqual(resolve_base_url(None), "http://from-env:1")
            self.assertEqual(resolve_base_url("http://from-cli:2/"), "http://from-cli:2")

    def test_api_key_env_then_config(self):
        self.assertEqual(resolve_api_key(), "config-key")
        with mock.patch.dict(os.environ, {"SEARXNG_API_KEY": "env-key"}):
            self.assertEqual(resolve_api_key(), "env-key")

    def test_missing_url_is_a_usage_error(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.tmp.name) / "nothing")}):
            with self.assertRaises(ToolError) as ctx:
                resolve_base_url(None)
        self.assertEqual(ctx.exception.exit_code, EXIT_USAGE)
        self.assertIn("SEARXNG_URL", ctx.exception.hint)

    def test_bad_scheme_rejected(self):
        with self.assertRaises(ToolError):
            resolve_base_url("ftp://example.com")

    def test_malformed_config_is_ignored(self):
        (Path(self.tmp.name) / "rpiv-web-tools" / "config.json").write_text("{not json")
        with self.assertRaises(ToolError):
            resolve_base_url(None)


if __name__ == "__main__":
    unittest.main()
