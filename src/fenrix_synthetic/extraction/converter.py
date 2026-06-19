"""SEC filing HTML to markdown converter.

Adapted from Zion Terminal pipeline/converter.py (commit e75ae57).

Uses BeautifulSoup for DOM-based SEC HTML conversion.  Preserves
headings, paragraphs, lists, and tables.  Promotes SEC Item headers
found in plain elements to markdown headings.  Removes scripts,
styles, and hidden transport content.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_ITEM_HEADER_RE = re.compile(r"^\s*(?:ITEM|Item)\s+\d{1,2}[A-Ba-b]?\b[\.\:\-\—\–]?\s*.{0,120}$")


class HtmlFilingExtractor:
    """Convert SEC filing HTML to normalized markdown text.

    The extraction is deterministic.  Company-identifying content is
    intentionally preserved — masking is not part of Phase 1.
    """

    def __init__(self, max_length: int = 200_000) -> None:
        self._max_length = max_length
        self._used_dom = False

    def extract(self, html: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert SEC HTML to normalized markdown.

        Returns a dict with ``text``, ``char_count``, and ``metadata``.
        """
        self._used_dom = False
        try:
            from bs4 import BeautifulSoup, NavigableString, Tag

            text = self._dom_extract(html, BeautifulSoup, NavigableString, Tag)
            self._used_dom = True
        except ImportError:
            logger.warning("bs4/lxml not available — falling back to regex stripping")
            text = self._fallback_extract(html)

        if len(text) > self._max_length:
            text = text[: self._max_length] + "\n\n[... truncated ...]"

        return {
            "text": text,
            "char_count": len(text),
            "metadata": {
                **(metadata or {}),
                "converter": "dom" if self._used_dom else "regex",
                "truncated": len(text) >= self._max_length,
            },
        }

    @property
    def used_dom(self) -> bool:
        """Whether the DOM-based converter was used."""
        return self._used_dom

    def _dom_extract(
        self,
        html: str,
        BeautifulSoup: Any,
        NavigableString: Any,
        Tag: Any,
    ) -> str:
        soup = BeautifulSoup(html, "lxml")

        for tag in soup.find_all(["script", "style", "meta", "link", "noscript"]):
            tag.decompose()

        parts: list[str] = []
        self._walk(soup, parts, NavigableString, Tag)
        text = "".join(parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _walk(self, element: Any, parts: list[str], NavigableString: Any, Tag: Any) -> None:
        if isinstance(element, NavigableString):
            text = str(element).strip()
            if text:
                parts.append(text + " ")
            return

        if not isinstance(element, Tag):
            return

        name = element.name.lower() if element.name else ""

        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(name[1])
            text = element.get_text(strip=True)
            if text:
                parts.append(f"\n\n{'#' * level} {text}\n\n")
            return

        if name == "p":
            text = element.get_text(strip=True)
            if text and _ITEM_HEADER_RE.match(text):
                parts.append(f"\n\n## {text}\n\n")
                return
            parts.append("\n\n")
            for child in element.children:
                self._walk(child, parts, NavigableString, Tag)
            parts.append("\n")
            return

        if name == "br":
            parts.append("\n")
            return

        if name in ("b", "strong"):
            text = element.get_text(strip=True)
            if not text:
                return
            if _ITEM_HEADER_RE.match(text):
                parts.append(f"\n\n## {text}\n\n")
                return
            parts.append(f"**{text}**")
            return

        if name in ("i", "em"):
            text = element.get_text(strip=True)
            if text:
                parts.append(f"*{text}*")
            return

        if name == "li":
            text = element.get_text(strip=True)
            if text:
                parts.append(f"\n- {text}")
            return

        if name == "table":
            self._convert_table(element, parts, Tag)
            return

        if name == "div":
            text = element.get_text(strip=True)
            if text and _ITEM_HEADER_RE.match(text) and len(text) < 150:
                parts.append(f"\n\n## {text}\n\n")
                return

        for child in element.children:
            self._walk(child, parts, NavigableString, Tag)

    @staticmethod
    def _convert_table(table: Any, parts: list[str], Tag: Any) -> None:
        rows = table.find_all("tr")
        if not rows:
            return

        md_rows: list[list[str]] = []
        for row in rows:
            cells = row.find_all(["td", "th"])
            md_row = [c.get_text(strip=True).replace("|", "/") for c in cells]
            if any(md_row):
                md_rows.append(md_row)

        if not md_rows:
            return

        max_cols = max(len(r) for r in md_rows)
        for r in md_rows:
            while len(r) < max_cols:
                r.append("")

        parts.append("\n\n")
        parts.append("| " + " | ".join(md_rows[0]) + " |\n")
        parts.append("| " + " | ".join(["---"] * max_cols) + " |\n")
        for row in md_rows[1:]:
            parts.append("| " + " | ".join(row) + " |\n")

    @staticmethod
    def _fallback_extract(html: str) -> str:
        text = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
