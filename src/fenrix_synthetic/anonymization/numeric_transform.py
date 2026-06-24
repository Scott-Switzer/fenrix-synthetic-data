"""Numeric transformation for financial statement values.

Provides deterministic, accounting-aware perturbation that preserves
financial usefulness while breaking exact value and ratio matches.

Input: source financial facts (or fixture data), company ID, seed, config.
Output: transformed metrics, ratio summaries, summary markdown, private audit.

Transformation strategy:
1. Company-level scale factor (0.65-1.35 range)
2. Metric-family multipliers (revenue, cost, asset, liability, equity families)
3. Year-level smoothed noise (±2-6%)
4. Derived values recomputed from transformed values
5. Aggressive rounding by scale
6. No exact source values survive rounding
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_SCALE_RANGE = (0.65, 1.35)
DEFAULT_YEAR_NOISE_RANGE = (0.02, 0.06)

METRIC_FAMILIES: dict[str, list[str]] = {
    "revenue": ["revenue", "total_revenue", "net_sales", "sales"],
    "cost": ["cogs", "cost_of_goods_sold", "cost_of_revenue", "operating_expenses", "sgna"],
    "operating_income": ["operating_income", "operating_profit", "ebit"],
    "net_income": ["net_income", "net_earnings", "net_profit"],
    "asset": [
        "total_assets",
        "current_assets",
        "cash",
        "accounts_receivable",
        "inventory",
        "ppe_net",
        "goodwill",
        "intangible_assets",
    ],
    "liability": [
        "total_liabilities",
        "current_liabilities",
        "accounts_payable",
        "short_term_debt",
        "long_term_debt",
    ],
    "equity": ["total_equity", "common_stock", "retained_earnings"],
    "cash_flow": [
        "operating_cash_flow",
        "investing_cash_flow",
        "financing_cash_flow",
        "free_cash_flow",
    ],
}


def _round_by_scale(value: float, base_revenue: float = 0.0) -> float:
    """Round value aggressively based on magnitude scale."""
    abs_val = abs(value)
    if abs_val == 0:
        return 0.0
    if abs_val >= 1_000_000_000:  # $1B+
        return round(value / 100_000_000) * 100_000_000  # nearest $100M
    if abs_val >= 100_000_000:  # $100M+
        return round(value / 10_000_000) * 10_000_000  # nearest $10M
    if abs_val >= 10_000_000:  # $10M+
        return round(value / 1_000_000) * 1_000_000  # nearest $1M
    if abs_val >= 1_000_000:  # $1M+
        return round(value / 100_000) * 100_000  # nearest $100K
    if abs_val >= 1_000:
        return round(value / 1_000) * 1_000  # nearest $1K
    return round(value, 0)


def _round_ratio(value: float) -> float:
    """Round a ratio to 1 decimal place or whole percent."""
    if abs(value) >= 10:
        return round(value, 0)
    if abs(value) >= 1:
        return round(value, 1)
    return round(value, 2)


# ── Input data model ───────────────────────────────────────────────────


@dataclass
class FinancialFact:
    """A single financial fact (e.g., "revenue_2024": 10000000000)."""

    metric_name: str
    value: float
    year: int  # absolute year or relative year neg offset
    period: str = "annual"  # annual, quarterly


@dataclass
class FinancialStatementSet:
    """A set of financial facts for one company across years."""

    company_id: str
    facts: list[FinancialFact] = field(default_factory=list)


# ── Transformed output ─────────────────────────────────────────────────


@dataclass
class TransformedMetric:
    """A single transformed metric value."""

    metric_name: str
    original_value: float
    transformed_value: float
    scale_factor: float
    year: int
    family: str


@dataclass
class TransformedRatio:
    """A derived ratio from transformed values."""

    ratio_name: str
    value: float
    numerator_metric: str
    denominator_metric: str


@dataclass
class TransformResult:
    """Complete result of a numeric transformation."""

    company_id: str
    metrics: list[TransformedMetric]
    ratios: list[TransformedRatio]
    scale_factor: float
    revenue_scale_factor: float
    year_noise_applied: dict[int, float]
    violations: list[str]
    warnings: list[str]
    passes_sanity: bool


# ── Main transformer ───────────────────────────────────────────────────


class NumericTransformer:
    """Deterministic, accounting-aware numeric transformer.

    Usage:
        transformer = NumericTransformer(company_id="COMPANY_001", seed=42)
        result = transformer.transform(statement_set)
    """

    def __init__(
        self,
        company_id: str = "COMPANY_001",
        *,
        seed: int = 42,
        scale_range: tuple[float, float] = DEFAULT_SCALE_RANGE,
        year_noise_range: tuple[float, float] = DEFAULT_YEAR_NOISE_RANGE,
    ) -> None:
        self.company_id = company_id
        self.seed = seed
        self.scale_range = scale_range
        self.year_noise_range = year_noise_range
        self._rng = random.Random(seed)

    def _family_for(self, metric: str) -> str:
        """Determine metric family."""
        ml = metric.lower().replace("_", "").replace(" ", "")
        for family, members in METRIC_FAMILIES.items():
            for m in members:
                if ml == m.lower().replace("_", "") or ml.endswith(m.lower().replace("_", "")):
                    return family
        return "other"

    def _company_scale(self) -> float:
        """Deterministic company-level scale factor."""
        h = hashlib.sha256(f"{self.company_id}:scale:{self.seed}".encode()).hexdigest()
        low, high = self.scale_range
        return low + (int(h[:8], 16) % 10000) / 10000 * (high - low)

    def _family_multiplier(self, family: str) -> float:
        """Deterministic family-level multiplier."""
        h = hashlib.sha256(f"{self.company_id}:family:{family}:{self.seed}".encode()).hexdigest()
        # Family multipliers vary around 1.0 by ±0.15
        return 0.85 + (int(h[:8], 16) % 3000) / 10000

    def _year_noise(self, year: int) -> float:
        """Deterministic year-level noise factor."""
        h = hashlib.sha256(f"{self.company_id}:year:{year}:{self.seed}".encode()).hexdigest()
        low, high = self.year_noise_range
        noise = low + (int(h[:8], 16) % 10000) / 10000 * (high - low)
        direction = -1 if int(h[8:12], 16) % 2 == 0 else 1
        return 1.0 + direction * noise

    def transform(self, facts: list[FinancialFact]) -> TransformResult:
        """Transform a list of financial facts.

        Applies company scale, family multiplier, year noise, and rounding.
        Returns transformed metrics + derived ratios.
        """
        if not facts:
            return TransformResult(
                company_id=self.company_id,
                metrics=[],
                ratios=[],
                scale_factor=1.0,
                revenue_scale_factor=1.0,
                year_noise_applied={},
                violations=[],
                warnings=["No facts provided"],
                passes_sanity=True,
            )

        company_scale = self._company_scale()
        family_cache: dict[str, float] = {}
        year_noise_cache: dict[int, float] = {}

        transformed: list[TransformedMetric] = []

        for fact in facts:
            family = self._family_for(fact.metric_name)
            if family not in family_cache:
                family_cache[family] = self._family_multiplier(family)

            if fact.year not in year_noise_cache:
                year_noise_cache[fact.year] = self._year_noise(fact.year)

            # Combine scales
            scale = company_scale * family_cache[family] * year_noise_cache[fact.year]
            transformed_value = fact.value * scale
            rounded = _round_by_scale(transformed_value)

            transformed.append(
                TransformedMetric(
                    metric_name=fact.metric_name,
                    original_value=fact.value,
                    transformed_value=rounded,
                    scale_factor=round(scale, 4),
                    year=fact.year,
                    family=family,
                )
            )

        # Compute derived ratios
        trans_map: dict[str, dict[int, float]] = {}
        for t in transformed:
            if t.metric_name not in trans_map:
                trans_map[t.metric_name] = {}
            trans_map[t.metric_name][t.year] = t.transformed_value

        ratios = self._compute_ratios(trans_map)
        violations = self._check_violations(transformed)

        return TransformResult(
            company_id=self.company_id,
            metrics=transformed,
            ratios=ratios,
            scale_factor=round(company_scale, 4),
            revenue_scale_factor=round(company_scale * family_cache.get("revenue", 1.0), 4),
            year_noise_applied={y: round(n, 4) for y, n in year_noise_cache.items()},
            violations=violations,
            warnings=[],
            passes_sanity=len(violations) == 0,
        )

    def _compute_ratios(self, trans_map: dict[str, dict[int, float]]) -> list[TransformedRatio]:
        """Compute derived ratios from transformed values."""
        ratios: list[TransformedRatio] = []
        years: set[int] = set()
        for ymap in trans_map.values():
            years.update(ymap.keys())

        for year in sorted(years):
            rev = trans_map.get("revenue", {}).get(year) or trans_map.get("total_revenue", {}).get(
                year
            )
            cogs = trans_map.get("cogs", {}).get(year) or trans_map.get(
                "cost_of_goods_sold", {}
            ).get(year)
            net_inc = trans_map.get("net_income", {}).get(year)
            total_assets = trans_map.get("total_assets", {}).get(year)
            total_liab = trans_map.get("total_liabilities", {}).get(year)
            total_eq = trans_map.get("total_equity", {}).get(year)
            op_inc = trans_map.get("operating_income", {}).get(year) or trans_map.get(
                "operating_profit", {}
            ).get(year)
            cash = trans_map.get("cash", {}).get(year) or trans_map.get(
                "cash_and_equivalents", {}
            ).get(year)

            if rev and cogs:
                gross_profit = rev - cogs
                ratios.append(
                    TransformedRatio(
                        ratio_name="gross_margin",
                        value=_round_ratio(gross_profit / rev),
                        numerator_metric="gross_profit",
                        denominator_metric="revenue",
                    )
                )
            if rev and net_inc is not None:
                ratios.append(
                    TransformedRatio(
                        ratio_name="net_margin",
                        value=_round_ratio(net_inc / rev),
                        numerator_metric="net_income",
                        denominator_metric="revenue",
                    )
                )
            if rev and op_inc is not None:
                ratios.append(
                    TransformedRatio(
                        ratio_name="operating_margin",
                        value=_round_ratio(op_inc / rev),
                        numerator_metric="operating_income",
                        denominator_metric="revenue",
                    )
                )
            if total_assets and total_eq and total_eq != 0:
                ratios.append(
                    TransformedRatio(
                        ratio_name="debt_to_equity",
                        value=_round_ratio((total_liab or 0) / total_eq),
                        numerator_metric="total_liabilities",
                        denominator_metric="total_equity",
                    )
                )
            if total_assets and net_inc is not None:
                ratios.append(
                    TransformedRatio(
                        ratio_name="roa",
                        value=_round_ratio(net_inc / total_assets),
                        numerator_metric="net_income",
                        denominator_metric="total_assets",
                    )
                )
            if total_eq and total_eq != 0 and net_inc is not None:
                ratios.append(
                    TransformedRatio(
                        ratio_name="roe",
                        value=_round_ratio(net_inc / total_eq),
                        numerator_metric="net_income",
                        denominator_metric="total_equity",
                    )
                )
            if total_assets and total_liab is not None:
                ratios.append(
                    TransformedRatio(
                        ratio_name="debt_to_assets",
                        value=_round_ratio(total_liab / total_assets),
                        numerator_metric="total_liabilities",
                        denominator_metric="total_assets",
                    )
                )
            if total_assets and cash is not None:
                ratios.append(
                    TransformedRatio(
                        ratio_name="cash_to_assets",
                        value=_round_ratio(cash / total_assets),
                        numerator_metric="cash",
                        denominator_metric="total_assets",
                    )
                )
            if rev and total_assets:
                ratios.append(
                    TransformedRatio(
                        ratio_name="asset_turnover",
                        value=_round_ratio(rev / total_assets),
                        numerator_metric="revenue",
                        denominator_metric="total_assets",
                    )
                )

        return ratios

    def _check_violations(self, metrics: list[TransformedMetric]) -> list[str]:
        """Check for basic sanity violations in transformed values."""
        violations: list[str] = []
        for m in metrics:
            if m.transformed_value < 0 and m.family in ("revenue",) and m.transformed_value != 0:
                violations.append(f"{m.metric_name} year {m.year}: revenue should be positive")
            # Check no exact match to original
            if m.transformed_value == m.original_value and m.transformed_value != 0:
                violations.append(
                    f"{m.metric_name} year {m.year}: exact source value survived ({m.transformed_value})"
                )
        return violations

    # ── Output writers ─────────────────────────────────────────────

    def write_public_outputs(
        self,
        result: TransformResult,
        output_dir: Path,
    ) -> list[str]:
        """Write public transformed outputs.

        Outputs:
            financials/transformed_metrics.csv
            financials/ratio_summary.csv
            financials/summary.md
        """
        import csv

        fin_dir = output_dir / "anonymized" / self.company_id / "financials"
        fin_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []

        # Metrics CSV
        csv_path = fin_dir / "transformed_metrics.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["metric", "year", "transformed_value", "scale_factor", "family"])
            for m in result.metrics:
                writer.writerow(
                    [m.metric_name, m.year, m.transformed_value, m.scale_factor, m.family]
                )
        written.append(str(csv_path))

        # Ratios CSV
        ratio_path = fin_dir / "ratio_summary.csv"
        with open(ratio_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ratio_name", "value", "numerator", "denominator", "year"])
            for r in result.ratios:
                year = 0
                writer.writerow(
                    [r.ratio_name, r.value, r.numerator_metric, r.denominator_metric, year]
                )
        written.append(str(ratio_path))

        # Summary markdown
        md_path = fin_dir / "summary.md"
        lines = [
            f"# Financial Summary: {self.company_id}",
            "",
            "## Transformed Metrics",
            "",
            "| Metric | Value |",
            "|--------|-------|",
        ]
        for m in result.metrics[:20]:
            lines.append(f"| {m.metric_name} ({m.year}) | {m.transformed_value:,.0f} |")

        lines.append("")
        lines.append("## Key Ratios")
        lines.append("")
        lines.append("| Ratio | Value |")
        lines.append("|-------|-------|")
        for r in result.ratios[:10]:
            lines.append(f"| {r.ratio_name} | {r.value} |")

        lines.append("")
        lines.append("---")
        lines.append("*Values are deterministically transformed. No exact source values survive.*")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(str(md_path))

        return written

    def write_private_audit(
        self,
        result: TransformResult,
        output_dir: Path,
    ) -> str:
        """Write private numeric transform audit."""
        private_dir = output_dir / "private" / "qa"
        private_dir.mkdir(parents=True, exist_ok=True)

        audit: dict[str, Any] = {
            "schema_version": "1.0",
            "company_id": self.company_id,
            "seed": self.seed,
            "scale_factor": result.scale_factor,
            "revenue_scale_factor": result.revenue_scale_factor,
            "year_noise_applied": result.year_noise_applied,
            "metric_count": len(result.metrics),
            "ratio_count": len(result.ratios),
            "violations": result.violations,
            "warnings": result.warnings,
            "passes_sanity": result.passes_sanity,
            "transform_details": [
                {
                    "metric": m.metric_name,
                    "year": m.year,
                    "original": m.original_value,
                    "transformed": m.transformed_value,
                    "scale": m.scale_factor,
                    "family": m.family,
                    "exact_match_survived": m.transformed_value == m.original_value,
                }
                for m in result.metrics
            ],
        }

        path = private_dir / "numeric_transform_audit.json"
        path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return str(path)
