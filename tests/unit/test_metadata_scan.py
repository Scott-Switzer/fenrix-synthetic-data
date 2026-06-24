"""Tests for metadata scanner.

Validates detection of SEC/iXBRL metadata artifacts in release files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fenrix_synthetic.qa.metadata_scan import MetadataHit, MetadataScanResult, scan_metadata


def _write_file(base: Path, name: str, content: str) -> Path:
    fp = base / name
    fp.write_text(content, encoding="utf-8")
    return fp


class TestMetadataScanXBRL:
    """Test detection of XBRL-specific metadata patterns."""

    def test_detects_ix_hidden(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", "<ix:hidden><p>hidden content</p></ix:hidden>")
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("ix_hidden" in h.pattern_id for h in result.hits)

    def test_detects_dei_namespace(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", "<dei:DocumentType>10-K</dei:DocumentType>")
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("dei_tag" in h.pattern_id for h in result.hits)

    def test_detects_us_gaap_namespace(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", "<us-gaap:Assets>1000</us-gaap:Assets>")
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("us_gaap_tag" in h.pattern_id for h in result.hits)

    def test_detects_context_ref(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", 'contextRef="FY2024"')
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("context_ref" in h.pattern_id for h in result.hits)

    def test_detects_unit_ref(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", 'unitRef="USD"')
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("unit_ref" in h.pattern_id for h in result.hits)

    def test_detects_trading_symbol(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", "TradingSymbol: CHC")
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("trading_symbol" in h.pattern_id for h in result.hits)

    def test_detects_entity_registrant_name(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", "EntityRegistrantName: Canary Holdings")
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("entity_registrant_name" in h.pattern_id for h in result.hits)

    def test_detects_document_fiscal_year_focus(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.json", '{"DocumentFiscalYearFocus": "FY2024"}')
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("document_fiscal_year_focus" in h.pattern_id for h in result.hits)


class TestMetadataScanHTMLXML:
    """Test detection of HTML/XML declarations."""

    def test_detects_html_public_artifact(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "report.html", "<html><body>Report</body></html>")
        result = scan_metadata(tmp_path)
        # Should detect .html file as forbidden
        assert not result.passed
        assert any(h.pattern_id == "html_xml_present" for h in result.hits)

    def test_detects_xml_public_artifact(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "data.xml", '<?xml version="1.0"?><data/>')
        result = scan_metadata(tmp_path)
        assert not result.passed
        assert any(h.pattern_id == "html_xml_present" for h in result.hits)

    def test_detects_html_declaration_in_content(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", "<html lang=\"en\">")
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("html_declaration" in h.pattern_id for h in result.hits)

    def test_detects_xml_declaration_in_content(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", '<?xml version="1.0"?>')
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("xml_declaration" in h.pattern_id for h in result.hits)

    def test_detects_accession_dashed(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", "Accession: 0000999999-24-000001")
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("accession_dashed" in h.pattern_id for h in result.hits)

    def test_detects_sec_archive_url(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", "https://www.sec.gov/Archives/edgar/data/99999/")
        result = scan_metadata(tmp_path, scan_html_xml_files=True)
        assert not result.passed
        assert any("sec_archive_url" in h.pattern_id for h in result.hits)


class TestMetadataScanClean:
    """Test that clean content passes."""

    def test_passes_clean_markdown(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.md", "# Clean Analysis\n\nNo metadata here.\n")
        result = scan_metadata(tmp_path)
        assert result.passed

    def test_passes_clean_json(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "test.json", '{"name": "Company 001", "sector": "financials"}')
        result = scan_metadata(tmp_path)
        assert result.passed


class TestMetadataScanResult:
    """Test MetadataScanResult properties."""

    def test_to_dict(self) -> None:
        hits = [MetadataHit("a", "html_xml_present", "html_xml", ".html")]
        result = MetadataScanResult(scanned_files=1, scanned_bytes=50, hits=hits, passed=False)
        d = result.to_dict()
        assert d["total_hits"] == 1
        assert not d["passed"]
        assert "html_xml" in d["hits_by_category"]
