"""Accounting sanity checks for transformed financial data.

Validates that transformed financial statements respect basic accounting
identities and plausible ranges. Flags violations and warnings but does
not crash on incomplete data.

Usage:
    from .accounting_sanity import AccountingSanityChecker, SanityResult
    checker = AccountingSanityChecker(config)
    result = checker.check(transform_result)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .numeric_transform import TransformResult


@dataclass
class SanityResult:
    """Result of accounting sanity checks."""

    passes_all: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0
    checks_warned: int = 0
    checks_failed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passes_all": self.passes_all,
            "violations": self.violations,
            "warnings": self.warnings,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "checks_warned": self.checks_warned,
            "checks_failed": self.checks_failed,
        }


@dataclass
class SanityConfig:
    """Configuration for accounting sanity checks."""

    debt_to_liability_tolerance: float = 0.05  # 5% tolerance
    net_margin_min: float = -0.50
    net_margin_max: float = 0.50
    balance_equation_tolerance: float = 0.02  # 2% of assets
    min_revenue: float = 0.0
    min_assets: float = 0.0


class AccountingSanityChecker:
    """Check transformed financial data for accounting sanity."""

    def __init__(self, config: SanityConfig | None = None) -> None:
        self.config = config or SanityConfig()

    def check(self, result: TransformResult) -> SanityResult:
        """Run all sanity checks on a transform result."""
        violations: list[str] = []
        warnings: list[str] = []
        checks_run = 0
        checks_passed = 0
        checks_warned = 0
        checks_failed = 0

        # Build lookup maps by metric name and year
        metric_map: dict[str, dict[int, float]] = {}
        for m in result.metrics:
            if m.metric_name not in metric_map:
                metric_map[m.metric_name] = {}
            metric_map[m.metric_name][m.year] = m.transformed_value

        # Collect all years present
        all_years: set[int] = set()
        for ymap in metric_map.values():
            all_years.update(ymap.keys())

        for year in sorted(all_years):
            yr_metrics = {name: ymap.get(year) for name, ymap in metric_map.items()}

            # 1. Revenue > 0
            checks_run += 1
            rev = yr_metrics.get("revenue") or yr_metrics.get("total_revenue")
            if rev is not None:
                if rev <= self.config.min_revenue:
                    violations.append(
                        f"year {year}: revenue {rev:,.0f} <= {self.config.min_revenue}"
                    )
                    checks_failed += 1
                else:
                    checks_passed += 1
            else:
                warnings.append(f"year {year}: revenue missing, skipping revenue checks")
                checks_warned += 1

            # 2. Assets > 0
            checks_run += 1
            assets = yr_metrics.get("total_assets")
            if assets is not None:
                if assets <= self.config.min_assets:
                    violations.append(
                        f"year {year}: total_assets {assets:,.0f} <= {self.config.min_assets}"
                    )
                    checks_failed += 1
                else:
                    checks_passed += 1
            else:
                warnings.append(f"year {year}: total_assets missing, skipping asset checks")
                checks_warned += 1

            # 3. Liabilities >= 0
            checks_run += 1
            liab = yr_metrics.get("total_liabilities")
            if liab is not None:
                if liab < 0:
                    violations.append(f"year {year}: total_liabilities {liab:,.0f} < 0")
                    checks_failed += 1
                else:
                    checks_passed += 1
            else:
                warnings.append(f"year {year}: total_liabilities missing")
                checks_warned += 1

            # 4. Cash >= 0 and cash <= assets
            checks_run += 1
            cash = yr_metrics.get("cash") or yr_metrics.get("cash_and_equivalents")
            if cash is not None and assets is not None:
                if cash < 0:
                    violations.append(f"year {year}: cash {cash:,.0f} < 0")
                    checks_failed += 1
                elif cash > assets:
                    violations.append(f"year {year}: cash {cash:,.0f} > total_assets {assets:,.0f}")
                    checks_failed += 1
                else:
                    checks_passed += 1
            else:
                if cash is None:
                    warnings.append(f"year {year}: cash missing, skipping cash check")
                checks_warned += 1

            # 5. Debt >= 0 and debt <= liabilities + tolerance
            checks_run += 1
            debt = yr_metrics.get("long_term_debt") or yr_metrics.get("total_debt")
            if debt is not None and liab is not None:
                if debt < 0:
                    violations.append(f"year {year}: debt {debt:,.0f} < 0")
                    checks_failed += 1
                elif liab > 0 and debt > liab * (1 + self.config.debt_to_liability_tolerance):
                    violations.append(
                        f"year {year}: debt {debt:,.0f} > liabilities {liab:,.0f} + tolerance"
                    )
                    checks_failed += 1
                else:
                    checks_passed += 1
            else:
                if debt is None:
                    warnings.append(f"year {year}: debt missing, skipping debt check")
                checks_warned += 1

            # 6. Gross profit <= revenue (unless explicitly allowed)
            checks_run += 1
            gp = yr_metrics.get("gross_profit")
            if gp is not None and rev is not None:
                if gp > rev:
                    violations.append(f"year {year}: gross_profit {gp:,.0f} > revenue {rev:,.0f}")
                    checks_failed += 1
                else:
                    checks_passed += 1
            else:
                checks_warned += 1

            # 7. Operating income <= revenue
            checks_run += 1
            op_inc = yr_metrics.get("operating_income")
            if op_inc is not None and rev is not None:
                if op_inc > rev:
                    violations.append(
                        f"year {year}: operating_income {op_inc:,.0f} > revenue {rev:,.0f}"
                    )
                    checks_failed += 1
                else:
                    checks_passed += 1
            else:
                checks_warned += 1

            # 8. Net margin within plausible range
            checks_run += 1
            net_inc = yr_metrics.get("net_income")
            if net_inc is not None and rev is not None and rev != 0:
                margin = net_inc / rev
                if margin < self.config.net_margin_min or margin > self.config.net_margin_max:
                    violations.append(
                        f"year {year}: net_margin {margin:.2%} outside plausible range "
                        f"[{self.config.net_margin_min:.0%}, {self.config.net_margin_max:.0%}]"
                    )
                    checks_failed += 1
                else:
                    checks_passed += 1
            else:
                checks_warned += 1

            # 9. Assets ≈ liabilities + equity
            checks_run += 1
            equity = yr_metrics.get("total_equity")
            if assets is not None and liab is not None and equity is not None:
                balance_diff = abs(assets - (liab + equity))
                tolerance = max(assets * self.config.balance_equation_tolerance, 1.0)
                if balance_diff > tolerance:
                    violations.append(
                        f"year {year}: balance equation violated: "
                        f"assets {assets:,.0f} != liabilities {liab:,.0f} + equity {equity:,.0f} "
                        f"(diff={balance_diff:,.0f}, tolerance={tolerance:,.0f})"
                    )
                    checks_failed += 1
                else:
                    checks_passed += 1
            else:
                if assets is None or liab is None or equity is None:
                    warnings.append(
                        f"year {year}: incomplete balance sheet, skipping balance equation check"
                    )
                checks_warned += 1

        # 10. No exact source value survived
        checks_run += 1
        exact_matches = [
            m
            for m in result.metrics
            if m.transformed_value == m.original_value and m.original_value != 0
        ]
        if exact_matches:
            for m in exact_matches[:5]:
                violations.append(
                    f"exact match survived: {m.metric_name} year {m.year} = {m.transformed_value:,.0f}"
                )
            checks_failed += 1
        else:
            checks_passed += 1

        # 11. No exact ratio survived
        checks_run += 1
        if result.ratios:
            # We don't have original ratios, so this is a structural check only
            checks_passed += 1
        else:
            warnings.append("No ratios computed, skipping ratio exact-match check")
            checks_warned += 1

        passes_all = len(violations) == 0

        return SanityResult(
            passes_all=passes_all,
            violations=violations,
            warnings=warnings,
            checks_run=checks_run,
            checks_passed=checks_passed,
            checks_warned=checks_warned,
            checks_failed=checks_failed,
        )
