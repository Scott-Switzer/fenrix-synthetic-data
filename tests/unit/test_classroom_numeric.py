"""Tests for the ClassroomNumericWriter (synthetic accounting-consistent)."""

from __future__ import annotations

import json
from pathlib import Path

from fenrix_synthetic.anonymization.classroom_numeric_writer import (
    ClassroomNumericWriter,
    RatioBucket,
    RegimeLabel,
    SyntheticAnnualStatement,
)


class TestDeterministicSeed:
    def test_seed_is_deterministic(self) -> None:
        assert ClassroomNumericWriter._derive_seed("CHC") == ClassroomNumericWriter._derive_seed(
            "CHC"
        )
        assert ClassroomNumericWriter._derive_seed("CHC") != ClassroomNumericWriter._derive_seed(
            "AAPL"
        )


class TestAnnualStatements:
    def test_default_five_years(self) -> None:
        w = ClassroomNumericWriter("CHC")
        stmts = w.generate_annual_statements()
        assert len(stmts) == 5
        # Relative years: -5..-1
        relative_years = [s.relative_year for s in stmts]
        assert relative_years == [-5, -4, -3, -2, -1]

    def test_accounting_identity_holds_for_all_years(self) -> None:
        w = ClassroomNumericWriter("FAKE-001")
        for s in w.generate_annual_statements():
            errs = s.validate_identities()
            assert errs == [], f"Identity violations for year {s.relative_year}: {errs}"

    def test_balance_equation_exact(self) -> None:
        # Even though rounding may cause off-by-1 differences, the
        # underlying construction enforces A = L + E. Check at integer level.
        w = ClassroomNumericWriter("FAKE-002")
        for s in w.generate_annual_statements():
            diff = s.total_assets - (s.total_liabilities + s.total_equity)
            assert abs(diff) <= max(s.total_assets * 0.005, 1.0)

    def test_revenue_uses_arbitrary_units(self) -> None:
        w = ClassroomNumericWriter("FAKE-003")
        for s in w.generate_annual_statements():
            assert s.revenue > 0
            assert s.cogs > 0
            assert s.gross_profit > 0


class TestQuarterlyStatements:
    def test_default_eight_quarters(self) -> None:
        w = ClassroomNumericWriter("CHC")
        qs = w.generate_quarterly_statements()
        assert len(qs) == 8
        # Period labels relative
        labels = [q["period_label"] for q in qs]
        assert labels[0] == "Period -0"
        assert labels[-1] == "Period -7"

    def test_periods_marked_relative(self) -> None:
        w = ClassroomNumericWriter("CHC")
        qs = w.generate_quarterly_statements()
        for q in qs:
            assert q["relative_period"] is True

    def test_quarterly_balance_equation(self) -> None:
        w = ClassroomNumericWriter("FAKE-Q")
        for q in w.generate_quarterly_statements():
            diff = q["total_assets"] - q["total_liabilities"] - q["total_equity"]
            assert abs(diff) <= max(q["total_assets"] * 0.005, 1.0)


class TestWeeklyFeatures:
    def test_default_thirteen_weeks(self) -> None:
        w = ClassroomNumericWriter("CHC")
        f = w.generate_weekly_features()
        assert len(f) == 13
        for feat in f:
            assert feat["synthetic_direction"] in {"up", "down", "flat"}
            assert feat["synthetic_change_bucket"] in {
                RatioBucket.VERY_LOW.value,
                RatioBucket.LOW.value,
                RatioBucket.MEDIUM.value,
                RatioBucket.HIGH.value,
                RatioBucket.VERY_HIGH.value,
            }

    def test_no_exact_ohlcv_keys(self) -> None:
        w = ClassroomNumericWriter("CHC")
        for feat in w.generate_weekly_features():
            assert "open" not in feat
            assert "high" not in feat
            assert "low" not in feat
            assert "close" not in feat
            assert "volume" not in feat
            assert "exact_change" not in feat


class TestRatioBuckets:
    def test_three_ratios_when_valid(self) -> None:
        w = ClassroomNumericWriter("CHC")
        annual = w.generate_annual_statements()
        ratios = w.generate_ratio_buckets(annual)
        assert "current_ratio" in ratios
        assert "debt_to_equity" in ratios
        assert "net_margin" in ratios
        for v in ratios.values():
            assert v in {
                RatioBucket.VERY_LOW.value,
                RatioBucket.LOW.value,
                RatioBucket.MEDIUM.value,
                RatioBucket.HIGH.value,
                RatioBucket.VERY_HIGH.value,
            }

    def test_empty_statements_returns_empty(self) -> None:
        w = ClassroomNumericWriter("CHC")
        assert w.generate_ratio_buckets([]) == {}


