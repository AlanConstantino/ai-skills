from __future__ import annotations

import unittest

from helpers import fixture_bytes
from _extract import extract


def body(html: str) -> str:
    return extract(html)[1]


class ArticleFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.title, cls.text = extract(fixture_bytes("article.html").decode("utf-8"))

    def test_title_ignores_svg_titles(self):
        self.assertEqual(self.title, "Widget 2.0 Released – Example Blog")

    def test_boilerplate_removed(self):
        for gone in ("Skip to content", "About", "cookies", "Accept all", "Popular posts", "© 2026",
                     "Related:", "Subscribe", "Tags:", "Hidden paragraph", "Invisible paragraph",
                     "Decorative text", "chart icon", "tracking", "tail script", "font-family"):
            self.assertNotIn(gone, self.text, gone)

    def test_article_header_kept(self):
        self.assertTrue(self.text.startswith("# Widget 2.0 Released\n\nBy Jane Doe · 2026-11-12"))

    def test_structure(self):
        self.assertIn("It is twice as fast and adds a new `widget.run()` API.", self.text)
        self.assertIn("1. Faster startup\n2. New API\n  - run()\n  - stop()\n3. Fewer dependencies", self.text)
        self.assertIn("| Version | Startup |\n| 1.0 | 120 ms |\n| 2.0 | 60 ms |", self.text)
        self.assertIn("```\npip install --upgrade widget\nwidget --version   # prints 2.0\n```", self.text)
        self.assertIn("> The best release yet.", self.text)
        self.assertIn("License\nMIT\n\nPython\n3.9+", self.text)
        self.assertIn("Line one\nLine two", self.text)

    def test_deterministic(self):
        html = fixture_bytes("article.html").decode("utf-8")
        self.assertEqual({extract(html) for _ in range(5)}, {(self.title, self.text)})


class RootSelectionTest(unittest.TestCase):
    def test_prefers_main(self):
        html = "<body><div>outside text " * 3 + "</div><main><p>" + "inside " * 50 + "</p></main></body>"
        self.assertNotIn("outside", body(html))

    def test_tiny_main_falls_back_to_body(self):
        html = "<body><main>Loading…</main><div><p>" + "real content " * 50 + "</p></div></body>"
        self.assertIn("real content", body(html))

    def test_dominant_article_chosen(self):
        html = ("<body><div class=promo>" + "promo " * 10 + "</div><article><p>" + "story " * 100
                + "</p></article></body>")
        self.assertNotIn("promo", body(html))

    def test_many_small_articles_keep_body(self):
        html = "<body>" + "".join(f"<article><p>Post {i} " + "w " * 20 + "</p></article>" for i in range(5)) + "</body>"
        text = body(html)
        self.assertIn("Post 0", text)
        self.assertIn("Post 4", text)

    def test_page_header_dropped_outside_content(self):
        html = "<body><header>Site banner</header><div><h1>Title</h1><p>Text</p></div></body>"
        self.assertEqual(body(html), "# Title\n\nText")


class RobustnessTest(unittest.TestCase):
    def test_malformed_html(self):
        html = "<p>one<p>two<ul><li>a<li>b</ul><div>three</span></div></b>"
        self.assertEqual(body(html), "one\n\ntwo\n\n- a\n- b\n\nthree")

    def test_deep_nesting_does_not_crash(self):
        html = "<div>" * 5000 + "deep text" + "</div>" * 5000
        self.assertEqual(body(html), "deep text")

    def test_entities_and_spaces(self):
        self.assertEqual(body("<p>a&nbsp;&amp;&#160;b &lt;c&gt; &#x41;</p>"), "a & b <c> A")

    def test_inline_elements_do_not_glue_words(self):
        self.assertEqual(body("<p>Hello<button>x</button>world</p>"), "Hello world")
        self.assertEqual(body("<p><span>Hel</span><span>lo</span></p>"), "Hello")

    def test_empty_and_script_only(self):
        self.assertEqual(body(""), "")
        self.assertEqual(body("<html><body><script>x()</script></body></html>"), "")

    def test_title_falls_back_to_h1(self):
        self.assertEqual(extract("<body><h1>Heading <em>One</em></h1></body>")[0], "Heading One")

    def test_nested_blockquote_and_ordered_list(self):
        html = "<blockquote><p>a</p><blockquote><p>b</p></blockquote></blockquote><ol><li>x<li>y</ol>"
        self.assertEqual(body(html), "> a\n\n> > b\n\n1. x\n2. y")


if __name__ == "__main__":
    unittest.main()
