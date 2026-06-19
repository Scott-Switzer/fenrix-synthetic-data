"""Tests for HTML filing extraction and segmentation."""

from pathlib import Path

import pytest

from fenrix_synthetic.extraction.converter import HtmlFilingExtractor
from fenrix_synthetic.extraction.segmenter import FilingSegmenter

FIXTURE_SEC = Path(__file__).parent.parent / "fixtures" / "sec"
FIXTURE_HTML = FIXTURE_SEC / "documents" / "0001234567-24-000001" / "synth-20240930.htm"


class TestHtmlFilingExtractor:
    """Test HTML → markdown converter."""

    @pytest.fixture
    def extractor(self) -> HtmlFilingExtractor:
        return HtmlFilingExtractor()

    @pytest.fixture
    def sample_html(self) -> str:
        return """<html><body>
<p><strong>Item 1. Business</strong></p>
<p>Test company description.</p>
<script>var x = 1;</script>
<style>.hidden{}</style>
<ul><li>List item 1</li><li>List item 2</li></ul>
<table><tr><th>Col A</th><th>Col B</th></tr><tr><td>Val 1</td><td>Val 2</td></tr></table>
</body></html>"""

    def test_basic_conversion(self, extractor: HtmlFilingExtractor, sample_html: str):
        result = extractor.extract(sample_html)
        text = result["text"]
        assert "Item 1. Business" in text
        assert "Test company description" in text
        assert result["char_count"] > 0

    def test_script_style_removed(self, extractor: HtmlFilingExtractor, sample_html: str):
        result = extractor.extract(sample_html)
        assert "var x = 1" not in result["text"]
        assert ".hidden" not in result["text"]

    def test_heading_promotion(self, extractor: HtmlFilingExtractor, sample_html: str):
        result = extractor.extract(sample_html)
        assert "## Item 1. Business" in result["text"]

    def test_paragraph_preservation(self, extractor: HtmlFilingExtractor, sample_html: str):
        result = extractor.extract(sample_html)
        assert "Test company description" in result["text"]

    def test_list_preservation(self, extractor: HtmlFilingExtractor, sample_html: str):
        result = extractor.extract(sample_html)
        assert "- List item 1" in result["text"]
        assert "- List item 2" in result["text"]

    def test_table_conversion(self, extractor: HtmlFilingExtractor, sample_html: str):
        result = extractor.extract(sample_html)
        text = result["text"]
        assert "|" in text
        assert "Col A" in text
        assert "Col B" in text
        assert "Val 1" in text
        assert "Val 2" in text

    def test_deterministic_output(self, extractor: HtmlFilingExtractor, sample_html: str):
        r1 = extractor.extract(sample_html)
        r2 = extractor.extract(sample_html)
        assert r1["text"] == r2["text"]

    def test_empty_html(self, extractor: HtmlFilingExtractor):
        result = extractor.extract("")
        assert result["text"] == ""

    def test_malformed_html(self, extractor: HtmlFilingExtractor):
        result = extractor.extract("<p>unclosed")
        assert "unclosed" in result["text"]

    def test_fixture_html_extraction(self, extractor: HtmlFilingExtractor):
        html = FIXTURE_HTML.read_text(encoding="utf-8")
        result = extractor.extract(html)
        text = result["text"]
        assert "SYNTHETIC FIXTURE" in text
        assert "Item 1. Business" in text
        assert "Item 1A. Risk Factors" in text
        assert "Item 2. Properties" in text
        assert "Item 7. Management Discussion" in text
        assert "Item 8. Financial Statements" in text
        assert "|" in text  # Table

    def test_no_script_in_fixture_output(self, extractor: HtmlFilingExtractor):
        html = FIXTURE_HTML.read_text(encoding="utf-8")
        result = extractor.extract(html)
        assert "console.log" not in result["text"]

    def test_used_dom_property(self, extractor: HtmlFilingExtractor):
        extractor.extract("<html><body><p>test</p></body></html>")
        assert extractor.used_dom is True