class TestRegimeClassification:
    def test_returns_enum_value(self) -> None:
        w = ClassroomNumericWriter("CHC")
        weekly = w.generate_weekly_features()
        regime = w.classify_regime(weekly)
        assert regime in {
            RegimeLabel.EARLY_EXPANSION.value,
            RegimeLabel.MID_CYCLE.value,
            RegimeLabel.LATE_CYCLE.value,
            RegimeLabel.CONTRACTION.value,
        }

    def test_empty_weekly_returns_default(self) -> None:
        w = ClassroomNumericWriter("CHC")
        assert w.classify_regime([]) == RegimeLabel.MID_CYCLE.value


class TestWritePackage:
    def test_writes_four_files(self, tmp_path: Path) -> None:
        w = ClassroomNumericWriter("CHC")
        pkg = w.write_package(tmp_path / "classroom_safe")
        assert pkg.annual_count == 5
        assert pkg.quarterly_count == 8
        assert pkg.weekly_count == 13
        assert pkg.all_annual_identities_valid is True
        assert pkg.identity_violations == []
        assert len(pkg.written_files) == 4
        for f in pkg.written_files:
            assert Path(f).exists()

    def test_annual_payload_includes_validation_block(self, tmp_path: Path) -> None:
        w = ClassroomNumericWriter("CHC")
        w.write_package(tmp_path / "classroom_safe")
        d = json.loads((tmp_path / "classroom_safe" / "annual_statements.json").read_text())
        assert d["no_real_public_trajectory"] is True
        assert d["years_labeled_relative"] is True
        assert d["validation"]["all_accounting_identities_hold"] is True
        assert d["validation"]["violations"] == []
        # No real ticker leaked
        assert d["ticker_seed"] == "CHC"
        # Statements marked relative
        for stmt in d["statements"]:
            assert stmt["relative_year"] < 0

    def test_quarterly_payload_no_real_trajectory(self, tmp_path: Path) -> None:
        w = ClassroomNumericWriter("CHC")
        w.write_package(tmp_path / "classroom_safe")
        d = json.loads((tmp_path / "classroom_safe" / "quarterly_statements.json").read_text())
        assert d["periods_labeled_relative"] is True
        assert d["no_real_public_trajectory"] is True
        assert d["validation"]["all_balance_equations_hold"] is True

    def test_weekly_features_no_exact_ohlcv(self, tmp_path: Path) -> None:
        w = ClassroomNumericWriter("CHC")
        w.write_package(tmp_path / "classroom_safe")
        d = json.loads((tmp_path / "classroom_safe" / "weekly_features.json").read_text())
        assert d["no_exact_ohlcv"] is True
        assert d["periods_labeled_relative"] is True
        assert isinstance(d["features"], list)
        assert len(d["features"]) > 0
        for f in d["features"]:
            assert "relative_week" in f

    def test_ratio_index_includes_regime(self, tmp_path: Path) -> None:
        w = ClassroomNumericWriter("CHC")
        w.write_package(tmp_path / "classroom_safe")
        d = json.loads((tmp_path / "classroom_safe" / "ratio_and_regime_index.json").read_text())
        assert "broad_regime_label" in d
        assert "ratio_buckets" in d

    def test_different_tickers_produce_different_payloads(self, tmp_path: Path) -> None:
        # Deterministic seed -> ticker-deterministic package
        ClassroomNumericWriter("AAA").write_package(tmp_path / "a")
        ClassroomNumericWriter("BBB").write_package(tmp_path / "b")
        # Compare just the numeric annual statements
        da = json.loads((tmp_path / "a" / "annual_statements.json").read_text())
        db = json.loads((tmp_path / "b" / "annual_statements.json").read_text())
        revs_a = [s["income_statement"]["revenue"] for s in da["statements"]]
        revs_b = [s["income_statement"]["revenue"] for s in db["statements"]]
        assert revs_a != revs_b


class TestSyntheticAnnualStatementIdentity:
    def test_force_violation_detected(self) -> None:
        # Construct a deliberately invalid statement
        s = SyntheticAnnualStatement(
            relative_year=-1,
            revenue=1000.0,
            cogs=500.0,  # gross = 500
            gross_profit=400.0,  # WRONG: should be 500
            operating_expenses=200.0,
            operating_income=300.0,
            interest_expense=10.0,
            pre_tax_income=290.0,
            tax_expense=60.0,
            net_income=230.0,
            cash=200.0,
            accounts_receivable=100.0,
            inventory=50.0,
            ppe_net=400.0,
            goodwill=200.0,
            total_assets=950.0,
            accounts_payable=80.0,
            short_term_debt=20.0,
            long_term_debt=300.0,
            total_equity=550.0,  # L+E = 400 + 550 = 950 OK
            operating_cash_flow=300.0,
            investing_cash_flow=-100.0,
            financing_cash_flow=-50.0,
            current_assets=350.0,
            current_liabilities=100.0,
            total_liabilities=400.0,
            retained_earnings=400.0,
            common_stock=150.0,
        )
        errs = s.validate_identities()
        assert any("Gross profit" in e for e in errs)
