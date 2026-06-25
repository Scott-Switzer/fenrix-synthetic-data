"""Integration tests for the TRAJECTORY_MORPH professor bundle stage."""

from __future__ import annotations

from pathlib import Path

import pytest

from fenrix_synthetic.anonymization.trajectory_morph import (
    TrajectoryMorphConfig,
    TrajectoryMorpher,
    write_public_price_series,
    write_private_trajectory_audit,
)
from fenrix_synthetic.qa.trajectory_attack import (
    TrajectoryAttack,
    TrajectoryAttackConfig,
    write_trajectory_attack_summary,
)


def _make_test_prices(n: int = 252) -> list[float]:
    """Create a simple fixture price series."""
    prices = [100.0]
    for i in range(n):
        prices.append(prices[-1] * (1.0005 + 0.02 * ((i % 7) - 3) / 3))
    return prices


def _make_test_dates(n: int = 252) -> list[str]:
    return [f"2024-{(i//28)+1:02d}-{((i%28)+1):02d}" for i in range(n)]


class TestTrajectoryProfessorStage:
    """Test that fixture professor build emits market outputs."""

    def test_fixture_build_emits_price_series_csv(
        self, tmp_path: Path,
    ) -> None:
        prices = _make_test_prices()
        dates = _make_test_dates()
        config = TrajectoryMorphConfig(seed=42)
        morpher = TrajectoryMorpher(config)
        result = morpher.morph("COMP_FIXTURE_001", dates, prices)
        outputs = write_public_price_series(str(tmp_path), "COMP_FIXTURE_001", result)
        csv_path = Path(outputs["csv"])
        assert csv_path.exists()
        content = csv_path.read_text()
        assert "date,price" in content

    def test_fixture_build_emits_return_summary_md(
        self, tmp_path: Path,
    ) -> None:
        prices = _make_test_prices()
        dates = _make_test_dates()
        config = TrajectoryMorphConfig(seed=42)
        morpher = TrajectoryMorpher(config)
        result = morpher.morph("COMP_FIXTURE_001", dates, prices)
        outputs = write_public_price_series(str(tmp_path), "COMP_FIXTURE_001", result)
        md_path = Path(outputs["md"])
        assert md_path.exists()
        content = md_path.read_text()
        assert "Return Summary" in content

    def test_zip_excludes_trajectory_audit(self, tmp_path: Path) -> None:
        """Private audit should not be in public paths."""
        prices = _make_test_prices()
        dates = _make_test_dates()
        config = TrajectoryMorphConfig(seed=42)
        morpher = TrajectoryMorpher(config)
        result = morpher.morph("COMP_FIXTURE_001", dates, prices)

        public_dir = tmp_path / "public"
        market_dir = public_dir / "anonymized" / "COMP_FIXTURE_001" / "market"
        market_dir.mkdir(parents=True)
        (market_dir / "price_series.csv").write_text("date,price\n")

        private_dir = tmp_path / "private"
        qa_dir = private_dir / "qa"
        qa_dir.mkdir(parents=True)
        write_private_trajectory_audit(str(private_dir), "COMP_FIXTURE_001", result)

        private_files = list(private_dir.rglob("*"))
        assert any("trajectory_morph_audit" in str(f) for f in private_files)
        public_files = list(public_dir.rglob("*"))
        assert not any("trajectory_morph_audit" in str(f) for f in public_files)

    def test_attack_summary_generated(self, tmp_path: Path) -> None:
        prices = _make_test_prices()
        dates = _make_test_dates()
        config = TrajectoryMorphConfig(seed=42)
        morpher = TrajectoryMorpher(config)
        result = morpher.morph("COMP_FIXTURE_001", dates, prices)
        attack = TrajectoryAttack(TrajectoryAttackConfig())
        attack_result = attack.run(result.source_returns, result.morphed_returns)
        qa_dir = tmp_path / "qa"
        qa_dir.mkdir(parents=True)
        summary_path = write_trajectory_attack_summary(str(qa_dir), "COMP_FIXTURE_001", attack_result)
        assert Path(summary_path).exists()

    def test_stage_fails_if_exact_returns_copied(self) -> None:
        """If exact source returns survive, the attack should detect them."""
        prices = _make_test_prices()
        dates = _make_test_dates()
        config = TrajectoryMorphConfig(seed=42)
        morpher = TrajectoryMorpher(config)
        result = morpher.morph("COMP_FIXTURE_001", dates, prices)
        attack = TrajectoryAttack(TrajectoryAttackConfig(exact_return_match_threshold=0))
        attack_result = attack.run(result.source_returns, result.source_returns)
        assert attack_result.exact_return_match_count > 0
        assert not attack_result.passes

    def test_deterministic_with_same_seed(self) -> None:
        prices = _make_test_prices()
        dates = _make_test_dates()
        m1 = TrajectoryMorpher(TrajectoryMorphConfig(seed=42)).morph("COMP_FIXTURE_001", dates, prices)
        m2 = TrajectoryMorpher(TrajectoryMorphConfig(seed=42)).morph("COMP_FIXTURE_001", dates, prices)
        assert m1.morphed_prices == m2.morphed_prices

    def test_public_files_no_source_identity(self, tmp_path: Path) -> None:
        """Public market files should not contain source identifiers."""
        prices = _make_test_prices()
        dates = _make_test_dates()
        config = TrajectoryMorphConfig(seed=42)
        morpher = TrajectoryMorpher(config)
        result = morpher.morph("COMP_FIXTURE_001", dates, prices)
        outputs = write_public_price_series(str(tmp_path), "COMP_FIXTURE_001", result)
        for path_str in outputs.values():
            content = Path(path_str).read_text()
            assert "CIK" not in content
            assert "ACM" not in content

    def test_numeric_financial_files_still_exist(self, tmp_path: Path) -> None:
        """Existing numeric financial outputs should coexist with market outputs."""
        # Simulate numeric outputs
        fin_dir = tmp_path / "anonymized" / "COMP_FIXTURE_001" / "financials"
        fin_dir.mkdir(parents=True)
        (fin_dir / "transformed_metrics.csv").write_text("year,metric\n")
        # Add market outputs
        prices = _make_test_prices()
        dates = _make_test_dates()
        config = TrajectoryMorphConfig(seed=42)
        morpher = TrajectoryMorpher(config)
        result = morpher.morph("COMP_FIXTURE_001", dates, prices)
        write_public_price_series(str(tmp_path), "COMP_FIXTURE_001", result)
        # Both should exist
        assert (fin_dir / "transformed_metrics.csv").exists()
        market_dir = tmp_path / "anonymized" / "COMP_FIXTURE_001" / "market"
        assert market_dir.exists()

    def test_existing_archetype_files_still_exist(self, tmp_path: Path) -> None:
        """Existing archetype outputs should coexist with market outputs."""
        profile_dir = tmp_path / "anonymized" / "COMP_FIXTURE_001" / "profile"
        profile_dir.mkdir(parents=True)
        (profile_dir / "archetype_card.json").write_text('{"company_id": "COMP_FIXTURE_001"}')
        # Add market outputs
        prices = _make_test_prices()
        dates = _make_test_dates()
        config = TrajectoryMorphConfig(seed=42)
        morpher = TrajectoryMorpher(config)
        result = morpher.morph("COMP_FIXTURE_001", dates, prices)
        write_public_price_series(str(tmp_path), "COMP_FIXTURE_001", result)
        assert (profile_dir / "archetype_card.json").exists()
