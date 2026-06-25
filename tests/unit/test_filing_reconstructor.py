"""Unit tests for FilingReconstructor."""

from __future__ import annotations

from fenrix_synthetic.reconstruct.filing_reconstructor import FilingReconstructor
from fenrix_synthetic.reconstruct.filing_sections import (
    SECTION_BUSINESS,
    SECTION_COVERAGE,
)

SAMPLE_SECTIONS = [
    {
        "section_type": "business",
        "content": "CIK: 1234567890 filed its annual report. ACME Corp is a leading provider of software solutions.",
    },
    {
        "section_type": "risk_factors",
        "content": "ACME Corp faces competition from established players. ACCESSION NUMBER: 0001234567-24-000001.",
    },
    {
        "section_type": "mda",
        "content": "Management discusses the financial results for fiscal year 2023.",
    },
]


SAMPLE_SECTIONS_NO_IDENTIFIERS = [
    {
        "section_type": "business",
        "content": "The company is a leading provider of software solutions.",
    },
]


class TestFilingReconstructor:
    """Test FilingReconstructor public output."""

    def test_public_reconstruction_removes_source_company_name(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", SAMPLE_SECTIONS)
        content = result[SECTION_BUSINESS]["content"]
        assert "ACME Corp" not in content

    def test_public_reconstruction_removes_cik(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", SAMPLE_SECTIONS)
        combined = " ".join(v["content"] for v in result.values())
        assert "1234567890" not in combined

    def test_public_reconstruction_removes_accession(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", SAMPLE_SECTIONS)
        combined = " ".join(v["content"] for v in result.values())
        assert "0001234567-24-000001" not in combined

    def test_public_reconstruction_uses_relative_periods(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", SAMPLE_SECTIONS)
        combined = " ".join(v["content"] for v in result.values())
        # Exact dates should be replaced
        assert "2023" not in combined or "[YEAR]" in combined

    def test_deterministic_with_same_seed(self) -> None:
        r1 = FilingReconstructor().reconstruct("COMP_FIXTURE_001", SAMPLE_SECTIONS_NO_IDENTIFIERS)
        r2 = FilingReconstructor().reconstruct("COMP_FIXTURE_001", SAMPLE_SECTIONS_NO_IDENTIFIERS)
        assert r1[SECTION_BUSINESS]["content"] == r2[SECTION_BUSINESS]["content"]

    def test_coverage_section_exists(self) -> None:
        result = FilingReconstructor().reconstruct("COMP_FIXTURE_001", SAMPLE_SECTIONS)
        assert SECTION_COVERAGE in result
        assert "Filing Coverage" in result[SECTION_COVERAGE]["content"]

    def test_placeholder_for_missing_sections(self) -> None:
        result = FilingReconstructor().reconstruct("COMP_FIXTURE_001", [])
        assert SECTION_BUSINESS in result
        assert len(result) > 1  # Should have all section types even with no input
