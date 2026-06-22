"""SEC parser provider layer.

Provides a provider abstraction for SEC filing discovery, download, and
semantic parsing. The fixture provider reads from committed test fixtures;
production providers (edgartools, sec-parser) are guarded imports.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .evidence import SourceFiling, SourceSection, SourceTable, build_provenance_key


class SecProviderError(Exception):
    """Raised when an SEC provider fails."""


class SecProvider(ABC):
    """Abstract SEC provider interface."""

    @abstractmethod
    def discover_filings(
        self, ticker: str, form: str = "10-K", limit: int = 1
    ) -> list[SourceFiling]:
        """Discover SEC filings for a ticker."""

    @abstractmethod
    def parse_sections(self, filing: SourceFiling) -> list[SourceSection]:
        """Parse a filing into semantic sections (Item 1, 1A, 7, 8, etc.)."""

    @abstractmethod
    def extract_tables(self, filing: SourceFiling) -> list[SourceTable]:
        """Extract structured tables from a filing."""


class FixtureSecProvider(SecProvider):
    """SEC provider that reads from fixture data for offline testing.

    Produces deterministic synthetic filings with proper SEC structure
    (Item 1, 1A, 7, 8 for 10-K; Item 2 for 10-Q).
    """

    def __init__(self, fixture_data: dict[str, Any] | None = None) -> None:
        self._fixture = fixture_data or _default_fixture()

    def discover_filings(
        self, ticker: str, form: str = "10-K", limit: int = 1
    ) -> list[SourceFiling]:
        """Return fixture filings for the canary company."""
        filings: list[SourceFiling] = []
        for fdata in self._fixture.get("filings", []):
            if fdata["form_type"] != form:
                continue
            filing = SourceFiling(
                filing_id=fdata["filing_id"],
                company_id=fdata["company_id"],
                form_type=fdata["form_type"],
                filing_date=fdata["filing_date"],
                period_end=fdata["period_end"],
                accession_ref="[PRIVATE_REF]",
                provenance_key=build_provenance_key(
                    fdata["company_id"], "FILING", fdata["form_type"], fdata["period_end"][:4]
                ),
                section_count=len(fdata.get("sections", [])),
                table_count=len(fdata.get("tables", [])),
            )
            filings.append(filing)
            if len(filings) >= limit:
                break
        return filings

    def parse_sections(self, filing: SourceFiling) -> list[SourceSection]:
        """Parse fixture filing into sections."""
        sections: list[SourceSection] = []
        for fdata in self._fixture.get("filings", []):
            if fdata["filing_id"] != filing.filing_id:
                continue
            for sdata in fdata.get("sections", []):
                section = SourceSection(
                    section_id=sdata["section_id"],
                    filing_id=filing.filing_id,
                    company_id=filing.company_id,
                    item_id=sdata["item_id"],
                    item_title=sdata["item_title"],
                    text_content=sdata["text_content"],
                    char_count=len(sdata["text_content"]),
                    provenance_key=build_provenance_key(
                        filing.company_id,
                        "SECTION",
                        filing.form_type,
                        filing.period_end[:4],
                        sdata["item_id"],
                    ),
                )
                sections.append(section)
        return sections

    def extract_tables(self, filing: SourceFiling) -> list[SourceTable]:
        """Extract fixture tables."""
        tables: list[SourceTable] = []
        for fdata in self._fixture.get("filings", []):
            if fdata["filing_id"] != filing.filing_id:
                continue
            for tdata in fdata.get("tables", []):
                table = SourceTable(
                    table_id=tdata["table_id"],
                    filing_id=filing.filing_id,
                    company_id=filing.company_id,
                    table_name=tdata["table_name"],
                    table_data=tdata.get("rows", []),
                    row_count=len(tdata.get("rows", [])),
                    col_count=len(tdata.get("headers", [])),
                    provenance_key=build_provenance_key(
                        filing.company_id,
                        "TABLE",
                        filing.form_type,
                        filing.period_end[:4],
                        tdata["table_name"],
                    ),
                )
                tables.append(table)
        return tables


def _default_fixture() -> dict[str, Any]:
    """Default fixture data for Canary Holdings Corporation."""
    return {
        "company_id": "COMPANY_001",
        "company_name": "Canary Holdings Corporation",
        "ticker": "CHC",
        "cik": "0000999999",
        "filings": [
            {
                "filing_id": "filing-10k-2024",
                "company_id": "COMPANY_001",
                "form_type": "10-K",
                "filing_date": "2025-02-15",
                "period_end": "2024-12-31",
                "sections": [
                    {
                        "section_id": "sec-item-1",
                        "item_id": "ITEM_1",
                        "item_title": "Business",
                        "text_content": (
                            "Canary Holdings Corporation operates as a diversified "
                            "financial services company. The company provides banking, "
                            "wealth management, and insurance products through its "
                            "subsidiaries. Revenue is primarily generated from net "
                            "interest income and fee-based services."
                        ),
                    },
                    {
                        "section_id": "sec-item-1a",
                        "item_id": "ITEM_1A",
                        "item_title": "Risk Factors",
                        "text_content": (
                            "The company faces risks from interest rate fluctuations, "
                            "credit losses, regulatory changes, and economic downturns. "
                            "Concentration in commercial lending may amplify losses "
                            "during recessionary periods. Cybersecurity threats pose "
                            "ongoing operational risks."
                        ),
                    },
                    {
                        "section_id": "sec-item-7",
                        "item_id": "ITEM_7",
                        "item_title": "Management's Discussion and Analysis",
                        "text_content": (
                            "Net income increased 12% year-over-year driven by higher "
                            "net interest margin and strong fee income. Loan growth "
                            "was 8% with stable credit quality. The allowance for "
                            "credit losses decreased modestly reflecting improved "
                            "macroeconomic conditions. Operating expenses were well "
                            "controlled with positive operating leverage."
                        ),
                    },
                    {
                        "section_id": "sec-item-8",
                        "item_id": "ITEM_8",
                        "item_title": "Financial Statements",
                        "text_content": (
                            "Total assets were $150 billion at period end. Total loans "
                            "were $95 billion and total deposits were $120 billion. "
                            "Tier 1 capital ratio was 11.2%. Net interest income was "
                            "$4.8 billion and noninterest income was $1.6 billion."
                        ),
                    },
                ],
                "tables": [
                    {
                        "table_id": "tbl-income-statement",
                        "table_name": "INCOME_STATEMENT",
                        "headers": ["Line Item", "FY2024", "FY2023"],
                        "rows": [
                            {
                                "Line Item": "Net Interest Income",
                                "FY2024": "4800",
                                "FY2023": "4300",
                            },
                            {"Line Item": "Noninterest Income", "FY2024": "1600", "FY2023": "1450"},
                            {"Line Item": "Total Revenue", "FY2024": "6400", "FY2023": "5750"},
                            {
                                "Line Item": "Provision for Credit Losses",
                                "FY2024": "500",
                                "FY2023": "700",
                            },
                            {"Line Item": "Net Income", "FY2024": "1800", "FY2023": "1550"},
                        ],
                    },
                    {
                        "table_id": "tbl-balance-sheet",
                        "table_name": "BALANCE_SHEET",
                        "headers": ["Line Item", "FY2024", "FY2023"],
                        "rows": [
                            {"Line Item": "Total Loans", "FY2024": "95000", "FY2023": "88000"},
                            {"Line Item": "Total Deposits", "FY2024": "120000", "FY2023": "112000"},
                            {"Line Item": "Total Assets", "FY2024": "150000", "FY2023": "140000"},
                            {"Line Item": "Total Equity", "FY2024": "18000", "FY2023": "16500"},
                        ],
                    },
                ],
            },
        ],
        "news": [
            {
                "news_id": "news-001",
                "headline": "Canary Holdings reports Q4 earnings beat",
                "published_date": "2025-01-20",
                "text_content": "Canary Holdings Corporation reported Q4 earnings above analyst expectations.",
            },
            {
                "news_id": "news-002",
                "headline": "Canary Holdings announces dividend increase",
                "published_date": "2025-02-01",
                "text_content": "The board approved a 10% increase in the quarterly dividend.",
            },
            {
                "news_id": "news-003",
                "headline": "Canary Holdings completes acquisition",
                "published_date": "2025-03-15",
                "text_content": "Canary Holdings completed the acquisition of a regional insurance provider.",
            },
        ],
    }


# ── SEC semantic validation ────────────────────────────────────────────


def validate_10k_sections(sections: list[SourceSection]) -> list[str]:
    """Validate that a 10-K has required sections (Item 7, Item 8).

    Returns list of violations (empty = valid).
    """
    violations: list[str] = []
    item_ids = {s.item_id for s in sections}
    if "ITEM_7" not in item_ids:
        violations.append("10-K missing Item 7 (MD&A)")
    if "ITEM_8" not in item_ids:
        violations.append("10-K missing Item 8 (Financial Statements)")
    return violations


def validate_10q_sections(sections: list[SourceSection]) -> list[str]:
    """Validate that a 10-Q has required sections (Item 2).

    Returns list of violations (empty = valid).
    """
    violations: list[str] = []
    item_ids = {s.item_id for s in sections}
    if "ITEM_2" not in item_ids:
        violations.append("10-Q missing Item 2 (MD&A)")
    return violations


def validate_filing_date(
    form_type: str, filing_date: str, period_end: str, release_date: str
) -> list[str]:
    """Validate filing date constraints.

    - 8-K must not be future-dated
    - 10-Q must not be Q4 (Q4 10-Q is invalid — use 10-K)
    - Filename period must match filing period
    """
    violations: list[str] = []

    if form_type == "8-K" and filing_date > release_date:
        violations.append(
            f"Future-dated 8-K: filing_date {filing_date} > release_date {release_date}"
        )

    if form_type == "10-Q":
        month = int(period_end.split("-")[1]) if "-" in period_end else 0
        if month == 12:
            violations.append("Q4 10-Q is invalid — use 10-K for fiscal year end")

    return violations


def validate_filename_period_match(filename: str, form_type: str, period_end: str) -> list[str]:
    """Validate that filename period matches filing period."""
    violations: list[str] = []
    # Check that the filename contains the period year
    year = period_end[:4] if len(period_end) >= 4 else ""
    if year and year not in filename:
        violations.append(f"Filename '{filename}' does not contain period year '{year}'")
    return violations
