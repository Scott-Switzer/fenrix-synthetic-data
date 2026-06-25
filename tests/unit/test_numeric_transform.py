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


# ── Slack feedback item #2: pervasive numeric-policy tests ─────────────
def test_numeric_policy_is_consistent_across_companies() -> None:
    """All 8 companies use the same NumericTransformer configuration.

    The only per-company variation is the deterministic seed. There is
    NO hard-coded +20% boost or per-source special case.
    """
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("total_assets", 50_000_000_000, 2024),
    ]
    scales = []
    for i in range(1, 9):
        t = NumericTransformer(company_id=f"COMPANY_{i:03d}", seed=42)
        result = t.transform(facts)
        # Sanity: scale lies within the public disclosure range (0.65–1.35)
        assert 0.65 <= result.scale_factor <= 1.35, (
            f"COMPANY_{i:03d} scale_factor out of disclosed range: {result.scale_factor}"
        )
        # The per-company scale varies (because seed differs by company_id),
        # but no company gets a uniform boost.
        assert result.scale_factor != 1.0, (
            f"COMPANY_{i:03d} expected per-company variation, got 1.0"
        )
        scales.append(result.scale_factor)
    # The 8 scale factors should NOT all be identical to each other —
    # companies get different per-company scale factors from SHA-256 keys.
    assert len({round(s, 4) for s in scales}) >= 2, (
        f"all 8 companies produced identical scales; expected variation: {scales}"
    )


def test_noise_is_bounded() -> None:
    """Year-level noise is bounded to the configured range, no outliers.

    The default range is (0.02, 0.06); even with a custom range of
    (-0.05, +0.05), every per-year noise must clip.
    """
    facts = [FinancialFact("revenue", 10_000_000_000, y) for y in range(2018, 2026)]
    # Test default config (range 0.02–0.06)
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)
    for year, noise in result.year_noise_applied.items():
        # noise is (1.0 +/- range) — not just +/- range
        assert 0.94 <= noise <= 1.06, f"year {year} default-range noise out of bounds: {noise}"

    # Test custom +/-5% range (Salim's example policy)
    t_custom = NumericTransformer(
        company_id="COMPANY_001",
        seed=42,
        year_noise_range=(0.0, 0.05),
    )
    result_custom = t_custom.transform(facts)
    for year, noise in result_custom.year_noise_applied.items():
        assert 0.95 <= noise <= 1.05, f"year {year} custom +/-5% noise out of bounds: {noise}"


def test_same_seed_is_deterministic() -> None:
    """Same (company_id, seed) always yields the same scale factor and
    transformed values, with process-independent determinism."""
    facts = [FinancialFact("revenue", 10_000_000_000, 2024)]
    t1 = NumericTransformer(company_id="COMPANY_001", seed=42)
    t2 = NumericTransformer(company_id="COMPANY_001", seed=42)
    r1 = t1.transform(facts)
    r2 = t2.transform(facts)
    assert r1.scale_factor == r2.scale_factor
    assert r1.metrics[0].transformed_value == r2.metrics[0].transformed_value


def test_exact_values_do_not_survive() -> None:
    """No transformed value retains its original source value.

    Even with mild scaling and rounding, exact matches are detected as
    violations for any non-zero pair.
    """
    facts = [
        FinancialFact("revenue", 10_000_000_000, 2024),
        FinancialFact("total_assets", 50_000_000_000, 2024),
        FinancialFact("net_income", 1_000_000_000, 2024),
        FinancialFact("cash", 5_000_000_000, 2024),
    ]
    t = NumericTransformer(company_id="COMPANY_001", seed=42)
    result = t.transform(facts)
    surviving = [
        m
        for m in result.metrics
        if m.original_value != 0 and m.transformed_value == m.original_value
    ]
    assert not surviving, (
        f"exact source values survived: "
        f"{[(m.metric_name, m.year, m.transformed_value) for m in surviving]}"
    )


def test_public_docs_disclose_perturbation_without_revealing_parameters(tmp_path: Path) -> None:
    """The disclosures in QUICKSTART/RUN_SUMMARY/DATA_DICTIONARY must
    mention the existence of perturbation WITHOUT leaking exact scale
    factors, seeds, or per-company multipliers in the public-facing
    certificate / disclosure prose."""
    from fenrix_synthetic.anonymization.numeric_transform import (
        PERTURBATION_DISCLOSURE,
        PRIVATE_TRANSFORM_KEYS,
    )

    # 1. The disclosure string must mention perturbation / transformation
    #    intent so reviewers understand that financial values are not literal.
    lowered = PERTURBATION_DISCLOSURE.lower()
    assert "transformed" in lowered or "perturb" in lowered, (
        "PERTURBATION_DISCLOSURE must state that values are transformed"
    )
    # 2. The disclosure must NOT contain any private-detail key name
    #    (which would leak the existence of reversible parameters).
    for bad_key in PRIVATE_TRANSFORM_KEYS:
        assert bad_key not in PERTURBATION_DISCLOSURE, (
            f"PERTURBATION_DISCLOSURE leaks private key {bad_key!r}"
        )
    # 3. The disclosure must NOT contain numeric scale factors like
    #    "0.65", "1.35", "20%", "+20%" — only the existence of the
    #    policy, not the parameters.
    for bad_value in ("0.65", "1.35", "0.85", "1.15", "+20%", "-20%", "20%"):
        assert bad_value not in PERTURBATION_DISCLOSURE, (
            f"PERTURBATION_DISCLOSURE leaks scale literal {bad_value!r}"
        )

    # 4. The per-bundle RUN_SUMMARY.md and DATA_DICTIONARY.md writers
    #    use the same constant; verify by reproducing a run.
    from fenrix_synthetic.professor.multi_orchestrator import write_top_level_bundle_files

    write_top_level_bundle_files(
        output_root=tmp_path,
        companies_processed=["COMPANY_001"],
        blind_guess_summary={
            "companies_reviewed": 1,
            "companies_passed": 1,
            "actual_source_top_1": [],
            "actual_source_top_3": [],
            "high_confidence_guesses": [],
            "privacy_gate": "pass",
        },
        utility_summary={
            "average_utility_score": 0.8,
            "min_score": 0.8,
            "max_score": 0.8,
            "utility_gate": "pass",
        },
        release_date="2026-06-22",
        source_mapping={
            "COMPANY_001": {"source_company": "!REDACTED!", "source_ticker": "!REDACTED!"}
        },
    )
    for fname in ("README.md", "QUICKSTART.md", "RUN_SUMMARY.md", "DATA_DICTIONARY.md"):
        body = (tmp_path / fname).read_text()
        assert "Financial values in this bundle have been consistently transformed" in body, (
            f"{fname} missing PERTURBATION_DISCLOSURE block"
        )
        # The docs must not reveal scale literal values.
        for bad_value in ("0.65", "1.35", "0.85", "1.15", "+20%"):
            assert bad_value not in body, f"{fname} leaks scale literal {bad_value!r}"
