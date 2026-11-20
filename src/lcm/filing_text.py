"""Turn an EDGAR HTML filing into plain text, page by page, so quotes can be cited.

Filings mark page breaks with CSS `page-break-before/after: always`. We split on
those, extract the text of each page with the standard-library HTML parser, and
read the printed page number from the page footer (e.g. "45" or "F-12").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

PAGE_BREAK = re.compile(r"<[^>]+page-break-(?:after|before)\s*:\s*always[^>]*>", re.I)
BLOCK_TAGS = {"div", "p", "tr", "br", "li", "h1", "h2", "h3", "h4", "table"}
SKIP_TAGS = {"script", "style", "ix:header"}
PAGE_NUMBER = re.compile(r"^(?:[A-Z]-)?\d{1,3}$")


@dataclass
class Page:
    index: int            # 1-based position in the HTML document
    printed: str | None   # page number printed in the footer, if found
    text: str


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "td":
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip(" |") for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


def _printed_number(text: str) -> str | None:
    tail = [ln.strip() for ln in text.splitlines()[-3:]]
    for ln in reversed(tail):
        if PAGE_NUMBER.match(ln):
            return ln
    return None


def split_pages(html: str) -> list[Page]:
    chunks = PAGE_BREAK.split(html)
    pages = []
    for i, chunk in enumerate(chunks, start=1):
        text = html_to_text(chunk)
        if text:
            pages.append(Page(i, _printed_number(text), text))
    return pages


def find(pages: list[Page], pattern: str, context: int = 0) -> list[tuple[Page, str]]:
    """Lines matching a regex (case-insensitive), with `context` lines either side."""
    rx = re.compile(pattern, re.I)
    hits = []
    for pg in pages:
        lines = pg.text.splitlines()
        for j, ln in enumerate(lines):
            if rx.search(ln):
                hits.append((pg, "\n".join(lines[max(0, j - context): j + context + 1])))
    return hits
