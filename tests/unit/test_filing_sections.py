"""Unit tests for filing section extraction."""

from __future__ import annotations

from fenrix_synthetic.reconstruct.filing_sections import (
    extract_business_section,
    extract_governance_section,
    extract_material_events_section,
    extract_mda_section,
    extract_quarterly_section,
    extract_risk_factors_section,
)


TENK_TEXT = """
ITEM 1. BUSINESS

We are a leading technology company serving enterprise customers.

ITEM 1A. RISK FACTORS

Investing in our securities involves substantial risks.

ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS

Our financial results reflect strong performance.

ITEM 8. FINANCIAL STATEMENTS

See our consolidated financial statements below.
"""

EIGHTK_TEXT = """
ITEM 8.01 OTHER EVENTS

On January 15, 2024, the company announced a strategic acquisition.

ITEM 9.01 FINANCIAL STATEMENTS AND EXHIBITS

The financial statements are attached as exhibits.
"""

PROXY_TEXT = """
ITEM 10. DIRECTORS AND EXECUTIVE OFFICERS

Our board consists of nine directors.

PROXY STATEMENT

This proxy statement is solicited by the board of directors.
"""


class TestFilingSectionExtraction:
    """Test extraction of specific filing sections from raw text."""

    def test_extracts_business_section(self) -> None:
        result = extract_business_section(TENK_TEXT)
        assert "leading technology company" in result

    def test_extracts_risk_factors_section(self) -> None:
        result = extract_risk_factors_section(TENK_TEXT)
        assert "substantial risks" in result

    def test_extracts_mda_section(self) -> None:
        result = extract_mda_section(TENK_TEXT)
        assert "strong performance" in result

    def test_extracts_quarterly_section(self) -> None:
        text = """
ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS

Quarterly results show strong growth.

ITEM 3. QUANTITATIVE AND QUALITATIVE DISCLOSURES
"""
        result = extract_quarterly_section(text)
        assert "Quarterly results" in result

    def test_extracts_eightk_item_categories(self) -> None:
        result = extract_material_events_section(EIGHTK_TEXT)
        assert "strategic acquisition" in result

    def test_extracts_governance_section(self) -> None:
        result = extract_governance_section(PROXY_TEXT)
        assert "nine directors" in result or "proxy statement" in result

    def test_empty_text_returns_empty(self) -> None:
        assert extract_business_section("") == ""

    def test_no_match_returns_empty(self) -> None:
        result = extract_business_section("Some random text without section markers.")
        assert result == ""