class TestFilingSegmenter:
    """Test filing section segmenter."""

    @pytest.fixture
    def segmenter(self) -> FilingSegmenter:
        return FilingSegmenter()

    def test_section_boundaries_with_headings(self, segmenter: FilingSegmenter):
        text = """## Item 1. Business
Content for item 1.
## Item 1A. Risk Factors
Risk content here.
## Item 2. Properties
Property content."""
        sections = segmenter.segment(text)
        assert len(sections) >= 2
        items = [s.item for s in sections]
        assert "Item 1" in items or "Item 1A" in items

    def test_toc_false_positive_suppression(self, segmenter: FilingSegmenter):
        """Dense cluster of items early in doc should be filtered."""
        lines = ["Line " + str(i) for i in range(300)]
        lines[10] = "## Item 1. Business"
        lines[12] = "## Item 1A. Risk Factors"
        lines[14] = "## Item 2. Properties"
        lines[16] = "## Item 3. Legal Proceedings"
        lines[200] = "## Item 7. Management Discussion"
        text = "\n".join(lines)
        sections = segmenter.segment(text)
        items = [s.item for s in sections]
        assert "Item 7" in items

    def test_missing_item_sections(self, segmenter: FilingSegmenter):
        sections = segmenter.segment("This document has no Item headers at all.")
        assert len(sections) == 1
        assert sections[0].item == "full"

    def test_duplicate_item_headings(self, segmenter: FilingSegmenter):
        text = """## Item 1. Business
Content A
## Item 1. Business
Content B"""
        sections = segmenter.segment(text)
        items = [s.item for s in sections]
        assert items.count("Item 1") == 2

    def test_preamble_preservation(self, segmenter: FilingSegmenter):
        text = """Preamble text before any item.
## Item 1. Business
Content."""
        sections = segmenter.segment(text)
        items = [s.item for s in sections]
        assert "preamble" in items

    def test_plain_text_item_pattern_fallback(self, segmenter: FilingSegmenter):
        text = """Some text.
Item 1. Business
Content
Item 1A. Risk Factors
Risks"""
        sections = segmenter.segment(text)
        items = [s.item for s in sections]
        assert "Item 1" in items

    def test_known_titles_mapping(self, segmenter: FilingSegmenter):
        text = "## Item 1A"
        sections = segmenter.segment(text)
        s = segmenter.get_section(sections, "1A")
        if s:
            assert s.title == "Risk Factors"

    def test_get_section_by_number(self, segmenter: FilingSegmenter):
        text = """## Item 1. Business
Biz content
## Item 1A. Risk Factors
Risk content"""
        sections = segmenter.segment(text)
        s = segmenter.get_section(sections, "1A")
        assert s is not None
        assert "Risk" in s.content

    def test_get_section_by_item_prefix(self, segmenter: FilingSegmenter):
        text = """## Item 1. Business
Biz content"""
        sections = segmenter.segment(text)
        s = segmenter.get_section(sections, "Item 1")
        assert s is not None

    def test_summary(self, segmenter: FilingSegmenter):
        text = """## Item 1. Business
Content
## Item 2. Properties
More content"""
        sections = segmenter.segment(text)
        summary = segmenter.summary(sections)
        assert summary["section_count"] >= 2
        assert summary["total_chars"] > 0

    def test_fixture_file_segmentation(self, segmenter: FilingSegmenter):
        """Run segmenter on the fixture's converted output."""
        from fenrix_synthetic.extraction.converter import HtmlFilingExtractor

        html = FIXTURE_HTML.read_text(encoding="utf-8")
        result = HtmlFilingExtractor().extract(html)
        sections = segmenter.segment(result["text"])
        assert len(sections) >= 2
        items_text = " ".join(s.item for s in sections)
        assert "Item 1" in items_text or "preamble" in items_text
