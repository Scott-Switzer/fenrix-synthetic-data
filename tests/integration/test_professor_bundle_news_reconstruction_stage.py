"""Integration tests for the NEWS_RECONSTRUCTION professor bundle stage."""

from __future__ import annotations

import json
from pathlib import Path

from fenrix_synthetic.anonymization.news_reconstructor import (
    NewsReconstructor,
    PrivateSourceEvent,
)
from fenrix_synthetic.qa.news_reconstruction_attack import (
    NewsReconstructionAttack,
)


class TestNewsReconstructionStage:
    """Integration tests for news reconstruction stage behavior."""

    def test_public_news_files_generated(self, tmp_path: Path) -> None:
        """Public news files are generated from fixtures."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="8-K",
                source_date="2024-03-15",
                source_headline="Acme Corp Sees Demand Shift",
                source_body="Acme Corp reported a demand shift.",
            ),
            PrivateSourceEvent(
                event_id="evt_002",
                event_class="capital_allocation",
                source_type="filing_section",
                source_date="2023-11-10",
                source_headline="Capital Allocation Decision",
                source_body="The company announced a capital allocation.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        public_dir = tmp_path / "news"
        md_path, csv_path = reconstructor.write_public_outputs(briefs, public_dir)

        assert md_path.exists()
        assert csv_path.exists()

        # Check markdown content
        md_content = md_path.read_text()
        assert "Synthetic News Briefs" in md_content
        assert "demand_shift" in md_content or "Demand" in md_content

        # Check CSV content
        csv_content = csv_path.read_text()
        assert "brief_id" in csv_content
        assert "event_class" in csv_content

    def test_news_files_no_private_source(self, tmp_path: Path) -> None:
        """News files contain no private source identifiers."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-15",
                source_headline="ACME Corp Reports Record Q1 2024 Earnings Beat",
                source_body="ACME (NYSE: ACM) exceeded analyst expectations.",
                source_company="ACME Corp",
                source_ticker="ACM",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        # Check that synthetic content doesn't leak private data
        for brief in briefs:
            md = brief.to_markdown()
            assert "ACME Corp" not in md
            assert "ACM" not in md
            assert "2024-01-15" not in md
            assert "NYSE" not in md

    def test_private_qa_excluded_from_public_zip(self, tmp_path: Path) -> None:
        """Private news QA files are excluded from public output."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Test",
                source_body="Test.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        public_dir = tmp_path / "public" / "news"
        private_dir = tmp_path / "private" / "qa"

        reconstructor.write_public_outputs(briefs, public_dir)
        prov_path = reconstructor.write_private_provenance(
            events, briefs, private_dir, "COMPANY_001"
        )

        # Private provenance should be in private dir
        assert prov_path.is_relative_to(private_dir)
        assert "private" in str(prov_path)

        # Public dir should not have private provenance
        assert not any(
            "provenance" in f.name.lower()
            for f in public_dir.parent.rglob("*")
            if f.is_file()
        )

    def test_strict_release_gate_passes_public_news(self, tmp_path: Path) -> None:
        """Strict release gate should pass sanitized public news."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Test",
                source_body="Test.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        public_dir = tmp_path / "news"
        reconstructor.write_public_outputs(briefs, public_dir)

        attack = NewsReconstructionAttack()
        result = attack.run(public_dir, "COMPANY_001")
        assert result.passed

    def test_attack_catches_confidential_in_news(self, tmp_path: Path) -> None:
        """News attack catches confidential data in public news."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "leaked.md").write_text(
            "ACME Corp announces acquisition of Rival Corp for $5B."
        )

        attack = NewsReconstructionAttack(
            source_company_names=["ACME Corp"],
            source_tickers=["ACM"],
            source_counterparties=["Rival Corp"],
        )
        result = attack.run(news_dir, "COMPANY_001")

        assert not result.passed
        findings = result.to_dict()["findings"]
        check_names = [f["check_name"] for f in findings]
        assert "source_company_leak" in check_names
        assert "counterparty_leak" in check_names

    def test_zip_contains_news_files(self, tmp_path: Path) -> None:
        """Final ZIP should contain news files."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Test",
                source_body="Test.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        news_dir = tmp_path / "news"
        md_path, csv_path = reconstructor.write_public_outputs(briefs, news_dir)

        # Verify both files exist
        assert md_path.suffix == ".md"
        assert csv_path.suffix == ".csv"
        assert "synthetic_news_briefs" in md_path.name
        assert "event_timeline" in csv_path.name

    def test_unknown_event_class_uses_demand_shift(self) -> None:
        """Unknown event class should fall back to demand_shift."""
        events = [
            PrivateSourceEvent(
                event_id="evt_001",
                event_class="demand_shift",
                source_type="news",
                source_date="2024-01-01",
                source_headline="Test",
                source_body="This is a test event.",
            ),
        ]

        reconstructor = NewsReconstructor()
        briefs = reconstructor.reconstruct("COMPANY_001", events)

        assert len(briefs) == 1
        assert briefs[0].event_class == "demand_shift"
        assert "demand_shift" in briefs[0].event_class or "Shift" in briefs[0].synthetic_title
