#!/usr/bin/env python3
"""Static checks on the page templates in docs/.

The pages build their markup with JS template literals. A `${...}` written
inside a quoted string that is itself inside a `${...}` expression is not
interpolated - it renders to the visitor as the literal text `${nRejected}`.
Nothing catches that: the page still loads, the JSON still parses, and the
placeholder just sits in the copy. So scan for it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
PAGES = sorted(DOCS.glob("*.html"))


def dead_placeholders(src: str) -> list[str]:
    """Every `${...}` written inside a '' or "" string inside a `${...}`.

    Walks a context stack: a backtick opens a template (where `${` interpolates
    and a nested backtick opens another template), and a quote opened while the
    innermost context is an expression opens an ordinary string, where `${` is
    just characters. That distinction is the whole point - `${x}` inside the
    HTML attributes of a template is fine, the same text inside a JS string is
    dead. Good enough for these files: no regex literals or comments contain
    stray quotes inside an expression.
    """
    found: list[str] = []
    stack: list[str] = []       # "tmpl" or "expr"
    depth: list[int] = []       # brace depth per open expr
    quote = ""
    qstart = 0
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                chunk = src[qstart:i]
                if "${" in chunk:
                    found.append(chunk.strip()[:120])
                quote = ""
            elif c == "\n":     # an apostrophe in prose, not a string
                quote = ""
            i += 1
            continue
        inner = stack[-1] if stack else None
        if c == "`":
            if inner == "tmpl":
                stack.pop()
            else:
                stack.append("tmpl")
        elif inner == "tmpl" and src.startswith("${", i):
            stack.append("expr")
            depth.append(0)
            i += 2
            continue
        elif inner == "expr":
            if c in "\"'":
                quote, qstart = c, i + 1
            elif c == "{":
                depth[-1] += 1
            elif c == "}":
                depth[-1] -= 1
                if depth[-1] < 0:
                    stack.pop()
                    depth.pop()
        i += 1
    return found


class TestPageTemplates(unittest.TestCase):
    def test_no_dead_placeholders(self):
        for page in PAGES:
            with self.subTest(page.name):
                dead = dead_placeholders(page.read_text())
                self.assertEqual(dead, [], f"{page.name} renders these literally: {dead}")

    def test_every_page_links_the_whole_nav(self):
        """A new asset that only half-appears in the nav is easy to ship."""
        expected = {"./", "gpiq.html", "tsla.html", "spcx.html", "nvda.html",
                    "goog.html", "retire.html"}
        for page in PAGES:
            with self.subTest(page.name):
                nav = re.search(r'<nav class="fundtoggle".*?</nav>', page.read_text(), re.S)
                self.assertIsNotNone(nav, f"{page.name} has no asset nav")
                hrefs = set(re.findall(r'href="([^"]+)"', nav.group(0)))
                self.assertEqual(hrefs, expected)

    def test_each_page_marks_exactly_one_nav_entry_active(self):
        for page in PAGES:
            with self.subTest(page.name):
                nav = re.search(r'<nav class="fundtoggle".*?</nav>', page.read_text(), re.S)
                self.assertEqual(nav.group(0).count('class="active"'), 1)

    def test_each_stock_page_fetches_its_own_data_file(self):
        for page in PAGES:
            fetched = set(re.findall(r'fetch\("(data[^"]*\.json)"\)', page.read_text()))
            if page.name in ("retire.html",):
                continue  # reads both fund files by design
            with self.subTest(page.name):
                expected = ("data.json" if page.name == "index.html"
                            else f"data-{page.stem}.json")
                self.assertEqual(fetched, {expected})


if __name__ == "__main__":
    unittest.main(verbosity=2)
