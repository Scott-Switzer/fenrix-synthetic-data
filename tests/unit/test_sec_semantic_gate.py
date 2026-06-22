"""Tests for SEC semantic validation gates."""

from __future__ import annotations

from fenrix_synthetic.professor.evidence import SourceSection, build_provenance_key
from fenrix_synthetic.professor.sec_providers import (
    FixtureSecProvider,
    validate_10k_sections,
    validate_10q_sections,
    validate_filename_period_match,
    validate_filing_date,
)


def _make_section(item_id: str, item_title: str = "") -> SourceSection:
    return SourceSection(
        section_id=f"sec-{item_id.lower()}",
        filing_id="filing-001",
        company_id="COMPANY_001",
        item_id=item_id,
        item_title=item_title or item_id,
        text_content="Test content",
        provenance_key=build_provenance_key("COMPANY_001", "SECTION", "10-K", "2024", item_id),
    )


class TestSecSemanticGate:
    def test_10k_with_item7_and_item8_passes(self) -> None:
        sections = [_make_section("ITEM_7"), _make_section("ITEM_8")]
        assert validate_10k_sections(sections) == []

    def test_10k_missing_item7_fails(self) -> None:
        sections = [_make_section("ITEM_8")]
        violations = validate_10k_sections(sections)
        assert any("Item 7" in v for v in violations)

    def test_10k_missing_item8_fails(self) -> None:
        sections = [_make_section("ITEM_7")]
        violations = validate_10k_sections(sections)
        assert any("Item 8" in v for v in violations)

    def test_10q_with_item2_passes(self) -> None:
        sections = [_make_section("ITEM_2")]
        assert validate_10q_sections(sections) == []

    def test_10q_missing_item2_fails(self) -> None:
        sections = [_make_section("ITEM_1")]
        violations = validate_10q_sections(sections)
        assert any("Item 2" in v for v in violations)

    def test_future_dated_8k_fails(self) -> None:
        violations = validate_filing_date("8-K", "2026-12-31", "2026-06-30", "2026-06-22")
        assert any("Future-dated" in v for v in violations)

    def test_past_dated_8k_passes(self) -> None:
        violations = validate_filing_date("8-K", "2026-01-15", "2026-01-01", "2026-06-22")
        assert violations == []

    def test_q4_10q_fails(self) -> None:
        violations = validate_filing_date("10-Q", "2026-02-15", "2025-12-31", "2026-06-22")
        assert any("Q4 10-Q" in v for v in violations)

    def test_q1_10q_passes(self) -> None:
        violations = validate_filing_date("10-Q", "2025-05-15", "2025-03-31", "2026-06-22")
        assert violations == []

    def test_filename_period_match_passes(self) -> None:
        violations = validate_filename_period_match("filing_2024_10k.html", "10-K", "2024-12-31")
        assert violations == []

    def test_filename_period_mismatch_fails(self) -> None:
        violations = validate_filename_period_match("filing_2023_10k.html", "10-K", "2024-12-31")
        assert any("period year" in v for v in violations)

    def test_fixture_provider_returns_10k_with_required_sections(self) -> None:
        provider = FixtureSecProvider()
        filings = provider.discover_filings("CHC", form="10-K", limit=1)
        assert len(filings) == 1
        sections = provider.parse_sections(filings[0])
        item_ids = {s.item_id for s in sections}
        assert "ITEM_7" in item_ids
        assert "ITEM_8" in item_ids
        assert validate_10k_sections(sections) == []
