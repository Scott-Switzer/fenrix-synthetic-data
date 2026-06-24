"""Exact-number attack: compare source/private facts to public transformed facts.

This QA module checks whether any exact source values or ratios survived
the numeric transformation pipeline. It is designed to run against private
source data and public output, reporting matches without exposing source
identity in public outputs.

Usage:
    from .exact_number_attack import ExactNumberAttack, AttackConfig
    attack = ExactNumberAttack(config)
    report = attack.run(source_facts, public_facts)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..anonymization.numeric_transform import FinancialFact


@dataclass
class AttackConfig:
    """Configuration for exact-number attack thresholds."""

    exact_value_matches_allowed: int = 0
    exact_ratio_matches_allowed: int = 0
    near_match_relative_tolerance: float = 0.001
    near_match_warning_threshold: int = 3


@dataclass
class NearMatch:
    """A near-match between source and public values."""

    metric_name: str
    year: int
    source_value: float
    public_value: float
    relative_diff: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "year": self.year,
            "source_value": self.source_value,
            "public_value": self.public_value,
            "relative_diff": round(self.relative_diff, 6),
        }


@dataclass
class AttackReport:
    """Report from an exact-number attack run."""

    exact_value_matches: int = 0
    exact_ratio_matches: int = 0
    near_matches: list[NearMatch] = field(default_factory=list)
    source_value_count: int = 0
    public_value_count: int = 0
    passed: bool = True
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exact_value_matches": self.exact_value_matches,
            "exact_ratio_matches": self.exact_ratio_matches,
            "near_matches": [m.to_dict() for m in self.near_matches],
            "source_value_count": self.source_value_count,
            "public_value_count": self.public_value_count,
            "passed": self.passed,
            "violations": self.violations,
        }


class ExactNumberAttack:
    """Run exact-number attack comparing source facts to public output."""

    def __init__(self, config: AttackConfig | None = None) -> None:
        self.config = config or AttackConfig()

    def run(
        self,
        source_facts: list[FinancialFact],
        public_facts: list[FinancialFact],
    ) -> AttackReport:
        """Compare source facts to public facts and report matches.

        Args:
            source_facts: Original/private financial facts.
            public_facts: Publicly released transformed financial facts.

        Returns:
            AttackReport with match counts and pass/fail status.
        """
        report = AttackReport()
        report.source_value_count = len(source_facts)
        report.public_value_count = len(public_facts)

        # Build lookup: (metric_name, year) -> value
        public_map: dict[tuple[str, int], float] = {}
        for pf in public_facts:
            public_map[(pf.metric_name, pf.year)] = pf.value

        # Check exact and near matches for values
        for sf in source_facts:
            key = (sf.metric_name, sf.year)
            if key in public_map:
                pub_val = public_map[key]
                if pub_val == sf.value and sf.value != 0:
                    report.exact_value_matches += 1
                    report.violations.append(
                        f"EXACT VALUE MATCH: {sf.metric_name} year {sf.year} = {sf.value:,.0f}"
                    )
                else:
                    rel_diff = abs(pub_val - sf.value) / max(abs(sf.value), 1.0)
                    if rel_diff < self.config.near_match_relative_tolerance:
                        report.near_matches.append(
                            NearMatch(
                                metric_name=sf.metric_name,
                                year=sf.year,
                                source_value=sf.value,
                                public_value=pub_val,
                                relative_diff=rel_diff,
                            )
                        )

        # Check exact ratio matches (simple: same metric name + "_ratio" suffix)
        ratio_public: dict[tuple[str, int], float] = {
            k: v for k, v in public_map.items() if "ratio" in k[0].lower()
        }
        ratio_source: dict[tuple[str, int], float] = {
            (sf.metric_name, sf.year): sf.value
            for sf in source_facts
            if "ratio" in sf.metric_name.lower()
        }
        for key, src_val in ratio_source.items():
            if key in ratio_public and ratio_public[key] == src_val:
                report.exact_ratio_matches += 1
                report.violations.append(f"EXACT RATIO MATCH: {key[0]} year {key[1]} = {src_val}")

        # Determine pass/fail
        report.passed = (
            report.exact_value_matches <= self.config.exact_value_matches_allowed
            and report.exact_ratio_matches <= self.config.exact_ratio_matches_allowed
            and len(report.near_matches) < self.config.near_match_warning_threshold
        )

        if len(report.near_matches) >= self.config.near_match_warning_threshold:
            report.violations.append(
                f"NEAR-MATCH WARNING: {len(report.near_matches)} near-matches found "
                f"(threshold={self.config.near_match_warning_threshold})"
            )

        return report

    def run_from_transform_result(
        self,
        source_facts: list[FinancialFact],
        transform_result: Any,
    ) -> AttackReport:
        """Convenience: run attack using a TransformResult's metrics.

        Args:
            source_facts: Original source facts.
            transform_result: A TransformResult with .metrics list.

        Returns:
            AttackReport.
        """
        from ..anonymization.numeric_transform import TransformResult

        if not isinstance(transform_result, TransformResult):
            raise TypeError("transform_result must be a TransformResult")

        public_facts = [
            FinancialFact(
                metric_name=m.metric_name,
                value=m.transformed_value,
                year=m.year,
                period="annual",
            )
            for m in transform_result.metrics
        ]
        return self.run(source_facts, public_facts)


def write_attack_report(report: AttackReport, path: Path) -> None:
    """Write attack report to JSON."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
