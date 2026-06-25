"""Unit tests for news reconstruction attack."""

from __future__ import annotations

from pathlib import Path

from fenrix_synthetic.qa.news_reconstruction_attack import (
    NewsAttackFinding,
    NewsAttackResult,
    NewsReconstructionAttack,
    check_public_news_directory,
)


class TestNewsReconstructionAttack:
    """Test the news reconstruction attack QA module."""

    def test_attack_catches_copied_headline(self, tmp_path: Path) -> None:
        """Attack catches copied headline in public output."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "brief.md").write_text(
            "Acme Corp Announces Record Profits in Q4 2024"
        )

        attack = NewsReconstructionAttack(
            source_headlines=["Acme Corp Announces Record Profits in Q4 2024"],
        )
        result = attack.run(news_dir, "COMPANY_001")

        assert not result.passed
        assert any(f.check_name == "copied_headline" for f in result.findings)

    def test_attack_catches_url(self, tmp_path: Path) -> None:
        """Attack catches URL in public output."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "brief.md").write_text(
            "For more details, visit https://example.com/news/1234"
        )

        attack = NewsReconstructionAttack()
        result = attack.run(news_dir, "COMPANY_001")

        assert not result.passed
        assert any(f.check_name == "url_found" for f in result.findings)

    def test_attack_catches_source_company(self, tmp_path: Path) -> None:
        """Attack catches source company name in public output."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "brief.md").write_text(
            "Acme Corporation reported strong earnings."
        )

        attack = NewsReconstructionAttack(
            source_company_names=["Acme Corporation"],
        )
        result = attack.run(news_dir, "COMPANY_001")

        assert not result.passed
        assert any(
            f.check_name == "source_company_leak" for f in result.findings
        )

    def test_attack_catches_source_ticker(self, tmp_path: Path) -> None:
        """Attack catches source ticker in public output."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "brief.md").write_text("The stock ACM fell 5%.")

        attack = NewsReconstructionAttack(
            source_tickers=["ACM"],
        )
        result = attack.run(news_dir, "COMPANY_001")

        assert not result.passed
        assert any(
            f.check_name == "source_ticker_leak" for f in result.findings
        )

    def test_attack_passes_sanitized_content(self, tmp_path: Path) -> None:
        """Attack passes sanitized synthetic news."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "brief.md").write_text(
            "# Synthetic News Brief\n\n"
            "**Event Class:** demand_shift\n"
            "**Relative Period:** Year -2, Q1\n"
            "The company experienced a demand shift. No real data leaked.\n"
        )

        attack = NewsReconstructionAttack(
            source_company_names=["Acme Corporation"],
            source_tickers=["ACM"],
            source_headlines=["Acme Corp Announces Record Profits"],
        )
        result = attack.run(news_dir, "COMPANY_001")

        assert result.passed
        assert result.blocking_count == 0

    def test_attack_catches_exact_date(self, tmp_path: Path) -> None:
        """Attack catches exact dates in output."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "brief.md").write_text("Event on 2024-03-15 was significant.")

        attack = NewsReconstructionAttack()
        result = attack.run(news_dir, "COMPANY_001")

        assert not result.passed
        assert any(f.check_name == "exact_date" for f in result.findings)

    def test_attack_catches_executive_quote(self, tmp_path: Path) -> None:
        """Attack catches executive quote patterns."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "brief.md").write_text(
            "said CEO John Smith during the earnings call."
        )

        attack = NewsReconstructionAttack()
        result = attack.run(news_dir, "COMPANY_001")

        assert not result.passed
        assert any(f.check_name == "executive_quote" for f in result.findings)

    def test_attack_catches_counterparty(self, tmp_path: Path) -> None:
        """Attack catches named counterparty."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "brief.md").write_text(
            "The acquisition of Rival Corporation was completed."
        )

        attack = NewsReconstructionAttack(
            source_counterparties=["Rival Corporation"],
        )
        result = attack.run(news_dir, "COMPANY_001")

        assert not result.passed
        assert any(f.check_name == "counterparty_leak" for f in result.findings)

    def test_directory_missing(self, tmp_path: Path) -> None:
        """Missing directory returns failed result."""
        news_dir = tmp_path / "nonexistent"

        attack = NewsReconstructionAttack()
        result = attack.run(news_dir, "COMPANY_001")

        assert not result.passed
        assert result.files_checked == 0

    def test_empty_directory_passes(self, tmp_path: Path) -> None:
        """Empty directory passes."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()

        attack = NewsReconstructionAttack()
        result = attack.run(news_dir, "COMPANY_001")

        assert result.passed
        assert result.files_checked == 0

    def test_convenience_function(self, tmp_path: Path) -> None:
        """Convenience function check_public_news_directory works."""
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        (news_dir / "clean.md").write_text("# Clean synthetic brief\n")

        result = check_public_news_directory(
            news_dir,
            source_company_names=["Acme Corp"],
        )
        assert result["passed"] is True
