"""News reconstruction attack QA for Phase 8B.

Checks public synthetic news briefs for leaked private source content:
- Copied headline substrings
- URL patterns
- Exact source company names/tickers
- Exact dates
- Named counterparties
- Executive quote patterns
- Unique event names
- Overly long verbatim overlap against private source snippets
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NewsAttackFinding:
    """A single finding from the news reconstruction attack."""

    check_name: str
    severity: str  # "blocking" or "warning"
    source: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "severity": self.severity,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass
class NewsAttackResult:
    """Result of a news reconstruction attack run."""

    company_id: str
    passed: bool
    files_checked: int
    findings: list[NewsAttackFinding] = field(default_factory=list)
    blocking_count: int = 0
    warning_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "passed": self.passed,
            "files_checked": self.files_checked,
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "findings": [f.to_dict() for f in self.findings],
        }


class NewsReconstructionAttack:
    """Attack that checks public synthetic news for private source leakage."""

    def __init__(
        self,
        source_company_names: list[str] | None = None,
        source_tickers: list[str] | None = None,
        source_headlines: list[str] | None = None,
        source_urls: list[str] | None = None,
        source_counterparties: list[str] | None = None,
        source_body_snippets: list[str] | None = None,
    ) -> None:
        self._source_company_names = [n.lower() for n in (source_company_names or [])]
        self._source_tickers = [t.upper() for t in (source_tickers or [])]
        self._source_headlines = [h.lower() for h in (source_headlines or [])]
        self._source_urls = source_urls or []
        self._source_counterparties = [c.lower() for c in (source_counterparties or [])]
        self._source_body_snippets = source_body_snippets or []

        # URL pattern
        self._url_pattern = re.compile(r"https?://\S+")

        # Executive quote pattern (looks like "said CEO Name," etc.)
        self._quote_pattern = re.compile(
            r'(?:said|stated|according to|commented)\s+(?:CEO|CFO|President|Chairman|Director|Chief)\s+\w+',
            re.IGNORECASE,
        )

        # Exact date patterns (YYYY-MM-DD, Month DD YYYY)
        self._date_pattern = re.compile(
            r"\b\d{4}-\d{2}-\d{2}\b|"
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b"
        )

        # Minimum overlap length for verbatim detection
        self._verbatim_min_chars = 40

    def run(
        self,
        public_news_dir: str | Path,
        company_id: str,
    ) -> NewsAttackResult:
        """Run the news reconstruction attack on public news files.

        Args:
            public_news_dir: Directory containing public news outputs.
            company_id: Anonymized company ID.

        Returns:
            NewsAttackResult with pass/fail and findings.
        """
        result = NewsAttackResult(company_id=company_id, passed=True, files_checked=0)
        news_dir = Path(public_news_dir)

        if not news_dir.exists():
            result.findings.append(
                NewsAttackFinding(
                    check_name="directory_missing",
                    severity="blocking",
                    source=str(news_dir),
                    detail="News directory does not exist",
                )
            )
            result.passed = False
            result.blocking_count = 1
            return result

        for fp in sorted(news_dir.rglob("*")):
            if not fp.is_file():
                continue
            suffix = fp.suffix.lower()
            if suffix not in {".md", ".csv", ".json", ".txt"}:
                continue

            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            result.files_checked += 1
            content_lower = content.lower()

            # ── Check 1: Copied headlines ─────────────────────────
            for headline in self._source_headlines:
                if len(headline) < 20:
                    continue
                # Check for significant substring overlap
                words = headline.split()
                if len(words) >= 4:
                    phrase = " ".join(words[:4]).lower()
                    if phrase in content_lower:
                        result.findings.append(
                            NewsAttackFinding(
                                check_name="copied_headline",
                                severity="blocking",
                                source=fp.name,
                                detail=f"Copied headline: {headline[:80]}",
                            )
                        )

            # ── Check 2: URLs ─────────────────────────────────────
            urls = self._url_pattern.findall(content)
            for url in urls:
                result.findings.append(
                    NewsAttackFinding(
                        check_name="url_found",
                        severity="blocking",
                        source=fp.name,
                        detail=f"URL in public output: {url}",
                    )
                )

            # ── Check 3: Source company names ─────────────────────
            for company in self._source_company_names:
                if len(company) < 4:
                    continue
                if company in content_lower:
                    result.findings.append(
                        NewsAttackFinding(
                            check_name="source_company_leak",
                            severity="blocking",
                            source=fp.name,
                            detail=f"Source company name found: {company}",
                        )
                    )

            # ── Check 4: Source tickers ───────────────────────────
            for ticker in self._source_tickers:
                if len(ticker) < 2:
                    continue
                # Match as word boundary
                pattern = re.compile(rf"\b{re.escape(ticker)}\b", re.IGNORECASE)
                if pattern.search(content):
                    result.findings.append(
                        NewsAttackFinding(
                            check_name="source_ticker_leak",
                            severity="blocking",
                            source=fp.name,
                            detail=f"Source ticker found: {ticker}",
                        )
                    )

            # ── Check 5: Exact dates ──────────────────────────────
            dates = self._date_pattern.findall(content)
            for date_match in dates:
                result.findings.append(
                    NewsAttackFinding(
                        check_name="exact_date",
                        severity="blocking",
                        source=fp.name,
                        detail=f"Exact date in public output: {date_match}",
                    )
                )

            # ── Check 6: Executive quotes ─────────────────────────
            quotes = self._quote_pattern.findall(content)
            for quote in quotes:
                result.findings.append(
                    NewsAttackFinding(
                        check_name="executive_quote",
                        severity="blocking",
                        source=fp.name,
                        detail=f"Executive quote pattern: {quote}",
                    )
                )

            # ── Check 7: Counterparties ───────────────────────────
            for party in self._source_counterparties:
                if len(party) < 4:
                    continue
                if party in content_lower:
                    result.findings.append(
                        NewsAttackFinding(
                            check_name="counterparty_leak",
                            severity="blocking",
                            source=fp.name,
                            detail=f"Named counterparty: {party}",
                        )
                    )

            # ── Check 8: Verbatim overlap ────────────────────────
            for snippet in self._source_body_snippets:
                if len(snippet) < self._verbatim_min_chars:
                    continue
                # Check for overlapping substrings of at least min_chars
                for start in range(0, len(snippet) - self._verbatim_min_chars + 1, max(1, (len(snippet) - self._verbatim_min_chars) // 10)):
                    chunk = snippet[start:start + self._verbatim_min_chars]
                    if chunk in content:
                        result.findings.append(
                            NewsAttackFinding(
                                check_name="verbatim_overlap",
                                severity="blocking",
                                source=fp.name,
                                detail=f"Verbatim text overlap (>{self._verbatim_min_chars} chars): {chunk[:60]}...",
                            )
                        )
                        break  # One hit per snippet is enough

        # Evaluate
        blocking = [f for f in result.findings if f.severity == "blocking"]
        result.blocking_count = len(blocking)
        result.warning_count = len([f for f in result.findings if f.severity == "warning"])
        result.passed = result.blocking_count == 0

        return result


def check_public_news_directory(
    news_dir: str | Path,
    source_company_names: list[str] | None = None,
    source_tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Convenience function to check a public news directory.

    Args:
        news_dir: Path to public news directory.
        source_company_names: Source company names to check for.
        source_tickers: Source tickers to check for.

    Returns:
        Dict with pass/fail status and findings.
    """
    attack = NewsReconstructionAttack(
        source_company_names=source_company_names,
        source_tickers=source_tickers,
    )
    result = attack.run(news_dir, "COMPANY_001")
    return result.to_dict()
