from __future__ import annotations

from fenrix_synthetic.masking.discovery import DiscoveredEntity
from fenrix_synthetic.reporting.coverage import CoverageReport, CoverageResult


class TestCoverageReport:
    def test_coverage_full(self):
        discovered = [
            DiscoveredEntity("Acme Corp", 0, 9, "capitalized_phrase", 0.4),
            DiscoveredEntity("(ACM)", 10, 15, "ticker_pattern", 0.6),
        ]
        accepted_spans = [(0, 9), (10, 15)]

        report = CoverageReport()
        result = report.compute(discovered, accepted_spans)
        assert result.total_discovered == 2
        assert result.total_masked == 2
        assert result.total_unmasked == 0
        assert result.coverage_pct == 100.0

    def test_coverage_partial(self):
        discovered = [
            DiscoveredEntity("Acme Corp", 0, 9, "capitalized_phrase", 0.4),
            DiscoveredEntity("(ACM)", 10, 15, "ticker_pattern", 0.6),
        ]
        accepted_spans = [(0, 9)]

        report = CoverageReport()
        result = report.compute(discovered, accepted_spans)
        assert result.total_discovered == 2
        assert result.total_masked == 1
        assert result.total_unmasked == 1
        assert result.coverage_pct == 50.0

    def test_coverage_no_discoveries(self):
        report = CoverageReport()
        result = report.compute([], [])
        assert result.total_discovered == 0
        assert result.coverage_pct == 0.0

    def test_coverage_high_confidence_unmasked(self):
        discovered = [
            DiscoveredEntity("Jane Smith", 0, 10, "executive_pattern", 0.8),
        ]
        accepted_spans: list[tuple[int, int]] = []

        report = CoverageReport()
        result = report.compute(discovered, accepted_spans)
        assert result.high_confidence_unmasked == 1
        assert len(result.warnings) >= 1

    def test_coverage_result_to_dict(self):
        result = CoverageResult(
            company_id="C001",
            document_artifact_id="bronze-C001-test",
            total_discovered=5,
            total_masked=3,
            total_unmasked=2,
            coverage_pct=60.0,
        )
        d = result.to_dict()
        assert d["company_id"] == "C001"
        assert d["coverage_pct"] == 60.0
        assert d["status"] == "completed"

    def test_coverage_result_to_dict_hashes_unmasked_text(self):
        result = CoverageResult(
            company_id="C001",
            unmasked_by_type={
                "capitalized_phrase": [{"text": "Acme Corp", "start": 0, "confidence": 0.4}]
            },
        )
        d = result.to_dict()
        entry = d["unmasked_by_type"]["capitalized_phrase"][0]
        assert "text_hash" in entry
        assert "text" not in entry
        assert isinstance(entry["text_hash"], str)
        assert len(entry["text_hash"]) == 16

    def test_entity_type_breakdown(self):
        discovered = [
            DiscoveredEntity("Acme Corp", 0, 9, "capitalized_phrase", 0.4),
            DiscoveredEntity("(ACM)", 10, 15, "ticker_pattern", 0.6),
            DiscoveredEntity("Jane Doe", 20, 28, "executive_pattern", 0.8),
        ]
        accepted_spans: list[tuple[int, int]] = []

        report = CoverageReport()
        result = report.compute(discovered, accepted_spans)
        assert result.entity_type_breakdown.get("capitalized_phrase", 0) >= 1
        assert result.entity_type_breakdown.get("ticker_pattern", 0) >= 1
        assert result.entity_type_breakdown.get("executive_pattern", 0) >= 1

    def test_warning_on_low_coverage(self):
        discovered = [
            DiscoveredEntity("Acme Corp", 0, 9, "capitalized_phrase", 0.4),
            DiscoveredEntity("(ACM)", 10, 15, "ticker_pattern", 0.6),
        ]
        accepted_spans: list[tuple[int, int]] = []

        report = CoverageReport()
        result = report.compute(discovered, accepted_spans)
        has_low_coverage_warning = any("coverage" in w.lower() for w in result.warnings)
        assert has_low_coverage_warning
