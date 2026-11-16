"""Turn an HTML page into clean, markdown-ish text with the boilerplate removed.

Deterministic: the same HTML always yields the same text. Standard library only.

Steps:
1. Parse into a light tree, repairing common malformed HTML (unclosed <p>, <li>, <td>).
2. Pick the content root: the largest <main>, else a dominant <article>, else <body>.
3. Render that root, dropping navigation, sidebars, forms, hidden elements and cookie
   banners, and keeping headings (#), list items (-), tables (|) and code blocks (```).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Elements whose content is never readable text.
NON_CONTENT_TAGS = {
    "script", "style", "noscript", "template", "svg", "math", "canvas", "iframe",
    "object", "embed", "select", "button", "input", "textarea", "head", "video", "audio",
}
# Page furniture. <header> is only dropped outside <main>/<article>, where it usually
# holds the site banner rather than the article title.
BOILERPLATE_TAGS = {"nav", "aside", "footer", "form", "dialog", "menu"}
BOILERPLATE_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "dialog", "alert"}
BOILERPLATE_ID_CLASS = re.compile(r"(^|[\s_-])(cookie|consent|gdpr|skip-link|visually-hidden|sr-only)([\s_-]|$)", re.I)

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "caption", "dd", "details", "dialog",
    "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "html", "li", "main", "nav", "ol", "p", "pre", "section",
    "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
# An open element of the key tag is closed when one of the value tags starts (HTML's optional end tags).
IMPLIED_END = {
    "p": BLOCK_TAGS,
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "tr": {"tr", "tbody", "thead", "tfoot"},
    "td": {"td", "th", "tr", "tbody", "thead", "tfoot"},
    "th": {"td", "th", "tr", "tbody", "thead", "tfoot"},
    "option": {"option", "optgroup"},
}
HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

MAX_DEPTH = 256  # deeper nesting is flattened so rendering cannot hit the recursion limit
MIN_MAIN_CHARS = 200  # a <main> smaller than this is probably an app shell; use <body>
MIN_ARTICLE_SHARE = 0.4  # an <article> must hold this share of the page text to be chosen

_SPACES = re.compile(r"\s+")  # includes non-breaking and other Unicode spaces
_BLANK_LINES = re.compile(r"\n{3,}")


class Node:
    __slots__ = ("tag", "attrs", "children", "_text_len")

    def __init__(self, tag: str, attrs: dict):
        self.tag = tag
        self.attrs = attrs
        self.children = []  # Node or str
        self._text_len = None


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self.stack = [self.root]
        self.title_parts = []
        self._in_title = False
        self._title_done = False

    def handle_starttag(self, tag, attrs):
        # Only the first <title> is the page title; later ones label SVG icons.
        if tag == "title" and not self._title_done:
            self._in_title = True
        while len(self.stack) > 1 and tag in IMPLIED_END.get(self.stack[-1].tag, ()):
            self.stack.pop()
        node = Node(tag, {k: (v or "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS and len(self.stack) < MAX_DEPTH:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs})
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return
        # A stray end tag with no matching open element is ignored.

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        self.stack[-1].children.append(data)


# ---------------------------------------------------------------------------
# Content-root selection
# ---------------------------------------------------------------------------


def _is_hidden(node: Node) -> bool:
    attrs = node.attrs
    if "hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true":
        return True
    style = attrs.get("style", "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _is_boilerplate(node: Node, in_content: bool) -> bool:
    if node.tag in BOILERPLATE_TAGS:
        return True
    if node.tag == "header" and not in_content:
        return True
    if node.attrs.get("role", "").lower() in BOILERPLATE_ROLES:
        return True
    marker = f"{node.attrs.get('id', '')} {node.attrs.get('class', '')}"
    return bool(BOILERPLATE_ID_CLASS.search(marker))


def _skipped(node: Node, in_content: bool) -> bool:
    return node.tag in NON_CONTENT_TAGS or _is_hidden(node) or _is_boilerplate(node, in_content)


def _text_len(node: Node) -> int:
    if node._text_len is None:
        total = 0
        for child in node.children:
            if isinstance(child, str):
                total += len(child.strip())
            elif not _skipped(child, in_content=True):
                total += _text_len(child)
        node._text_len = total
    return node._text_len


def _find_all(node: Node, predicate, found: list) -> list:
    for child in node.children:
        if isinstance(child, Node) and child.tag not in NON_CONTENT_TAGS:
            if predicate(child):
                found.append(child)
            _find_all(child, predicate, found)
    return found


def _largest(nodes: list):
    best = None
    for node in nodes:  # first one wins a tie, so the choice is stable
        if best is None or _text_len(node) > _text_len(best):
            best = node
    return best


def choose_content_root(document: Node) -> Node:
    bodies = _find_all(document, lambda n: n.tag == "body", [])
    body = bodies[0] if bodies else document
    main = _largest(_find_all(body, lambda n: n.tag == "main" or n.attrs.get("role", "").lower() == "main", []))
    if main is not None and _text_len(main) >= MIN_MAIN_CHARS:
        return main
    article = _largest(_find_all(body, lambda n: n.tag == "article", []))
    if article is not None and _text_len(article) >= MIN_ARTICLE_SHARE * max(_text_len(body), 1):
        return article
    return body


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _raw_text(node: Node) -> str:
    """Text of a subtree with whitespace preserved; <br> becomes a newline."""
    parts = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag == "br":
            parts.append("\n")
        elif child.tag not in NON_CONTENT_TAGS:
            parts.append(_raw_text(child))
    return "".join(parts)


def _inline_text(node: Node) -> str:
    return _SPACES.sub(" ", _raw_text(node)).strip()


class _Renderer:
    def __init__(self):
        self.blocks = []  # (text, kind); kind "item" and "dd" join with one newline, others with a blank line
        self.buffer = []
        self.prefix_first = ""  # prefix for the next flushed block (list marker)
        self.prefix_rest = ""  # indentation for continuation blocks in the same list item
        self.quote_depth = 0
        self.in_dd = False

    def joined(self) -> str:
        parts = []
        previous = ""
        for text, kind in self.blocks:
            if parts:
                tight = kind == "dd" or (kind == "item" and previous == "item")
                parts.append("\n" if tight else "\n\n")
            parts.append(text)
            previous = kind
        return "".join(parts)

    def flush(self, heading_level: int = 0):
        text = "".join(self.buffer)
        self.buffer = []
        lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
        lines = [line for line in lines if line]
        if not lines:
            return
        if heading_level:
            lines = ["#" * heading_level + " " + " ".join(lines)]
        self.emit("\n".join(lines))

    def emit(self, block: str):
        kind = "dd" if self.in_dd else "item" if self.prefix_first or self.prefix_rest else "para"
        lines = block.split("\n")
        lines = [self.prefix_first + lines[0]] + [self.prefix_rest + line if line else line for line in lines[1:]]
        self.prefix_first = self.prefix_rest
        if self.quote_depth:
            quote = "> " * self.quote_depth
            lines = [quote + line for line in lines]
        self.blocks.append(("\n".join(lines), kind))

    def render(self, node: Node, in_content: bool, list_depth: int = 0):
        for child in node.children:
            if isinstance(child, str):
                self.buffer.append(_SPACES.sub(" ", child))
            else:
                self.render_element(child, in_content, list_depth)

    def render_element(self, node: Node, in_content: bool, list_depth: int):
        tag = node.tag
        if _skipped(node, in_content):
            # Keep words on either side of a dropped inline element apart.
            if tag not in BLOCK_TAGS:
                self.buffer.append(" ")
            return
        if tag in ("main", "article"):
            in_content = True

        if tag == "br":
            self.buffer.append("\n")
        elif tag == "hr":
            self.flush()
        elif tag in HEADING_LEVELS:
            self.flush()
            self.render(node, in_content, list_depth)
            self.flush(heading_level=HEADING_LEVELS[tag])
        elif tag == "pre":
            self.flush()
            code = _raw_text(node).strip("\n")
            if code.strip():
                self.emit("```\n" + code.rstrip() + "\n```")
        elif tag == "code":
            text = _inline_text(node)
            if text:
                self.buffer.append(f"`{text}`")
        elif tag in ("ul", "ol"):
            self.render_list(node, in_content, list_depth)
        elif tag == "table":
            self.render_table(node)
        elif tag == "blockquote":
            self.flush()
            self.quote_depth += 1
            self.render(node, in_content, list_depth)
            self.flush()
            self.quote_depth -= 1
        elif tag in ("img", "picture", "source", "wbr"):
            return
        elif tag == "dd":
            # A definition sits directly under its term ("Author:" / "Jane Doe").
            self.flush()
            saved, self.in_dd = self.in_dd, True
            self.render(node, in_content, list_depth)
            self.flush()
            self.in_dd = saved
        elif tag in BLOCK_TAGS:
            self.flush()
            self.render(node, in_content, list_depth)
            self.flush()
        else:
            self.render(node, in_content, list_depth)

    def render_list(self, node: Node, in_content: bool, list_depth: int):
        self.flush()
        saved = (self.prefix_first, self.prefix_rest)
        indent = "  " * list_depth
        number = 0
        for child in node.children:
            if isinstance(child, str):
                continue  # whitespace between items
            if child.tag != "li" or _skipped(child, in_content):
                if not _skipped(child, in_content):
                    self.render_element(child, in_content, list_depth)
                continue
            number += 1
            marker = f"{number}. " if node.tag == "ol" else "- "
            self.prefix_first = indent + marker
            self.prefix_rest = indent + " " * len(marker)
            self.render(child, in_content, list_depth + 1)
            self.flush()
        self.prefix_first, self.prefix_rest = saved

    def render_table(self, node: Node):
        self.flush()
        rows = []
        for row in _find_all(node, lambda n: n.tag == "tr", []):
            cells = [_inline_text(c) for c in row.children if isinstance(c, Node) and c.tag in ("td", "th")]
            if any(cells):
                rows.append("| " + " | ".join(cells) + " |")
        if rows:
            self.emit("\n".join(rows))


def extract(html_text: str) -> tuple:
    """Return (title, text) for an HTML document."""
    builder = _TreeBuilder()
    builder.feed(html_text)
    builder.close()
    root = choose_content_root(builder.root)

    renderer = _Renderer()
    renderer.render(root, in_content=root.tag in ("main", "article"))
    renderer.flush()
    text = _BLANK_LINES.sub("\n\n", renderer.joined()).strip()

    title = _SPACES.sub(" ", "".join(builder.title_parts)).strip()
    if not title:
        headings = _find_all(builder.root, lambda n: n.tag == "h1", [])
        title = _inline_text(headings[0]) if headings else ""
    return title, text
