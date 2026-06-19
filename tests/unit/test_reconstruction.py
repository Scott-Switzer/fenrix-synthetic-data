from __future__ import annotations

from fenrix_synthetic.masking.deterministic import MatchEntry
from fenrix_synthetic.masking.reconstruction import DocumentReconstructor


def make_match(
    span_id: str,
    start: int,
    end: int,
    replacement: str,
    match_policy: str = "literal",
) -> MatchEntry:
    return MatchEntry(
        span_id=span_id,
        document_artifact_id="doc-001",
        original_start=start,
        original_end=end,
        entity_id="ent-001",
        alias_id="ali-001",
        entity_type="company",
        match_policy=match_policy,
        priority=100,
        matched_text="x" * (end - start),
        replacement=replacement,
    )


class TestDocumentReconstructor:
    def test_basic_replacement(self):
        reconstructor = DocumentReconstructor()
        # "Hello Canary Holdings Corporation world"
        # "Canary Holdings Corporation" = positions 6-32, end=33
        text = "Hello Canary Holdings Corporation world"
        spans = [
            make_match("s1", 6, 33, "Company 001"),
        ]
        result = reconstructor.apply_replacements(text, spans)
        assert result == "Hello Company 001 world", f"got: {result!r}"

    def test_multiple_replacements(self):
        reconstructor = DocumentReconstructor()
        text = "CHC is a great company (CHC)"
        # First CHC at (0,3), second at (24,27)
        spans = [
            make_match("s1", 0, 3, "Ticker 001"),
            make_match("s2", 24, 27, "Ticker 001"),
        ]
        result = reconstructor.apply_replacements(text, spans)
        assert result == "Ticker 001 is a great company (Ticker 001)", f"got: {result!r}"

    def test_reverse_order_replacement(self):
        reconstructor = DocumentReconstructor()
        text = "First Canary Holdings and then Canary Holdings Corporation"
        # "Canary Holdings" at (6, 21) (15 chars), "Canary Holdings Corporation" at (31, 58) (27 chars)
        spans = [
            make_match("s1", 6, 21, "Company 001", match_policy="literal"),
            make_match("s2", 31, 58, "Company 001", match_policy="literal"),
        ]
        result = reconstructor.apply_replacements(text, spans)
        assert result == "First Company 001 and then Company 001", f"got: {result!r}"

    def test_punctuation_preserved(self):
        reconstructor = DocumentReconstructor()
        text = "(CHC)"
        spans = [
            make_match("s1", 1, 4, "Ticker 001"),
        ]
        result = reconstructor.apply_replacements(text, spans)
        assert result == "(Ticker 001)", f"got: {result!r}"

    def test_possessive_preserved(self):
        reconstructor = DocumentReconstructor()
        # "Canary Holdings" (without 's suffix) at (0, 15)
        text = "Canary Holdings's revenue"
        spans = [
            make_match("s1", 0, 15, "Company 001"),
        ]
        result = reconstructor.apply_replacements(text, spans)
        assert result == "Company 001's revenue", f"got: {result!r}"

    def test_table_content_preserved_structure(self):
        reconstructor = DocumentReconstructor()
        text = "| CHC | $1.2B |\n|-----|-------|\n| 2024 | 180M |"
        spans = [
            make_match("s1", 2, 5, "Ticker 001"),
        ]
        result = reconstructor.apply_replacements(text, spans)
        assert "Ticker 001" in result
        assert "|" in result

    def test_empty_text(self):
        reconstructor = DocumentReconstructor()
        result = reconstructor.apply_replacements("", [])
        assert result == ""

    def test_no_matches(self):
        reconstructor = DocumentReconstructor()
        result = reconstructor.apply_replacements("Hello world", [])
        assert result == "Hello world"

    def test_replacements_out_of_bounds_handled(self):
        reconstructor = DocumentReconstructor()
        text = "Short"
        spans = [
            make_match("bad", 0, 100, "Company 001"),
        ]
        result = reconstructor.apply_replacements(text, spans)
        assert result == "Short"

    def test_rebuild_sections(self):
        reconstructor = DocumentReconstructor()
        sections = [
            {"item": "1", "title": "Business", "char_count": 500},
            {"item": "2", "title": "Risk Factors", "char_count": 300},
        ]
        result = reconstructor.rebuild_sections(sections, "masked text")
        assert len(result) == 2
        assert result[0]["item"] == "1"
