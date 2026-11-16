from __future__ import annotations

import json
import unittest

from helpers import ServerTestCase, assert_golden, run_cli, stable
import search


class NormalizeTest(unittest.TestCase):
    def setUp(self):
        from helpers import fixture_bytes

        self.payload = json.loads(fixture_bytes("searxng_synthetic.json"))

    def normalize(self, max_results=5):
        return search.normalize_response(self.payload, "synthetic", max_results, {"page": 1})

    def test_dedupes_skips_and_ranks(self):
        results = self.normalize()["results"]
        self.assertEqual([r["rank"] for r in results], [1, 2, 3, 4, 5])
        urls = [r["url"] for r in results]
        self.assertEqual(urls[0], "https://www.example.com/releases/?utm_source=feed&id=7#top")
        self.assertNotIn("http://example.com/releases?id=7", urls)
        self.assertFalse(any(u.startswith("magnet:") for u in urls))
        self.assertNotIn("https://blog.example.com/six", urls)

    def test_cleans_fields(self):
        first, second, third = self.normalize()["results"][:3]
        self.assertEqual(first["title"], "Release notes & changes")
        self.assertEqual(first["snippet"], "The first result has messy whitespace.")
        self.assertEqual(first["published"], "2026-11-12")
        self.assertEqual(first["engines"], ["google", "bing"])
        self.assertEqual(second["title"], "(no title)")
        self.assertTrue(second["snippet"].endswith("…"))
        self.assertLessEqual(len(second["snippet"]), search.SNIPPET_CHARS + 1)
        self.assertEqual(third["published"], "2026-11-10")

    def test_max_results(self):
        self.assertEqual(len(self.normalize(max_results=1)["results"]), 1)
        self.assertEqual(len(self.normalize(max_results=10)["results"]), 6)

    def test_answers_infobox_suggestions_engines(self):
        data = self.normalize()
        self.assertEqual(
            data["answers"],
            [{"text": "42 is the answer", "url": "https://answers.example.com/42"},
             {"text": "A legacy plain-string answer", "url": ""}],
        )
        self.assertEqual(data["infobox"]["url"], "https://en.wikipedia.org/wiki/Example")
        self.assertEqual(data["suggestions"], ["synthetic data"])
        self.assertEqual(
            data["failed_engines"],
            [{"engine": "brave", "reason": "Suspended: too many requests"}, {"engine": "wikipedia", "reason": "timeout"}],
        )

    def test_dates_and_outlets(self):
        cases = [
            ({"publishedDate": "2026-11-12T08:30:00"}, ("2026-11-12", "")),
            ({"pubdate": "3 days ago"}, ("3 days ago", "")),
            ({"metadata": "2 days ago | CNBC"}, ("2 days ago", "CNBC")),
            ({"metadata": "CNBC | 21 hours ago"}, ("21 hours ago", "CNBC")),
            ({"pubdate": "Yesterday", "metadata": "5 hours ago | BBC"}, ("Yesterday", "BBC")),
            ({"metadata": "Nov 14, 2026 | Reuters"}, ("Nov 14, 2026", "Reuters")),
            ({"metadata": "14 November 2026"}, ("14 November 2026", "")),
            ({"metadata": "1,234 views | some channel"}, ("", "")),  # not a date: ignore the field
            ({"pubdate": "a very long free-text field that merely mentions something ago"}, ("", "")),
            ({"publishedDate": None, "metadata": None}, ("", "")),
        ]
        for item, expected in cases:
            self.assertEqual(search._published_and_source(item), expected, item)

    def test_engines_used(self):
        self.assertEqual(self.normalize()["engines_used"], ["bing", "duckduckgo", "google"])

    def test_build_search_url_has_fixed_order(self):
        url = search.build_search_url("http://s", "a b", time_range="week", categories="news", language="en", page=2)
        self.assertEqual(
            url, "http://s/search?q=a+b&format=json&safesearch=0&time_range=week&categories=news&language=en&pageno=2"
        )
        self.assertEqual(search.build_search_url("http://s", "x"), "http://s/search?q=x&format=json&safesearch=0")


