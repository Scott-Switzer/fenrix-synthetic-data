"""Tests for exact-number attack module."""

from __future__ import annotations

import pytest

from fenrix_synthetic.anonymization.numeric_transform import (
    FinancialFact,
    NumericTransformer,
)
from fenrix_synthetic.qa.exact_number_attack import (
    AttackConfig,
    AttackReport,
    ExactNumberAttack,
    NearMatch,
)

# ── Basic attack ───────────────────────────────────────────────────────


def test_no_exact_matches_when_transformed() -> None:
    source = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("net_income", 1_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(source)

    # Build public facts from transformed values
    public = [FinancialFact(m.metric_name, m.transformed_value, m.year) for m in result.metrics]

    attack = ExactNumberAttack()
    report = attack.run(source, public)

    assert report.exact_value_matches == 0
    assert report.passed is True
    assert report.source_value_count == 2
    assert report.public_value_count == 2


def test_catches_exact_match() -> None:
    source = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("net_income", 1_000_000_000, 2024),
    ]
    # Public has exact copy of revenue
    public = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("net_income", 999_999_999, 2024),
    ]

    attack = ExactNumberAttack()
    report = attack.run(source, public)

    assert report.exact_value_matches == 1
    assert report.passed is False
    assert any("EXACT VALUE MATCH" in v for v in report.violations)


def test_catches_near_match() -> None:
    source = [
        FinancialFact("revenue", 10_000_000_000, 2024),
    ]
    # Public value is 0.05% different (below 0.1% tolerance)
    public = [
        FinancialFact("revenue", 10_005_000_000, 2024),
    ]

    attack = ExactNumberAttack(AttackConfig(near_match_relative_tolerance=0.001))
    report = attack.run(source, public)

    # 0.05% diff < 0.1% tolerance, so it should be flagged as near match
    assert len(report.near_matches) == 1
    assert report.near_matches[0].relative_diff < 0.001


def test_near_match_threshold_blocks() -> None:
    source = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("net_income", 1_000_000_000, 2024),
        FinancialFact("total_assets", 50_000_000_000, 2024),
        FinancialFact("total_equity", 20_000_000_000, 2024),
    ]
    # All public values slightly different but within tolerance
    public = [
        FinancialFact("revenue", 10_005_000_000, 2024),
        FinancialFact("net_income", 1_001_000_000, 2024),
        FinancialFact("total_assets", 50_002_000_000, 2024),
        FinancialFact("total_equity", 20_001_000_000, 2024),
    ]

    attack = ExactNumberAttack(AttackConfig(near_match_warning_threshold=3))
    report = attack.run(source, public)

    assert len(report.near_matches) >= 3
    assert report.passed is False
    assert any("NEAR-MATCH WARNING" in v for v in report.violations)


def test_missing_public_metric_not_counted() -> None:
    source = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("secret_metric", 5_000_000_000, 2024),
    ]
    public = [
        FinancialFact("revenue", 9_000_000_000, 2024),
    ]

    attack = ExactNumberAttack()
    report = attack.run(source, public)

    assert report.source_value_count == 2
    assert report.public_value_count == 1
    assert report.exact_value_matches == 0


def test_run_from_transform_result() -> None:
    source = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("net_income", 1_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(source)

    attack = ExactNumberAttack()
    report = attack.run_from_transform_result(source, result)

    assert report.exact_value_matches == 0
    assert report.passed is True


def test_run_from_transform_result_type_error() -> None:
    attack = ExactNumberAttack()
    with pytest.raises(TypeError):
        attack.run_from_transform_result([], "not a TransformResult")


def test_attack_report_to_dict() -> None:
    report = AttackReport(
        exact_value_matches=0,
        exact_ratio_matches=0,
        near_matches=[NearMatch("revenue", 2024, 10_000_000_000, 10_005_000_000, 0.0005)],
        source_value_count=5,
        public_value_count=5,
        passed=True,
    )
    d = report.to_dict()
    assert d["exact_value_matches"] == 0
    assert d["passed"] is True
    assert len(d["near_matches"]) == 1
    assert d["near_matches"][0]["metric_name"] == "revenue"


def test_zero_allowed_exact_matches() -> None:
    source = [
        FinancialFact("revenue", 10_000_000_000, 2024),
    ]
    public = [
        FinancialFact("revenue", 10_000_000_000, 2024),
    ]

    attack = ExactNumberAttack(AttackConfig(exact_value_matches_allowed=0))
    report = attack.run(source, public)

    assert report.exact_value_matches == 1
    assert report.passed is False


def test_one_allowed_exact_match() -> None:
    source = [
        FinancialFact("revenue", 10_000_000_000, 2024),
    ]
    public = [
        FinancialFact("revenue", 10_000_000_000, 2024),
    ]

    attack = ExactNumberAttack(AttackConfig(exact_value_matches_allowed=1))
    report = attack.run(source, public)

    assert report.exact_value_matches == 1
    assert report.passed is True
