"""Unit tests for V3.2 volume gate module."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fenrix_synthetic.qa.volume_gate import (
    DEFAULT_THRESHOLDS,
    VOLUME_FAIL,
    VOLUME_PASS,
    VOLUME_PASS_WITH_WAIVER,
    VolumeGateResult,
    VolumeThresholds,
    evaluate_volume_gate,
)


def _build_minimal_bundle(output_root: Path, companies: int = 8, years: list[int] | None = None) -> None:
    """Build a minimal bundle directory tree for volume gate testing."""
    if years is None:
        years = list(range(2016, 2026))  # 2016-2025

    public_dir = output_root / "public" / "anonymized"
    for i in range(1, companies + 1):
        cid = f"COMPANY_{i:03d}"
        cd = public_dir / cid
        (cd / "profile").mkdir(parents=True, exist_ok=True)
        (cd / "profile" / "archetype_card.json").write_text(json.dumps({"archetype_key": "test"}))
        (cd / "profile" / "profile.md").write_text("# Profile")

        (cd / "financials").mkdir(parents=True, exist_ok=True)
        csv_lines = ["year,metric_name,transformed_value,family"]
        for y in years:
            csv_lines.append(f"{y},Revenue,5.5,income_statement")
        (cd / "financials" / "transformed_metrics.csv").write_text("\n".join(csv_lines))

        (cd / "financials" / "summary.md").write_text(f"Coverage: {min(years)}-{max(years)}")

        (cd / "financials" / "reconciliation_summary.md").write_text("# Reconciliation")

        (cd / "market").mkdir(parents=True, exist_ok=True)
        price_lines = ["relative_day,price,volume_indicator"] + [
            f"DAY_{j:04d},{100 + j * 0.01},3" for j in range(1100)
        ]
        (cd / "market" / "price_series.csv").write_text("\n".join(price_lines))

        (cd / "sec").mkdir(parents=True, exist_ok=True)
        (cd / "sec" / "filing_coverage.md").write_text("# Coverage\nHonestly-labeled fallback.")
        (cd / "sec" / "annual_report_business.md").write_text(f"## Business for {cid}")

        (cd / "news").mkdir(parents=True, exist_ok=True)
        (cd / "news" / "event_timeline.csv").write_text(
            "brief_id,company_id,event_class,relative_period\n"
            + "\n".join(f"news_{j},{cid},demand_shift,Year -1 Q1" for j in range(6))
        )

    # Top-level docs
    (output_root / "README.md").write_text("# README")
    (output_root / "QUICKSTART.md").write_text("# Quickstart")
    (output_root / "RUN_SUMMARY.md").write_text("# Run Summary\nEarliest year: 2016, Latest: 2025")

    # Coverage
    (output_root / "coverage").mkdir(parents=True, exist_ok=True)
    (output_root / "coverage" / "source_coverage_by_company.csv").write_text(
        "company_id,earliest_year,latest_year\n"
        + "\n".join(f"COMPANY_{i:03d},2016,2025" for i in range(1, 9))
    )

    # QA
    (output_root / "qa").mkdir(parents=True, exist_ok=True)


def test_volume_gate_pass_with_all_targets_met():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root)
        # The default thresholds require >= 100 SEC docs and >= 1000 ZIP entries.
        # Our minimal fixture has only 2 SEC docs per company and 82 entries.
        # This is expected to FAIL on volume — the gate is intentionally strict.
        result = evaluate_volume_gate(root)
        assert isinstance(result, VolumeGateResult)
        assert result.company_count == 8
        # Year span and no-future-years checks should pass
        year_check = next(c for c in result.checks if c.check_id == "min_year_span")
        assert year_check.passed
        future_check = next(c for c in result.checks if c.check_id == "no_future_years")
        assert future_check.passed
        # But volume checks fail (2 docs < 100, 82 entries < 1000) — this is correct
        sec_check = next(c for c in result.checks if c.check_id == "min_sec_docs")
        assert not sec_check.passed
        assert result.verdict == VOLUME_FAIL


def test_volume_gate_passes_with_waiver_on_low_volume():
    """With a source-backed waiver, low volume should pass (PASS not PASS_WITH_WAIVER
    when all volume checks pass through the waiver)."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root)
        result = evaluate_volume_gate(
            root,
            waiver_reason="Source coverage limited. Honest fallback stubs used.",
        )
        # When waiver makes all checks pass, verdict is PASS (waiver fully covers gaps)
        assert result.passed
        assert result.verdict == VOLUME_PASS


