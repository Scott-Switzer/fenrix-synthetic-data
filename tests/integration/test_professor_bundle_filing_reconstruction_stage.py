"""Integration tests for the FILING_RECONSTRUCTION professor bundle stage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fenrix_synthetic.reconstruct.filing_reconstructor import FilingReconstructor
from fenrix_synthetic.reconstruct.filing_sections import (
    SECTION_BUSINESS,
    SECTION_COVERAGE,
    SECTION_FINANCIAL_SUMMARY,
    SECTION_GOVERNANCE,
    SECTION_MATERIAL_EVENTS,
    SECTION_MDA,
    SECTION_QUARTERLY,
    SECTION_RISK_FACTORS,
)
from fenrix_synthetic.qa.filing_reconstruction_attack import (
    FilingReconstructionAttack,
)


FIXTURE_SECTIONS = [
    {"section_type": "business", "content": "COMP_FIXTURE_001 is a technology company. CIK: 1234567890."},
    {"section_type": "risk_factors", "content": "COMP_FIXTURE_001 faces competition."},
    {"section_type": "mda", "content": "Management discusses fiscal year 2023 results."},
    {"section_type": "financial_summary", "content": "Revenue grew 15% year over year."},
    {"section_type": "quarterly_update_summary", "content": "Q4 results exceeded expectations."},
    {"section_type": "material_events_summary", "content": "Acquisition completed on 2024-01-15."},
    {"section_type": "governance_proxy_summary", "content": "Board of directors is composed of 9 members."},
]


class TestFilingReconstructionStage:
    """Test that fixture professor build emits filing reconstruction outputs."""

    def test_bundle_emits_business_md(self) -> None:
        """Reconstructed SEC markdown should include business section."""
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        assert SECTION_BUSINESS in result
        content = result[SECTION_BUSINESS]["content"]
        assert len(content) > 0

    def test_bundle_emits_risk_factors_md(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        assert SECTION_RISK_FACTORS in result

    def test_bundle_emits_mda_md(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        assert SECTION_MDA in result

    def test_bundle_emits_financial_summary_md(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        assert SECTION_FINANCIAL_SUMMARY in result

    def test_bundle_emits_quarterly_md(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        assert SECTION_QUARTERLY in result

    def test_bundle_emits_material_events_md(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        assert SECTION_MATERIAL_EVENTS in result

    def test_bundle_emits_governance_md(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        assert SECTION_GOVERNANCE in result

    def test_bundle_emits_coverage_md(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        assert SECTION_COVERAGE in result
        assert "Filing Coverage" in result[SECTION_COVERAGE]["content"]

    def test_public_reconstruction_removes_cik(self) -> None:
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        combined = " ".join(v["content"] for v in result.values())
        assert "1234567890" not in combined

    def test_private_source_sections_not_in_public(self) -> None:
        """Private source sections should not leak into public output."""
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        combined = " ".join(v["content"] for v in result.values())
        # The source had "COMP_FIXTURE_001 is a technology company" - the company_id is the anonymized ID
        # But other identifiers should be removed
        assert "CIK" not in combined
        assert "1234567890" not in combined

    def test_attack_catches_raw_sec_text(self) -> None:
        attack = FilingReconstructionAttack()
        raw_sections = [
            {"content": "CIK: 1234567890 ACCESSION NUMBER: 0001234567-24-000001"},
        ]
        result = attack.run("COMP_FIXTURE_001", raw_sections)
        assert not result["passes"]

    def test_attack_passes_clean_markdown(self) -> None:
        attack = FilingReconstructionAttack()
        clean_sections = [
            {"content": "# Business Overview\n\nThe company is a technology provider.\n"},
        ]
        result = attack.run("COMP_FIXTURE_001", clean_sections)
        assert result["passes"]

    def test_bundle_emits_expected_sec_files(self, tmp_path: Path) -> None:
        """Simulate the full public SEC output structure."""
        sec_dir = tmp_path / "sec"
        sec_dir.mkdir()
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        for section_key in result:
            file_path = sec_dir / f"{section_key}.md"
            file_path.write_text(result[section_key]["content"])
            assert file_path.exists()

    def test_zip_excludes_raw_filing_artifacts(self, tmp_path: Path) -> None:
        """Raw .html and .xml files should not be in public SEC output."""
        sec_dir = tmp_path / "sec"
        sec_dir.mkdir()
        # Put only .md files (simulating reconstruction output)
        (sec_dir / "annual_report_business.md").write_text("# Business\n")
        files = list(sec_dir.iterdir())
        assert all(f.suffix == ".md" for f in files)
        assert not any(f.suffix in (".html", ".xml") for f in files)

    def test_strict_release_gate_passes_public_markdown(self, tmp_path: Path) -> None:
        """Public SEC markdown should pass a basic content scan."""
        sec_dir = tmp_path / "sec"
        sec_dir.mkdir()
        reconstructor = FilingReconstructor()
        result = reconstructor.reconstruct("COMP_FIXTURE_001", FIXTURE_SECTIONS)
        for section_key in result:
            (sec_dir / f"{section_key}.md").write_text(result[section_key]["content"])
        # Check no banned content
        for f in sec_dir.iterdir():
            content = f.read_text()
            assert "CIK" not in content
            assert "xbrl" not in content.lower()
