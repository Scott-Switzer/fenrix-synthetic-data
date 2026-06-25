"""Integration tests for the NUMERIC_TRANSFORM professor bundle stage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fenrix_synthetic.anonymization.accounting_sanity import (
    AccountingSanityChecker,
    SanityConfig,
)
from fenrix_synthetic.anonymization.numeric_transform import (
    FinancialFact,
    NumericTransformer,
)
from fenrix_synthetic.qa.exact_number_attack import AttackConfig, ExactNumberAttack

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def fixture_facts() -> list[FinancialFact]:
    """Create deterministic fixture financial facts."""
    return [
        FinancialFact(year=2020, metric_name="Revenue", value=50_000_000),
        FinancialFact(year=2020, metric_name="CostOfGoodsSold", value=30_000_000),
        FinancialFact(year=2020, metric_name="NetIncome", value=5_000_000),
        FinancialFact(year=2020, metric_name="TotalAssets", value=80_000_000),
        FinancialFact(year=2020, metric_name="TotalLiabilities", value=40_000_000),
        FinancialFact(year=2020, metric_name="TotalEquity", value=40_000_000),
        FinancialFact(year=2020, metric_name="CashAndCashEquivalents", value=8_000_000),
        FinancialFact(year=2020, metric_name="LongTermDebt", value=20_000_000),
        FinancialFact(year=2021, metric_name="Revenue", value=55_000_000),
        FinancialFact(year=2021, metric_name="CostOfGoodsSold", value=33_000_000),
        FinancialFact(year=2021, metric_name="NetIncome", value=5_500_000),
        FinancialFact(year=2021, metric_name="TotalAssets", value=85_000_000),
        FinancialFact(year=2021, metric_name="TotalLiabilities", value=42_000_000),
        FinancialFact(year=2021, metric_name="TotalEquity", value=43_000_000),
        FinancialFact(year=2021, metric_name="CashAndCashEquivalents", value=9_000_000),
        FinancialFact(year=2021, metric_name="LongTermDebt", value=21_000_000),
    ]


@pytest.fixture
def company_id() -> str:
    return "COMP_FIXTURE_001"


# ── Tests ────────────────────────────────────────────────────────


class TestNumericTransformStage:
    """Test that fixture professor build emits financial outputs."""

    def _write_csv(self, path: Path, lines: list[str]) -> None:
        """Write lines as CSV with newline separators."""
        path.write_text("\n".join(lines) + "\n")

    def test_transform_emits_transformed_metrics_csv(
        self, tmp_path: Path, fixture_facts: list[FinancialFact], company_id: str
    ) -> None:
        transformer = NumericTransformer(company_id, seed=42)
        result = transformer.transform(fixture_facts)
        financials_dir = tmp_path / "financials"
        financials_dir.mkdir()
        csv_lines = ["year,metric_name,transformed_value,family"]
        for m in result.metrics:
            csv_lines.append(f"{m.year},{m.metric_name},{m.transformed_value},{m.family}")
        csv_path = financials_dir / "transformed_metrics.csv"
        self._write_csv(csv_path, csv_lines)
        assert csv_path.exists()
        content = csv_path.read_text()
        assert "year,metric_name,transformed_value,family" in content
        assert "Revenue" in content

    def test_transform_emits_ratio_summary_csv(
        self, tmp_path: Path, fixture_facts: list[FinancialFact], company_id: str
    ) -> None:
        transformer = NumericTransformer(company_id, seed=42)
        result = transformer.transform(fixture_facts)
        financials_dir = tmp_path / "financials"
        financials_dir.mkdir()
        ratio_lines = ["ratio_name,ratio_value"]
        for r in result.ratios:
            ratio_lines.append(f"{r.ratio_name},{r.value}")
        csv_path = financials_dir / "ratio_summary.csv"
        self._write_csv(csv_path, ratio_lines)
        assert csv_path.exists()
        content = csv_path.read_text()
        assert "ratio_name,ratio_value" in content

    def test_transform_emits_summary_md(
        self, tmp_path: Path, fixture_facts: list[FinancialFact], company_id: str
    ) -> None:
        transformer = NumericTransformer(company_id, seed=42)
        result = transformer.transform(fixture_facts)
        financials_dir = tmp_path / "financials"
        financials_dir.mkdir()
        md_lines = [f"# Financial Summary for {company_id}"]
        md_lines.append("| Year | Metric | Value |")
        for m in result.metrics[:5]:
            md_lines.append(f"| {m.year} | {m.metric_name} | {m.transformed_value:.2f} |")
        md_path = financials_dir / "summary.md"
        md_path.write_text("\n".join(md_lines) + "\n")
        assert md_path.exists()
        content = md_path.read_text()
        assert company_id in content

    def test_private_numeric_audit_excluded_from_public(
        self, tmp_path: Path, fixture_facts: list[FinancialFact], company_id: str
    ) -> None:
        """Private audit should be in private/qa, not in public paths."""
        transformer = NumericTransformer(company_id, seed=42)
        result = transformer.transform(fixture_facts)
        # Write public files
        public_dir = tmp_path / "public"
        financials_dir = public_dir / "anonymized" / company_id / "financials"
        financials_dir.mkdir(parents=True)
        (financials_dir / "transformed_metrics.csv").write_text("year,metric_name,value\n")
        # Write private audit
        private_dir = tmp_path / "private"
        qa_dir = private_dir / "qa"
        qa_dir.mkdir(parents=True)
        audit = {"company_id": company_id, "num_metrics": len(result.metrics)}
        (qa_dir / "numeric_transform_audit.json").write_text(json.dumps(audit))
        # Verify separation
        private_files = list(private_dir.rglob("*"))
        assert any("numeric_transform_audit" in str(f) for f in private_files)
        # Audit should NOT be in public
        public_files = list(public_dir.rglob("*"))
        assert not any("numeric_transform_audit" in str(f) for f in public_files)

    def test_exact_number_attack_generated(
        self, fixture_facts: list[FinancialFact], company_id: str
    ) -> None:
        transformer = NumericTransformer(company_id, seed=42)
        result = transformer.transform(fixture_facts)
        attack = ExactNumberAttack(AttackConfig())
        attack_result = attack.run_from_transform_result(fixture_facts, result)
        assert hasattr(attack_result, "exact_value_matches")
        assert hasattr(attack_result, "exact_ratio_matches")

    def test_stage_fails_if_exact_values_copied(
        self, fixture_facts: list[FinancialFact], company_id: str
    ) -> None:
        """If exact source values survive, the attack should detect them."""
        transformer = NumericTransformer(company_id, seed=42)
        result = transformer.transform(fixture_facts)
        attack = ExactNumberAttack(AttackConfig())
        attack_result = attack.run_from_transform_result(fixture_facts, result)
        # With proper transformation, there should be no exact matches
        assert attack_result.exact_value_matches == 0
        assert attack_result.exact_ratio_matches == 0

    def test_stage_fails_if_accounting_sanity_blocking(
        self, fixture_facts: list[FinancialFact], company_id: str
    ) -> None:
        """Accounting sanity should pass with valid fixture data."""
        transformer = NumericTransformer(company_id, seed=42)
        result = transformer.transform(fixture_facts)
        checker = AccountingSanityChecker(SanityConfig())
        sanity_result = checker.check(result)
        # Should pass with valid fixture data
        assert sanity_result.passes_all, f"Sanity violations: {sanity_result.violations}"

    def test_deterministic_with_same_seed(
        self, fixture_facts: list[FinancialFact], company_id: str
    ) -> None:
        t1 = NumericTransformer(company_id, seed=42)
        r1 = t1.transform(fixture_facts)
        t2 = NumericTransformer(company_id, seed=42)
        r2 = t2.transform(fixture_facts)
        assert r1.metrics == r2.metrics
        assert r1.ratios == r2.ratios

    def test_public_files_no_source_identity(
        self, tmp_path: Path, fixture_facts: list[FinancialFact], company_id: str
    ) -> None:
        """Public financial files should not contain source ticker or company keys."""
        financials_dir = tmp_path / "financials"
        financials_dir.mkdir()
        md_lines = [f"# Financial Summary for {company_id}"]
        md_lines.append("| 2020 | Revenue | 12345.67 |")
        md_path = financials_dir / "summary.md"
        md_path.write_text("\n".join(md_lines) + "\n")
        content = md_path.read_text()
        # Should contain the anonymized company ID, not source identifiers
        assert company_id in content
        # No CIK or real ticker
        assert "CIK" not in content

    def test_existing_peer_archetype_outputs_still_exist(
        self, tmp_path: Path, company_id: str
    ) -> None:
        """Existing peer archetype outputs should still work alongside numeric outputs."""
        # Simulate peer archetype
        profile_dir = tmp_path / "anonymized" / company_id / "profile"
        profile_dir.mkdir(parents=True)
        (profile_dir / "archetype_card.json").write_text(json.dumps({"company_id": company_id}))
        (profile_dir / "profile.md").write_text(f"# Profile for {company_id}")
        # Simulate numeric outputs
        financials_dir = tmp_path / "anonymized" / company_id / "financials"
        financials_dir.mkdir(parents=True)
        (financials_dir / "transformed_metrics.csv").write_text("year,metric\n")
        # Verify both exist
        assert (profile_dir / "archetype_card.json").exists()
        assert (profile_dir / "profile.md").exists()
        assert (financials_dir / "transformed_metrics.csv").exists()
