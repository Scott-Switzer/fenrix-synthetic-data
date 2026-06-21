"""Classroom-safe numeric writer (Phase 9).

Produces accounting-consistent synthetic annual and quarterly statements,
coarse weekly market feature buckets (no exact OHLCV), ratio buckets,
and broad regime labels for the classroom release package.

**Crucial invariants** enforced by this module:
- NEVER uses real OHLCV, exact dates, exact revenue path, exact statement
  values, or any real numerical trajectory.
- All numeric content is generated deterministically from a hash of the
  ticker symbol so classroom releases are reproducible across runs.
- The accounting identity ``Assets == Liabilities + Equity`` is checked
  for every annual and quarterly statement. Any violation is recorded.
- Outputs are labeled ``relative_period`` / ``relative_year`` so that
  consumers cannot confuse them with real public statements.

Output files written to ``public/numeric/classroom_safe/``:

    annual_statements.json     — 5 years of synthetic annual statements
    quarterly_statements.json  — 8 most-recent quarters of synthetic data
    weekly_features.json       — 13 weeks of coarse weekly feature buckets
    ratio_and_regime_index.json — coarse ratio buckets + regime label
"""

from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import orjson

logger = logging.getLogger(__name__)


# ── Enums ──────────────────────────────────────────────────────────────


class RegimeLabel(StrEnum):
    EARLY_EXPANSION = "early_expansion"
    MID_CYCLE = "mid_cycle"
    LATE_CYCLE = "late_cycle"
    CONTRACTION = "contraction"


class RatioBucket(StrEnum):
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


# ── Synthetic annual statement model ───────────────────────────────────


@dataclass
class SyntheticAnnualStatement:
    """Synthetic annual statement (relative year, amounts in millions)."""

    relative_year: int  # -1 = most recent, -2 = prior, ...
    revenue: float
    cogs: float
    gross_profit: float
    operating_expenses: float
    operating_income: float
    interest_expense: float
    pre_tax_income: float
    tax_expense: float
    net_income: float
    cash: float
    accounts_receivable: float
    inventory: float
    ppe_net: float
    goodwill: float
    total_assets: float
    accounts_payable: float
    short_term_debt: float
    long_term_debt: float
    total_equity: float
    operating_cash_flow: float
    investing_cash_flow: float
    financing_cash_flow: float
    # Derived for convenience
    current_assets: float = 0.0
    current_liabilities: float = 0.0
    total_liabilities: float = 0.0
    retained_earnings: float = 0.0
    common_stock: float = 0.0

    def validate_identities(self) -> list[str]:
        """Validate accounting identities; return list of violation messages.

        Accounting identities tested:
        - Gross profit == Revenue − COGS
        - Operating income == Gross profit − Operating expenses
        - Net income == Pre-tax − Tax expense
        - Total assets ≡ Current + PPE + Goodwill (kept by build)
        - Balance: Assets == Liabilities + Equity (within rounding)
        """
        errors: list[str] = []
        tolerance = max(self.revenue * 0.005, 1.0)
        if abs(self.gross_profit - (self.revenue - self.cogs)) > tolerance:
            errors.append(
                f"Gross profit {self.gross_profit:.0f} != revenue - COGS "
                f"= {(self.revenue - self.cogs):.0f}"
            )
        if abs(self.operating_income - (self.gross_profit - self.operating_expenses)) > tolerance:
            errors.append(f"Operating income {self.operating_income:.0f} != gross - opex")
        if abs(self.net_income - (self.pre_tax_income - self.tax_expense)) > tolerance:
            errors.append(f"Net income {self.net_income:.0f} != pre_tax - tax")
        balance_diff = self.total_assets - (self.total_liabilities + self.total_equity)
        if abs(balance_diff) > max(self.total_assets * 0.005, 1.0):
            errors.append(f"Balance equation violated: A - (L + E) = {balance_diff:.2f}")
        return errors


# ── Writer result ──────────────────────────────────────────────────────


