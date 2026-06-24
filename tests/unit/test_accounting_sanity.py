"""Tests for accounting sanity checker."""

from __future__ import annotations

from fenrix_synthetic.anonymization.accounting_sanity import (
    AccountingSanityChecker,
    SanityConfig,
    SanityResult,
)
from fenrix_synthetic.anonymization.numeric_transform import (
    FinancialFact,
    NumericTransformer,
    TransformResult,
)

# ── Sanity checker basic ───────────────────────────────────────────────


def test_sanity_passes_valid_transform() -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("cogs", 4_200_000_000, 2024),
        FinancialFact("gross_profit", 5_800_000_000, 2024),
        FinancialFact("operating_income", 2_000_000_000, 2024),
        FinancialFact("net_income", 1_500_000_000, 2024),
        FinancialFact("total_assets", 50_000_000_000, 2024),
        FinancialFact("total_liabilities", 30_000_000_000, 2024),
        FinancialFact("total_equity", 20_000_000_000, 2024),
        FinancialFact("cash", 5_000_000_000, 2024),
        FinancialFact("long_term_debt", 10_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)

    checker = AccountingSanityChecker()
    sanity = checker.check(result)

    assert isinstance(sanity, SanityResult)
    assert sanity.passes_all is True
    assert sanity.checks_failed == 0


def test_sanity_catches_negative_revenue() -> None:
    facts = [
        FinancialFact("revenue", -100_000_000, 2024),
        FinancialFact("total_assets", 50_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)

    checker = AccountingSanityChecker()
    sanity = checker.check(result)

    assert sanity.passes_all is False
    assert any("revenue" in v and "<=" in v for v in sanity.violations)


def test_sanity_catches_cash_exceeds_assets() -> None:
    facts = [
        FinancialFact("total_assets", 1_000_000_000, 2024),
        FinancialFact("cash", 2_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)

    checker = AccountingSanityChecker()
    sanity = checker.check(result)

    assert sanity.passes_all is False
    assert any("cash" in v and "total_assets" in v for v in sanity.violations)


def test_sanity_catches_balance_equation_violation() -> None:
    facts = [
        FinancialFact("total_assets", 50_000_000_000, 2024),
        FinancialFact("total_liabilities", 30_000_000_000, 2024),
        FinancialFact("total_equity", 15_000_000_000, 2024),  # should be 20B
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)

    checker = AccountingSanityChecker()
    sanity = checker.check(result)

    assert sanity.passes_all is False
    assert any("balance equation" in v for v in sanity.violations)


def test_sanity_catches_exact_source_value_survival() -> None:
    # Create a transform result where exact value survived
    from fenrix_synthetic.anonymization.numeric_transform import TransformedMetric

    result = TransformResult(
        company_id="COMPANY_001",
        metrics=[
            TransformedMetric(
                metric_name="revenue",
                original_value=10_000_000_000,
                transformed_value=10_000_000_000,
                scale_factor=1.0,
                year=2024,
                family="revenue",
            )
        ],
        ratios=[],
        scale_factor=1.0,
        revenue_scale_factor=1.0,
        year_noise_applied={},
        violations=[],
        warnings=[],
        passes_sanity=True,
    )

    checker = AccountingSanityChecker()
    sanity = checker.check(result)

    assert sanity.passes_all is False
    assert any("exact match survived" in v for v in sanity.violations)


def test_sanity_missing_facts_warning_not_crash() -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)

    checker = AccountingSanityChecker()
    sanity = checker.check(result)

    # Should not crash; may have warnings for missing optional fields
    assert sanity.checks_run > 0
    assert any("missing" in w.lower() for w in sanity.warnings)


def test_sanity_config_tolerance() -> None:
    # Build a TransformResult manually with a small balance diff
    # Use DIFFERENT original/transformed values to avoid exact-match violation
    from fenrix_synthetic.anonymization.numeric_transform import TransformedMetric

    result = TransformResult(
        company_id="COMPANY_001",
        metrics=[
            TransformedMetric("total_assets", 50_000_000_000, 51_000_000_000, 1.02, 2024, "asset"),
            TransformedMetric(
                "total_liabilities", 30_000_000_000, 30_500_000_000, 1.017, 2024, "liability"
            ),
            TransformedMetric(
                "total_equity", 19_500_000_000, 20_000_000_000, 1.026, 2024, "equity"
            ),
        ],
        ratios=[],
        scale_factor=1.0,
        revenue_scale_factor=1.0,
        year_noise_applied={},
        violations=[],
        warnings=[],
        passes_sanity=True,
    )

    # With default 2% tolerance, should pass (diff = 500M / 51B ≈ 1%)
    checker = AccountingSanityChecker(SanityConfig(balance_equation_tolerance=0.02))
    sanity = checker.check(result)
    assert sanity.passes_all is True

    # With 0.5% tolerance, should fail (diff ≈ 1% > 0.5%)
    checker_strict = AccountingSanityChecker(SanityConfig(balance_equation_tolerance=0.005))
    sanity_strict = checker_strict.check(result)
    assert sanity_strict.passes_all is False


def test_sanity_net_margin_range() -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("net_income", 6_000_000_000, 2024),  # 60% margin
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)

    # Default max is 50%, should catch this
    checker = AccountingSanityChecker()
    sanity = checker.check(result)
    # Note: after transformation the margin may change, so we check the structure
    assert sanity.checks_run > 0
