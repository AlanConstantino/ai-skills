"""Smoke test against the real SearXNG instance and the public web.

Skipped unless WEB_SEARX_LIVE=1, so the normal test run never needs the network:

    WEB_SEARX_LIVE=1 python3 -m unittest test_live
"""

from __future__ import annotations

import json
import os
import unittest

from helpers import run_cli
import fetch
import search


@unittest.skipUnless(os.environ.get("WEB_SEARX_LIVE") == "1", "set WEB_SEARX_LIVE=1 to run live tests")
class LiveTest(unittest.TestCase):
    def test_search_returns_results(self):
        code, out, err = run_cli(search.main, ["python programming language", "--json"])
        self.assertEqual(code, 0, err)
        self.assertGreater(len(json.loads(out)["results"]), 0)

    def test_news_search(self):
        code, out, err = run_cli(search.main, ["technology", "--news", "--time-range", "week"])
        self.assertEqual(code, 0, err)
        self.assertIn("1. ", out)

    def test_fetch_public_page(self):
        code, out, err = run_cli(fetch.main, ["https://www.python.org/", "--max-chars", "2000", "--refresh"])
        self.assertEqual(code, 0, err)
        self.assertIn("Python", out)


if __name__ == "__main__":
    unittest.main()