class SearchCliTest(ServerTestCase):
    def search(self, *argv):
        return run_cli(search.main, [*argv, "--base-url", self.base_url])

    def test_text_output_matches_golden(self):
        code, out, err = self.search("synthetic")
        self.assertEqual((code, err), (0, ""))
        assert_golden(self, "search_synthetic.txt", stable(out))

    def test_recorded_news_response_matches_golden(self):
        code, out, _ = self.search("news", "--news", "--time-range", "week")
        self.assertEqual(code, 0)
        assert_golden(self, "search_news_week.txt", stable(out))

    def test_multi_engine_news_matches_golden(self):
        code, out, _ = self.search("openai", "--news")
        self.assertEqual(code, 0)
        self.assertIn("Published: 2 days ago (CNBC)", out)
        self.assertNotIn("--time-range", out)
        assert_golden(self, "search_news_openai.txt", stable(out))

    def test_narrow_time_range_note(self):
        _, out, _ = self.search("news", "--news", "--time-range", "week")
        self.assertIn("Note: with --time-range, only 1 search engine answered (bing news).", out)
        _, out, _ = self.search("news", "--news")
        self.assertNotIn("Note: with --time-range", out)  # no time range, no note
        _, out, _ = self.search("openai", "--news", "--time-range", "week")
        self.assertNotIn("Note: with --time-range", out)  # several engines answered
        _, out, _ = self.search("empty", "--time-range", "day")
        self.assertIn("Note: with --time-range, no search engine returned results.", out)

    def test_output_is_deterministic(self):
        runs = {self.search("synthetic", "--max-results", "3")[1] for _ in range(5)}
        self.assertEqual(len(runs), 1)

    def test_json_output(self):
        code, out, _ = self.search("synthetic", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["results"]), 5)

    def test_flags_reach_searxng(self):
        self.search("multi", "word", "query", "--news", "--categories", "it,news", "--time-range", "day", "--page", "3")
        sent = self.server.last_search_query
        self.assertEqual(sent["q"], ["multi word query"])
        self.assertEqual(sent["categories"], ["it,news"])
        self.assertEqual(sent["time_range"], ["day"])
        self.assertEqual(sent["pageno"], ["3"])
        self.assertEqual(sent["format"], ["json"])

    def test_max_results_is_clamped(self):
        self.assertEqual(len(json.loads(self.search("synthetic", "--max-results", "0", "--json")[1])["results"]), 1)
        self.assertEqual(len(json.loads(self.search("synthetic", "--max-results", "99", "--json")[1])["results"]), 6)

    def test_no_results_shows_suggestions_and_failures(self):
        code, out, _ = self.search("empty")
        self.assertEqual(code, 0)
        self.assertIn('No results found for "empty".', out)
        self.assertIn('Try instead: "empt"; "emptier"; "empty set"', out)
        self.assertIn("google (CAPTCHA)", out)
        self.assertNotIn("fetch.py", out)

    def test_http_errors_have_hints(self):
        for status, hint in ((401, "Bearer"), (403, "search.formats"), (429, "rate-limiting"), (500, "logs")):
            code, out, err = self.search(f"status{status}")
            self.assertEqual(code, 1, status)
            self.assertIn(f"HTTP {status}", err)
            self.assertIn(hint, err)
            self.assertEqual(out, "")

    def test_json_error_goes_to_stdout(self):
        code, out, _ = self.search("status403", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["ok"], False)

    def test_non_json_response(self):
        code, _, err = self.search("notjson")
        self.assertEqual(code, 1)
        self.assertIn("did not return JSON", err)

    def test_timeout(self):
        code, _, err = self.search("slow", "--timeout", "0.3")
        self.assertEqual(code, 1)
        self.assertIn("did not answer within 0.3s", err)

    def test_unreachable(self):
        code, _, err = run_cli(search.main, ["x", "--base-url", "http://127.0.0.1:9"])
        self.assertEqual(code, 1)
        self.assertIn("cannot reach SearXNG", err)

    def test_usage_errors(self):
        self.assertEqual(self.search("   ")[0], 2)
        self.assertEqual(self.search("x", "--page", "0")[0], 2)
        self.assertEqual(self.search("x", "--time-range", "decade")[0], 2)


if __name__ == "__main__":
    unittest.main()