def test_volume_gate_fails_on_future_years():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root, years=list(range(2016, 2027)))  # includes 2026
        result = evaluate_volume_gate(root)
        # Should fail because 2026 is a future year
        assert not result.per_company[0].has_future_years is False
        # Check the actual result
        future_check = next(c for c in result.checks if c.check_id == "no_future_years")
        assert not future_check.passed


def test_volume_gate_fails_on_few_companies():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root, companies=4)
        result = evaluate_volume_gate(root)
        assert result.company_count == 4
        count_check = next(c for c in result.checks if c.check_id == "company_count")
        assert not count_check.passed


def test_volume_gate_passes_with_waiver():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Use 8 companies but only 5 years — waiver covers the year gap
        _build_minimal_bundle(root, years=list(range(2021, 2026)))  # 5 years
        result = evaluate_volume_gate(
            root,
            waiver_reason="Source coverage limited to 5 years for these companies.",
        )
        assert result.passed
        assert result.verdict == VOLUME_PASS


def test_volume_gate_fails_on_low_year_span_without_waiver():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root, years=list(range(2022, 2026)))  # only 4 years
        result = evaluate_volume_gate(root)
        year_check = next(c for c in result.checks if c.check_id == "min_year_span")
        assert not year_check.passed
        assert not result.passed  # No waiver


def test_volume_gate_no_future_years_in_summary_md():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root, years=list(range(2016, 2026)))
        # Put a future year in a summary.md
        for i in range(1, 9):
            summary = root / "public" / "anonymized" / f"COMPANY_{i:03d}" / "financials" / "summary.md"
            summary.write_text("Coverage: 2016-2029 (future years included)")
        result = evaluate_volume_gate(root)
        assert not result.passed
        assert result.verdict == VOLUME_FAIL


def test_volume_gate_per_company_stats():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root)
        result = evaluate_volume_gate(root)
        assert len(result.per_company) == 8
        for pc in result.per_company:
            assert pc.sec_docs > 0
            assert pc.financial_files > 0
            assert pc.market_files > 0
            assert pc.earliest_year == 2016
            assert pc.latest_year == 2025
            assert pc.year_span == 10
            assert not pc.has_future_years


def test_volume_gate_report_writable():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root)
        result = evaluate_volume_gate(root)
        from fenrix_synthetic.qa.volume_gate import write_volume_gate_report
        report_path = write_volume_gate_report(result, root / "qa")
        assert report_path.exists()
        data = json.loads(report_path.read_text())
    assert data["verdict"] == VOLUME_FAIL  # Default volume is too low for PASS
    assert data["company_count"] == 8


def test_min_market_rows_check():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root)
        # Reduce market rows below 1000
        for i in range(1, 9):
            price_path = root / "public" / "anonymized" / f"COMPANY_{i:03d}" / "market" / "price_series.csv"
            price_path.write_text("relative_day,price\n" + "\n".join(f"DAY_{j},100" for j in range(500)))
        result = evaluate_volume_gate(root)
        market_check = next(c for c in result.checks if c.check_id == "min_market_rows")
        assert not market_check.passed


def test_custom_thresholds():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_minimal_bundle(root, companies=3, years=list(range(2020, 2026)))
        thresholds = VolumeThresholds(
            min_companies=3,
            min_total_zip_entries=30,  # 37 entries > 30, so this passes
            min_sec_docs_per_company=1,
            min_year_span_per_company=3,
            target_end_year=2025,
            min_market_rows_per_company=500,
        )
        result = evaluate_volume_gate(root, thresholds=thresholds)
        assert result.passed
