"""Unit tests for FilingReconstructionAttack."""

from __future__ import annotations

from fenrix_synthetic.qa.filing_reconstruction_attack import (
    FilingReconstructionAttack,
    check_public_sec_directory,
)


class TestFilingReconstructionAttack:
    """Test filing reconstruction attack detection."""

    def test_attack_catches_raw_sec_text(self) -> None:
        attack = FilingReconstructionAttack()
        sections = [
            {"content": "CIK: 1234567890 CENTRAL INDEX KEY: 0001234567"},
        ]
        result = attack.run("COMP_FIXTURE_001", sections)
        assert not result["passes"]
        assert any("cik" in v.lower() for v in result["violations"])

    def test_attack_catches_xbrl_namespace(self) -> None:
        attack = FilingReconstructionAttack()
        sections = [
            {"content": "The company uses us-gaap accounting standards."},
        ]
        result = attack.run("COMP_FIXTURE_001", sections)
        assert not result["passes"]

    def test_attack_catches_sec_gov_url(self) -> None:
        attack = FilingReconstructionAttack()
        sections = [
            {"content": "See www.sec.gov for more information."},
        ]
        result = attack.run("COMP_FIXTURE_001", sections)
        assert not result["passes"]

    def test_attack_passes_clean_markdown(self) -> None:
        attack = FilingReconstructionAttack()
        sections = [
            {"content": "# Business Overview\n\nThe company operates in the technology sector.\n"},
        ]
        result = attack.run("COMP_FIXTURE_001", sections)
        assert result["passes"]

    def test_attack_detects_html_file_reference(self) -> None:
        attack = FilingReconstructionAttack()
        sections = [
            {"content": "See the attached file: annual_report.html"},
        ]
        result = attack.run("COMP_FIXTURE_001", sections)
        assert not result["passes"]

    def test_attack_detects_form_header(self) -> None:
        attack = FilingReconstructionAttack()
        sections = [
            {"content": "FORM 10-K for the fiscal year ended December 31, 2023"},
        ]
        result = attack.run("COMP_FIXTURE_001", sections)
        assert not result["passes"]

    def test_attack_returns_section_count(self) -> None:
        attack = FilingReconstructionAttack()
        sections = [{"content": "test1"}, {"content": "test2"}, {"content": "test3"}]
        result = attack.run("COMP_FIXTURE_001", sections)
        assert result["num_sections_checked"] == 3


class TestCheckPublicSecDirectory:
    """Test check_public_sec_directory utility."""

    def test_missing_directory_returns_violation(self) -> None:
        result = check_public_sec_directory("/nonexistent/path")
        assert not result["passes"]
        assert "Directory not found" in result["violations"]
