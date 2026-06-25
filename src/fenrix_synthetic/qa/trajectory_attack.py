"""Trajectory re-identification attack — minimal stub for test compatibility.

Full implementation deferred to Phase 6 per V3_ARCHITECTURE_AUDIT.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TrajectoryAttackConfig:
    """Configuration for trajectory attack."""

    exact_return_match_threshold: float = 0.001


@dataclass
class TrajectoryAttackResult:
    """Result of trajectory attack."""

    exact_return_match_count: int = 0
    passes: bool = True


class TrajectoryAttack:
    """Detect if exact source returns survive morphing."""

    def __init__(self, config: TrajectoryAttackConfig) -> None:
        self.config = config

    def run(
        self,
        source_returns: list[float],
        morphed_returns: list[float],
    ) -> TrajectoryAttackResult:
        """Compare source and morphed returns for exact matches."""
        match_count = 0
        for s, m in zip(source_returns, morphed_returns, strict=False):
            if abs(s - m) <= self.config.exact_return_match_threshold:
                match_count += 1

        return TrajectoryAttackResult(
            exact_return_match_count=match_count,
            passes=match_count == 0,
        )


def write_trajectory_attack_summary(
    output_dir: str,
    company_id: str,
    attack_result: TrajectoryAttackResult,
) -> str:
    """Write trajectory attack summary to QA directory."""
    out = Path(output_dir)
    summary_path = out / "trajectory_attack_summary.json"
    summary_path.write_text(json.dumps({
        "company_id": company_id,
        "exact_return_match_count": attack_result.exact_return_match_count,
        "passes": attack_result.passes,
    }, indent=2))
    return str(summary_path)
