"""SEC filing segmenter — splits markdown into Item sections.

Adapted from Zion Terminal pipeline/segmenter.py (commit e75ae57).

Identifies standard 10-K/10-Q Item section boundaries in normalized
markdown text.  Preserves preamble and trailing content.  Filters
table-of-contents false positives.  Deterministic output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Section:
    """A single section of an SEC filing."""

    item: str
    title: str = ""
    content: str = ""
    start_line: int = 0
    end_line: int = 0

    @property
    def char_count(self) -> int:
        return len(self.content)


_ITEM_HEADING_PATTERN = re.compile(
    r"^#{1,4}\s*(?:ITEM|Item)\s+(\d{1,2}[A-Ba-b]?)\b[\.\:\-—–]?\s*(.*?)$",
    re.MULTILINE,
)

_ITEM_PLAIN_PATTERN = re.compile(
    r"^(?:ITEM|Item)\s+(\d{1,2}[A-Ba-b]?)\b[\.\:\-—–]?\s*(.*?)$",
    re.MULTILINE,
)

_KNOWN_TITLES: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "Reserved",
    "7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements With Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
}


class FilingSegmenter:
    """Split filing markdown into Item sections."""

    def segment(self, markdown: str) -> list[Section]:
        """Split markdown into sections based on Item headers.

        Tries heading-prefixed patterns first (``## Item 1``).  Falls
        back to plain-text patterns (``Item 1``) when no headings found.
        Retains unmatched preamble and trailing content explicitly.
        """
        lines = markdown.split("\n")
        matches: list[tuple[int, str, str]] = []

        for i, line in enumerate(lines):
            m = _ITEM_HEADING_PATTERN.match(line)
            if m:
                item_num = m.group(1).upper()
                title = m.group(2).strip()
                if not title:
                    title = _KNOWN_TITLES.get(item_num, "")
                matches.append((i, item_num, title))

        if not matches:
            for i, line in enumerate(lines):
                m = _ITEM_PLAIN_PATTERN.match(line)
                if m:
                    item_num = m.group(1).upper()
                    title = m.group(2).strip()
                    if not title:
                        title = _KNOWN_TITLES.get(item_num, "")
                    matches.append((i, item_num, title))

        if len(matches) > 3:
            matches = self._filter_toc_entries(matches, len(lines))

        if not matches:
            return [
                Section(
                    item="full",
                    title="Full Document",
                    content=markdown,
                    start_line=0,
                    end_line=len(lines),
                )
            ]

        sections: list[Section] = []

        preamble_content = "\n".join(lines[: matches[0][0]]).strip()
        if preamble_content:
            sections.append(
                Section(
                    item="preamble",
                    title="Preamble",
                    content=preamble_content,
                    start_line=0,
                    end_line=matches[0][0],
                )
            )

        for idx, (line_num, item_num, title) in enumerate(matches):
            end_line = matches[idx + 1][0] if idx + 1 < len(matches) else len(lines)
            content = "\n".join(lines[line_num:end_line]).strip()
            sections.append(
                Section(
                    item=f"Item {item_num}",
                    title=title,
                    content=content,
                    start_line=line_num,
                    end_line=end_line,
                )
            )

        return sections

    @staticmethod
    def _filter_toc_entries(
        matches: list[tuple[int, str, str]],
        total_lines: int,
    ) -> list[tuple[int, str, str]]:
        if len(matches) < 4:
            return matches

        if total_lines < 200:
            return matches

        cutoff_line = max(total_lines // 5, 50)
        early_matches = [m for m in matches if m[0] < cutoff_line]
        late_matches = [m for m in matches if m[0] >= cutoff_line]

        if len(early_matches) >= 3 and late_matches:
            early_span = early_matches[-1][0] - early_matches[0][0]
            if early_span < 30:
                gaps = [
                    early_matches[j + 1][0] - early_matches[j][0]
                    for j in range(len(early_matches) - 1)
                ]
                avg_gap = sum(gaps) / len(gaps) if gaps else 0
                if avg_gap < 5:
                    return late_matches

        return matches

    @staticmethod
    def get_section(sections: list[Section], item: str) -> Section | None:
        """Find a section by item number (e.g. '1A', 'Item 7')."""
        normalized = item.upper().replace("ITEM ", "").strip()
        for s in sections:
            s_num = s.item.upper().replace("ITEM ", "").strip()
            if s_num == normalized:
                return s
        return None

    @staticmethod
    def summary(sections: list[Section]) -> dict[str, Any]:
        """Return a summary dict of all sections."""
        return {
            "section_count": len(sections),
            "sections": [
                {"item": s.item, "title": s.title, "char_count": s.char_count} for s in sections
            ],
            "total_chars": sum(s.char_count for s in sections),
        }