@dataclass
class ClassroomNumericPackage:
    """Summary of the classroom-safe numeric package written to disk."""

    ticker: str
    synthetic_company: str
    annual_count: int = 0
    quarterly_count: int = 0
    weekly_count: int = 0
    ratio_buckets_count: int = 0
    regime_label: str = RegimeLabel.MID_CYCLE.value
    all_annual_identities_valid: bool = True
    identity_violations: list[str] = field(default_factory=list)
    written_files: list[str] = field(default_factory=list)


# ── Writer ─────────────────────────────────────────────────────────────


class ClassroomNumericWriter:
    """Generate classroom-safe numeric package deterministically.

    Inputs: ticker symbol (used to seed RNG so the package is reproducible).
    NEVER reads any real financial data; all values are synthetic.
    """

    DEFAULT_SYNTHETIC_COMPANY = "Aster"

    def __init__(
        self,
        ticker: str,
        n_quarterly: int = 8,
        n_annual: int = 5,
        n_weekly: int = 13,
        synthetic_company: str | None = None,
    ) -> None:
        self.ticker = ticker.upper()
        self.n_quarterly = n_quarterly
        self.n_annual = n_annual
        self.n_weekly = n_weekly
        self.synthetic_company = synthetic_company or self.DEFAULT_SYNTHETIC_COMPANY
        self.seed = self._derive_seed(self.ticker)
        # Local RNG state is reset for each call so weekly features don't
        # conflate with annual/quarterly deterministic draws.
        self._rng = random.Random(self.seed)

    @staticmethod
    def _derive_seed(ticker: str) -> int:
        return int(hashlib.sha256(ticker.upper().encode()).hexdigest()[:8], 16)

    # ── Annual statement builder ─────────────────────────────────

    def _build_annual_statement(
        self, revenue: float, relative_year: int
    ) -> SyntheticAnnualStatement:
        rng = random.Random(self.seed + relative_year)  # deterministic per-year
        cogs_pct = 0.42 + (rng.random() - 0.5) * 0.10  # 37-47 %
        cogs = revenue * cogs_pct
        gross_profit = revenue - cogs
        opex_pct = 0.20 + (rng.random() - 0.5) * 0.08  # 16-24 %
        operating_expenses = revenue * opex_pct
        operating_income = gross_profit - operating_expenses
        interest_expense = revenue * (0.005 + rng.random() * 0.012)
        pre_tax_income = operating_income - interest_expense
        tax_rate = 0.21 + (rng.random() - 0.5) * 0.04
        tax_expense = max(pre_tax_income * tax_rate, 0.0)
        net_income = pre_tax_income - tax_expense

        # Balance sheet
        cash = revenue * (0.10 + rng.random() * 0.20)
        ar = revenue * (0.07 + rng.random() * 0.05)
        inventory = cogs * (0.07 + rng.random() * 0.04)
        current_assets = cash + ar + inventory
        ppe_net = revenue * (0.30 + rng.random() * 0.20)
        goodwill = revenue * (0.10 + rng.random() * 0.15)
        total_assets = current_assets + ppe_net + goodwill

        ap = cogs * (0.05 + rng.random() * 0.03)
        st_debt = revenue * (0.02 + rng.random() * 0.03)
        current_liabilities = ap + st_debt
        lt_debt = revenue * (0.10 + rng.random() * 0.10)
        total_liabilities = current_liabilities + lt_debt

        # Equity MUST satisfy A = L + E exactly
        total_equity = total_assets - total_liabilities
        common_stock = total_equity * (0.30 + rng.random() * 0.10)
        retained_earnings = total_equity - common_stock

        # Cash flow (rough synthetic estimates)
        operating_cash_flow = net_income + revenue * 0.08
        investing_cash_flow = -(ppe_net * 0.20)
        financing_cash_flow = -lt_debt * 0.05
        if net_income > 0:
            financing_cash_flow -= revenue * 0.02

        return SyntheticAnnualStatement(
            relative_year=relative_year,
            revenue=round(revenue, 0),
            cogs=round(cogs, 0),
            gross_profit=round(gross_profit, 0),
            operating_expenses=round(operating_expenses, 0),
            operating_income=round(operating_income, 0),
            interest_expense=round(interest_expense, 0),
            pre_tax_income=round(pre_tax_income, 0),
            tax_expense=round(tax_expense, 0),
            net_income=round(net_income, 0),
            cash=round(cash, 0),
            accounts_receivable=round(ar, 0),
            inventory=round(inventory, 0),
            ppe_net=round(ppe_net, 0),
            goodwill=round(goodwill, 0),
            total_assets=round(total_assets, 0),
            accounts_payable=round(ap, 0),
            short_term_debt=round(st_debt, 0),
            long_term_debt=round(lt_debt, 0),
            total_equity=round(total_equity, 0),
            operating_cash_flow=round(operating_cash_flow, 0),
            investing_cash_flow=round(investing_cash_flow, 0),
            financing_cash_flow=round(financing_cash_flow, 0),
            current_assets=round(current_assets, 0),
            current_liabilities=round(current_liabilities, 0),
            total_liabilities=round(total_liabilities, 0),
            retained_earnings=round(retained_earnings, 0),
            common_stock=round(common_stock, 0),
        )

    def generate_annual_statements(self) -> list[SyntheticAnnualStatement]:
        """Generate N years of synthetic accounting-consistent annual statements.

        Years labelled relative: -1 = most recent, -2 = prior, etc.
        """
        base_revenue = 800 + (self.seed % 1200)  # 800-1999
        revenue_growth = 0.05 + (self.seed % 9) / 200.0  # 5-9.5 %
        statements: list[SyntheticAnnualStatement] = []
        # Start from assumed oldest year (so growth rolls forward)
        rev = base_revenue / ((1 + revenue_growth) ** (self.n_annual - 1))
        for i in range(self.n_annual):
            rev = rev * (
                1 + revenue_growth + (random.Random(self.seed + 100 + i).random() - 0.5) * 0.04
            )
            relative_year = -(self.n_annual - i)
            statements.append(self._build_annual_statement(rev, relative_year))
        return statements

    def generate_quarterly_statements(self) -> list[dict[str, Any]]:
        """Generate N most-recent synthetic quarterly statements.

        Labels are relative: ``Period 0`` = current quarter, ``Period -1``
        = prior, etc.
        """
        base_q_rev = (self.seed >> 8) % 1000 + 200  # 200-1199
        quarters: list[dict[str, Any]] = []
        for i in range(self.n_quarterly):
            rng = random.Random(self.seed + 5000 + i)
            drift = -(i * 0.005)
            rev = base_q_rev * (1 + drift + (rng.random() - 0.5) * 0.05)
            cogs = rev * 0.45
            gross = rev - cogs
            opex = rev * 0.22
            op_income = gross - opex
            net_income = op_income * 0.78
            total_assets = rev * 4 + (rng.random() - 0.5) * rev * 0.15
            total_liabilities = total_assets * (0.40 + (rng.random() - 0.5) * 0.10)
            total_equity = total_assets - total_liabilities
            quarters.append(
                {
                    "period_label": f"Period -{i}",
                    "relative_period": True,
                    "revenue": round(rev, 0),
                    "cost_of_goods_sold": round(cogs, 0),
                    "gross_profit": round(gross, 0),
                    "operating_income": round(op_income, 0),
                    "net_income": round(net_income, 0),
                    "total_assets": round(total_assets, 0),
                    "total_liabilities": round(total_liabilities, 0),
                    "total_equity": round(total_equity, 0),
                }
            )
        return quarters

    def generate_weekly_features(self) -> list[dict[str, Any]]:
        """Generate coarse weekly market features — NO OHLCV, only buckets."""
        rng = random.Random(self.seed + 9000)
        features: list[dict[str, Any]] = []
        for i in range(self.n_weekly):
            ret = (rng.random() - 0.5) * 0.06  # synthetic weekly return
            if ret > 0:
                direction = "up"
            elif ret < 0:
                direction = "down"
            else:
                direction = "flat"
            magnitude = self._bucket_ratio(
                abs(ret),
                [
                    (0.005, RatioBucket.VERY_LOW),
                    (0.015, RatioBucket.LOW),
                    (0.030, RatioBucket.MEDIUM),
                    (1.0, RatioBucket.HIGH),
                ],
            )
            features.append(
                {
                    "relative_week": f"Week -{i}",
                    "relative_period": True,
                    "synthetic_direction": direction,
                    "synthetic_change_bucket": magnitude.value,
                }
            )
        return features

    @staticmethod
    def _bucket_ratio(value: float, thresholds: list[tuple[float, RatioBucket]]) -> RatioBucket:
        for thresh, bucket in thresholds:
            if value < thresh:
                return bucket
        return RatioBucket.VERY_HIGH

    def generate_ratio_buckets(self, statements: list[SyntheticAnnualStatement]) -> dict[str, str]:
        """Compute coarse ratio buckets from most recent annual statement."""
        if not statements:
            return {}
        latest = statements[-1]
        buckets: dict[str, str] = {}
        if latest.current_liabilities > 0:
            cr = latest.current_assets / latest.current_liabilities
            buckets["current_ratio"] = self._bucket_ratio(
                cr,
                [
                    (0.8, RatioBucket.VERY_LOW),
                    (1.0, RatioBucket.LOW),
                    (1.5, RatioBucket.MEDIUM),
                    (2.5, RatioBucket.HIGH),
                    (999.0, RatioBucket.VERY_HIGH),
                ],
            ).value
        if latest.total_equity > 0:
            de = latest.total_liabilities / latest.total_equity
            buckets["debt_to_equity"] = self._bucket_ratio(
                de,
                [
                    (0.3, RatioBucket.VERY_LOW),
                    (0.6, RatioBucket.LOW),
                    (1.0, RatioBucket.MEDIUM),
                    (2.0, RatioBucket.HIGH),
                    (999.0, RatioBucket.VERY_HIGH),
                ],
            ).value
        if latest.revenue > 0:
            nm = latest.net_income / latest.revenue
            buckets["net_margin"] = self._bucket_ratio(
                nm,
                [
                    (0.05, RatioBucket.VERY_LOW),
                    (0.10, RatioBucket.LOW),
                    (0.15, RatioBucket.MEDIUM),
                    (0.25, RatioBucket.HIGH),
                    (999.0, RatioBucket.VERY_HIGH),
                ],
            ).value
        return buckets

    def classify_regime(self, weekly_features: list[dict[str, Any]]) -> str:
        """Classify synthetic trend regime from coarse weekly features."""
        if not weekly_features:
            return RegimeLabel.MID_CYCLE.value
        recent = weekly_features[:4]
        ups = sum(1 for w in recent if w["synthetic_direction"] == "up")
        downs = sum(1 for w in recent if w["synthetic_direction"] == "down")
        if ups >= 3:
            return RegimeLabel.EARLY_EXPANSION.value
        if downs >= 3:
            return RegimeLabel.CONTRACTION.value
        if ups == 2 and downs <= 1:
            return RegimeLabel.MID_CYCLE.value
        if ups <= 1 and downs >= 2:
            return RegimeLabel.LATE_CYCLE.value
        return RegimeLabel.MID_CYCLE.value

    # ── Public entry point ────────────────────────────────────────

    def write_package(
        self,
        output_dir: Path,
    ) -> ClassroomNumericPackage:
        """Write the full classroom-safe numeric package to ``output_dir``."""
        output_dir.mkdir(parents=True, exist_ok=True)

        annual = self.generate_annual_statements()
        quarterly = self.generate_quarterly_statements()
        weekly = self.generate_weekly_features()
        ratios = self.generate_ratio_buckets(annual)
        regime = self.classify_regime(weekly)

        violations: list[str] = []
        for s in annual:
            for err in s.validate_identities():
                violations.append(f"year {s.relative_year}: {err}")

        pkg = ClassroomNumericPackage(
            ticker=self.ticker,
            synthetic_company=self.synthetic_company,
            annual_count=len(annual),
            quarterly_count=len(quarterly),
            weekly_count=len(weekly),
            ratio_buckets_count=len(ratios),
            regime_label=regime,
            all_annual_identities_valid=not violations,
            identity_violations=violations,
        )

        # Annual statements
        annual_payload: dict[str, Any] = {
            "synthetic_company": self.synthetic_company,
            "ticker_seed": self.ticker,
            "label": "synthetic accounting-consistent annual statements",
            "no_real_public_trajectory": True,
            "years_labeled_relative": True,
            "n_years": len(annual),
            "statements": [
                {
                    "relative_year": s.relative_year,
                    "income_statement": {
                        "revenue": s.revenue,
                        "cost_of_goods_sold": s.cogs,
                        "gross_profit": s.gross_profit,
                        "operating_expenses": s.operating_expenses,
                        "operating_income": s.operating_income,
                        "interest_expense": s.interest_expense,
                        "pre_tax_income": s.pre_tax_income,
                        "tax_expense": s.tax_expense,
                        "net_income": s.net_income,
                    },
                    "balance_sheet": {
                        "cash_and_equivalents": s.cash,
                        "accounts_receivable": s.accounts_receivable,
                        "inventory": s.inventory,
                        "total_current_assets": s.current_assets,
                        "ppe_net": s.ppe_net,
                        "goodwill": s.goodwill,
                        "total_assets": s.total_assets,
                        "accounts_payable": s.accounts_payable,
                        "short_term_debt": s.short_term_debt,
                        "total_current_liabilities": s.current_liabilities,
                        "long_term_debt": s.long_term_debt,
                        "total_liabilities": s.total_liabilities,
                        "common_stock": s.common_stock,
                        "retained_earnings": s.retained_earnings,
                        "total_equity": s.total_equity,
                    },
                    "cash_flow": {
                        "operating": s.operating_cash_flow,
                        "investing": s.investing_cash_flow,
                        "financing": s.financing_cash_flow,
                    },
                }
                for s in annual
            ],
            "validation": {
                "all_accounting_identities_hold": not violations,
                "violations": violations,
            },
        }
        annual_path = output_dir / "annual_statements.json"
        annual_path.write_bytes(
            orjson.dumps(annual_payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # Quarterly statements
        quarterly_payload: dict[str, Any] = {
            "synthetic_company": self.synthetic_company,
            "ticker_seed": self.ticker,
            "label": "synthetic accounting-consistent quarterly statements",
            "periods_labeled_relative": True,
            "no_real_public_trajectory": True,
            "n_periods": len(quarterly),
            "statements": quarterly,
            "validation": {
                "all_balance_equations_hold": all(
                    abs(q["total_assets"] - q["total_liabilities"] - q["total_equity"])
                    < max(q["total_assets"] * 0.005, 1.0)
                    for q in quarterly
                ),
            },
        }
        quarterly_path = output_dir / "quarterly_statements.json"
        quarterly_path.write_bytes(
            orjson.dumps(quarterly_payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # Weekly features
        weekly_path = output_dir / "weekly_features.json"
        weekly_payload: dict[str, Any] = {
            "synthetic_company": self.synthetic_company,
            "ticker_seed": self.ticker,
            "label": "synthetic weekly market features (classroom-safe)",
            "periods_labeled_relative": True,
            "no_exact_ohlcv": True,
            "n_weeks": len(weekly),
            "features": weekly,
            "regime_classification": regime,
        }
        weekly_path.write_bytes(
            orjson.dumps(weekly_payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # Ratio + regime index
        index_path = output_dir / "ratio_and_regime_index.json"
        index_payload: dict[str, Any] = {
            "synthetic_company": self.synthetic_company,
            "ticker_seed": self.ticker,
            "label": "classroom-safe ratio buckets and broad regime labels",
            "ratio_buckets": ratios,
            "broad_regime_label": regime,
            "n_ratio_buckets": len(ratios),
        }
        index_path.write_bytes(
            orjson.dumps(index_payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        pkg.written_files = [
            str(annual_path),
            str(quarterly_path),
            str(weekly_path),
            str(index_path),
        ]
        return pkg
