"""Unit tests for news reconstructor."""

from __future__ import annotations

import csv
from pathlib import Path

from fenrix_synthetic.anonymization.news_reconstructor import (
    EVENT_CLASSES,
    NewsReconstructor,
    PrivateSourceEvent,
)


class TestNewsReconstructor:
    """Test the synthetic news reconstructor."""

    def test_generates_from_fake_events(self) -> None:
        """Synthetic news generated from fake private event fixtures."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="strategic_investment",
                source_type="8-K",
                source_date="2024-03-15",
                source_headline="Acme Corp Announces $5B Acquisition of Rival Corp",
                source_body="Acme Corp today announced the acquisition of Rival Corp for $5B.",
                source_url="https://example.com/news/1",
                source_company="Acme Corporation",
                source_ticker="ACM",
                deal_value=5_000_000_000,
                counterparty="Rival Corp",
            ),
            PrivateSourceEvent(
                event_id="evt_002",
                event_class="margin_pressure",
                source_type="filing_section",
                source_date="2023-10-20",
                source_headline="Quarterly Results Show Margin Compression",
                source_body="Margins compressed due to rising input costs.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        assert len(briefs) == 2

    def test_uses_controlled_event_classes(self) -> None:
        """Output uses controlled event classes."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="competitive_pressure",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Competition Intensifies",
                source_body="Market share under pressure.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        assert briefs[0].event_class in EVENT_CLASSES
        assert briefs[0].event_class == "competitive_pressure"

    def test_removes_fake_source_company(self) -> None:
        """Output removes fake source company name."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Acme Corp Sees Demand Shift",
                source_body="Acme Corporation reported demand changes.",
                source_company="Acme Corporation",
                source_ticker="ACM",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        md = briefs[0].to_markdown()
        assert "Acme Corporation" not in md
        assert "Acme Corp" not in md

    def test_removes_fake_ticker(self) -> None:
        """Output removes fake ticker."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Stock ACM Falls",
                source_body="No tickers in output.",
                source_ticker="ACM",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        md = briefs[0].to_markdown()
        assert "ACM" not in md

    def test_uses_relative_periods(self) -> None:
        """Output uses relative periods, not exact dates."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-03-15",
                source_headline="Test",
                source_body="Test body.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events, ref_year=2026)

        md = briefs[0].to_markdown()
        assert "Year -2" in md or "2024-03-15" not in md

    def test_removes_exact_date(self) -> None:
        """Output should use relative periods instead of exact dates."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-03-15",
                source_headline="Test on 2024-03-15",
                source_body="Event occurred on 2024-03-15.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events, ref_year=2026)

        md = briefs[0].to_markdown()
        # The reconstructor generates synthetic descriptions, not copying source text
        # So exact dates from source won't appear
        assert "2024-03-15" not in md

    def test_removes_url(self) -> None:
        """Output removes URLs."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Test",
                source_body="Check https://example.com/news for details.",
                source_url="https://example.com/news/1",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        md = briefs[0].to_markdown()
        assert "https://" not in md

    def test_removes_copied_headline(self) -> None:
        """Output should not copy original headlines."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Acme Corp Announces Record Profits in 2024",
                source_body="Profits are up.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        md = briefs[0].to_markdown()
        # The synthetic description comes from templates, not the source headline
        assert "Record Profits" not in md

    def test_removes_deal_value(self) -> None:
        """Output removes exact deal value."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="strategic_investment",
                source_type="8-K",
                source_date="2024-01-01",
                source_headline="Acquisition for $5,000,000,000",
                source_body="The deal is valued at $5B.",
                deal_value=5_000_000_000,
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        md = briefs[0].to_markdown()
        assert "5000000000" not in md.lower()
        assert "5 billion" not in md.lower()
        assert "$5B" not in md

    def test_output_is_valid_markdown(self) -> None:
        """Output should be valid markdown."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Test",
                source_body="Test body.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        md = briefs[0].to_markdown()
        assert md.startswith("# ")
        assert "## Description" in md
        assert "## Market/Financial Relevance" in md

    def test_writes_public_outputs(self, tmp_path: Path) -> None:
        """Writes public markdown and CSV outputs."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Test",
                source_body="Test body.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        public_dir = tmp_path / "news"
        md_path, csv_path = reconstructor.write_public_outputs(briefs, public_dir)

        assert md_path.exists()
        assert csv_path.exists()
        assert md_path.read_text().startswith("# Synthetic News Briefs")

        # CSV should have header and data rows
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1

    def test_writes_private_provenance(self, tmp_path: Path) -> None:
        """Writes private provenance (hashes only)."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Acme Corp Announces Record Profits",
                source_body="Acme Corporation reported record profits.",
                source_company="Acme Corporation",
                source_ticker="ACM",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        private_dir = tmp_path / "private"
        prov_path = reconstructor.write_private_provenance(
            events, briefs, private_dir, "COMPANY_001"
        )

        assert prov_path.exists()
        content = prov_path.read_text()
        # Should not contain source company/ticker
        assert "Acme Corporation" not in content
        assert "ACM" not in content

    def test_invalid_event_class_skipped(self) -> None:
        """Events with invalid event classes are skipped."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="invalid_class",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Test",
                source_body="Test.",
            ),
            PrivateSourceEvent(
                event_id="evt_002",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Valid",
                source_body="Valid.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        assert len(briefs) == 1
        assert briefs[0].event_class == "demand_shift"
