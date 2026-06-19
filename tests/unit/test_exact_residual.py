from __future__ import annotations

from pathlib import Path

from fenrix_synthetic.attacks.exact_match import ExactResidualScanner, ScanHit, ScanResult

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


class TestExactResidualScanner:
    def test_scan_text_finds_literal(self):
        scanner = ExactResidualScanner()
        values = {"canary": ["Canary Holdings"]}
        result = scanner.scan_text(
            "Canary Holdings Corporation is a company.",
            values,
        )
        assert result.total_hits > 0
        assert result.is_blocked

    def test_scan_text_no_match(self):
        scanner = ExactResidualScanner()
        values = {"canary": ["Eleanor Testperson"]}
        result = scanner.scan_text(
            "This document contains nothing suspicious.",
            values,
        )
        assert result.total_hits == 0
        assert not result.is_blocked

    def test_scan_metadata_finds_value(self):
        scanner = ExactResidualScanner()
        values = {"ticker": ["CHC"]}
        result = scanner.scan_metadata(
            {"source": "CHC 10-K filing"},
            values,
        )
        assert result.total_hits > 0

    def test_blocking_types(self):
        scanner = ExactResidualScanner()
        values = {
            "canary": ["Canary Holdings"],
            "nonblocking": ["hello"],
        }
        result = scanner.scan_text("Canary Holdings says hello", values)
        assert result.blocking_hits >= 1

    def test_canary_document_full_scan(self):
        scanner = ExactResidualScanner()
        doc_path = FIXTURE_DIR / "canary_document.md"
        text = doc_path.read_text()

        values = {
            "company": ["Canary Holdings Corporation", "Canary Holdings"],
            "ticker": ["CHC"],
            "cik": ["0000999999"],
            "executive": ["Eleanor Testperson"],
            "company_domain": ["canary-test.invalid"],
            "sec_accession_number": ["0000999999-26-000001"],
            "canary": ["CanaryShield 9000"],
        }

        result = scanner.scan_text(text, values)
        assert result.total_hits > 0, "Canary document must have hits"
        assert result.is_blocked, "Canary document must be blocked"

    def test_clean_document_zero_hits(self):
        scanner = ExactResidualScanner()
        doc_path = FIXTURE_DIR / "clean_document.md"
        text = doc_path.read_text()

        values = {
            "canary": [
                "Canary Holdings",
                "CHC",
                "Eleanor Testperson",
                "canary-test.invalid",
                "CanaryShield 9000",
            ],
        }

        result = scanner.scan_text(text, values)
        assert result.total_hits == 0, f"Clean document should have 0 hits, got {result.total_hits}"
        assert not result.is_blocked

    def test_URL_email_detection(self):
        scanner = ExactResidualScanner()
        text = "Email us at info@canary-test.invalid or visit https://www.canary-test.invalid"
        values = {"company_domain": ["canary-test.invalid"]}
        result = scanner.scan_text(text, values)
        assert result.total_hits >= 2  # email + URL

    def test_accession_number_variants(self):
        scanner = ExactResidualScanner()
        text = "Accession 0000999999-26-000001 or 000099999926000001"
        values = {"sec_accession_number": ["0000999999-26-000001"]}
        result = scanner.scan_text(text, values)
        assert result.total_hits >= 1

    def test_ticker_variants(self):
        scanner = ExactResidualScanner()
        text = "NYSE: CHC (CHC)"
        values = {"ticker": ["CHC"]}
        result = scanner.scan_text(text, values)
        assert result.total_hits >= 2


class TestScanResult:
    def test_add_hit(self):
        result = ScanResult()
        result.add_hit("test", ScanHit(value="secret", location="text"), is_blocking=True)
        assert result.total_hits == 1
        assert result.blocking_hits == 1
        assert result.is_blocked

    def test_non_blocking_hit(self):
        result = ScanResult()
        result.add_hit("test", ScanHit(value="public", location="text"), is_blocking=False)
        assert result.total_hits == 1
        assert result.blocking_hits == 0
        assert not result.is_blocked

    def test_empty_result(self):
        result = ScanResult()
        assert result.total_hits == 0
        assert result.blocking_hits == 0
        assert not result.is_blocked
