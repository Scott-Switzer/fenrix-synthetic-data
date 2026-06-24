"""Tests for numeric transformation module."""

from __future__ import annotations

from pathlib import Path

from fenrix_synthetic.anonymization.numeric_transform import (
    FinancialFact,
    NumericTransformer,
    _round_by_scale,
    _round_ratio,
)

# ── Rounding helpers ───────────────────────────────────────────────────


def test_round_by_scale_billions() -> None:
    assert _round_by_scale(1_500_000_000) == 1_500_000_000  # nearest $100M
    assert _round_by_scale(1_550_000_000) == 1_600_000_000


def test_round_by_scale_millions() -> None:
    assert _round_by_scale(150_000_000) == 150_000_000  # nearest $10M
    assert _round_by_scale(155_000_000) == 160_000_000


def test_round_by_scale_small() -> None:
    assert _round_by_scale(1_500) == 2_000  # nearest $1K
    assert _round_by_scale(500) == 500


def test_round_ratio() -> None:
    assert _round_ratio(15.7) == 16.0  # whole number
    assert _round_ratio(5.67) == 5.7  # 1 decimal
    assert _round_ratio(0.45) == 0.45  # 2 decimals


# ── Transformer determinism ────────────────────────────────────────────


def test_same_seed_same_output() -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("cogs", 4_200_000_000, 2024),
        FinancialFact("total_assets", 50_000_000_000, 2024),
    ]
    t1 = NumericTransformer(company_id="COMPANY_001", seed=42)
    t2 = NumericTransformer(company_id="COMPANY_001", seed=42)

    r1 = t1.transform(facts)
    r2 = t2.transform(facts)

    assert len(r1.metrics) == len(r2.metrics)
    for m1, m2 in zip(r1.metrics, r2.metrics, strict=False):
        assert m1.transformed_value == m2.transformed_value
    assert r1.scale_factor == r2.scale_factor


def test_different_seed_different_output() -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("total_assets", 50_000_000_000, 2024),
    ]
    t1 = NumericTransformer(company_id="COMPANY_001", seed=42)
    t2 = NumericTransformer(company_id="COMPANY_001", seed=99)

    r1 = t1.transform(facts)
    r2 = t2.transform(facts)

    # Scale factors should differ with high probability
    assert r1.scale_factor != r2.scale_factor


def test_different_company_different_output() -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
    ]
    t1 = NumericTransformer(company_id="COMPANY_001", seed=42)
    t2 = NumericTransformer(company_id="COMPANY_002", seed=42)

    r1 = t1.transform(facts)
    r2 = t2.transform(facts)

    assert r1.scale_factor != r2.scale_factor


# ── Transformation properties ──────────────────────────────────────────


def test_revenue_stays_positive() -> None:
    facts = [FinancialFact("revenue", 10_000_000_000, 2024)]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)
    rev_metric = next(m for m in result.metrics if m.metric_name == "revenue")
    assert rev_metric.transformed_value > 0


def test_assets_stay_positive() -> None:
    facts = [FinancialFact("total_assets", 50_000_000_000, 2024)]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)
    asset_metric = next(m for m in result.metrics if m.metric_name == "total_assets")
    assert asset_metric.transformed_value > 0


def test_exact_values_do_not_survive() -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("total_assets", 50_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)

    for m in result.metrics:
        if m.original_value != 0:
            assert m.transformed_value != m.original_value, (
                f"{m.metric_name} year {m.year}: exact match survived"
            )


def test_ratios_recomputed() -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("net_income", 1_000_000_000, 2024),
        FinancialFact("total_assets", 50_000_000_000, 2024),
        FinancialFact("total_equity", 20_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)

    ratio_names = {r.ratio_name for r in result.ratios}
    assert "net_margin" in ratio_names
    assert "roa" in ratio_names
    assert "roe" in ratio_names


def test_rounding_by_scale() -> None:
    facts = [FinancialFact("revenue", 10_234_567_890, 2024)]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)
    rev = next(m for m in result.metrics if m.metric_name == "revenue")
    # Large values should be rounded to nearest $100M
    assert rev.transformed_value % 100_000_000 == 0


def test_negative_net_income_handled() -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("net_income", -500_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)
    net_inc = next(m for m in result.metrics if m.metric_name == "net_income")
    # Should preserve sign but be transformed
    assert net_inc.transformed_value != -500_000_000


def test_empty_facts_warning() -> None:
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform([])
    assert result.passes_sanity is True
    assert any("No facts provided" in w for w in result.warnings)


# ── Output writers ─────────────────────────────────────────────────────


def test_write_public_outputs(tmp_path: Path) -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("net_income", 1_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)
    written = t.write_public_outputs(result, tmp_path)

    assert len(written) == 3
    assert any("transformed_metrics.csv" in w for w in written)
    assert any("ratio_summary.csv" in w for w in written)
    assert any("summary.md" in w for w in written)

    # Check CSV content
    csv_path = next(Path(w) for w in written if w.endswith("transformed_metrics.csv"))
    content = csv_path.read_text()
    assert "metric,year,transformed_value" in content


def test_write_private_audit(tmp_path: Path) -> None:
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)
    audit_path = t.write_private_audit(result, tmp_path)
    assert Path(audit_path).exists()
    assert "numeric_transform_audit.json" in audit_path
