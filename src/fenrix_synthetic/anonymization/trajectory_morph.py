"""Price trajectory morphing — minimal stub for test compatibility.

Full implementation deferred to Phase 6 per V3_ARCHITECTURE_AUDIT.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TrajectoryMorphConfig:
    """Configuration for trajectory morphing."""

    seed: int = 42
    epsilon: float = 0.01


@dataclass
class TrajectoryMorphResult:
    """Result of trajectory morphing."""

    source_prices: list[float]
    morphed_prices: list[float]
    source_returns: list[float] = field(default_factory=list)
    morphed_returns: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Compute returns from prices."""
        if not self.source_returns:
            self.source_returns = self._compute_returns(self.source_prices)
        if not self.morphed_returns:
            self.morphed_returns = self._compute_returns(self.morphed_prices)

    @staticmethod
    def _compute_returns(prices: list[float]) -> list[float]:
        return [
            (prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(1, len(prices))
        ]


class TrajectoryMorpher:
    """Minimal trajectory morpher — deterministic placeholder."""

    def __init__(self, config: TrajectoryMorphConfig) -> None:
        self.config = config

    def morph(
        self,
        company_id: str,
        dates: list[str],
        prices: list[float],
    ) -> TrajectoryMorphResult:
        """Apply a small deterministic perturbation to prices."""
        import hashlib

        seed_bytes = f"{company_id}:{self.config.seed}".encode()
        seed_hash = hashlib.sha256(seed_bytes).digest()
        seed_int = int.from_bytes(seed_hash[:8], "big")

        # Simple perturbation: multiply by 1 + small noise
        morphed_prices = []
        for i, p in enumerate(prices):
            noise = ((seed_int * (i + 1) * 73) % 20000 - 10000) / 10000.0
            perturbation = 1.0 + noise * self.config.epsilon
            morphed_prices.append(round(p * perturbation, 2))
        return TrajectoryMorphResult(
            source_prices=list(prices),
            morphed_prices=morphed_prices,
        )


def write_public_price_series(
    output_dir: str,
    company_id: str,
    result: TrajectoryMorphResult,
) -> dict[str, str]:
    """Write public price series CSV and return summary MD."""
    out = Path(output_dir)
    market_dir = out / "anonymized" / company_id / "market"
    market_dir.mkdir(parents=True, exist_ok=True)

    csv_path = market_dir / "price_series.csv"
    csv_path.write_text("\n".join(
        ["date,price"]
        + [f"day_{i},{p}" for i, p in enumerate(result.morphed_prices)]
    ) + "\n")

    md_path = market_dir / "return_summary.md"
    start_price = result.morphed_prices[0]
    end_price = result.morphed_prices[-1]
    total_return = (end_price - start_price) / start_price * 100
    md_path.write_text(
        "# Return Summary\n\n"
        f"Start price: {start_price:.2f}\n"
        f"End price: {end_price:.2f}\n"
        f"Total return: {total_return:.1f}%\n"
        f"Observations: {len(result.morphed_prices)}\n"
    )

    return {"csv": str(csv_path), "md": str(md_path)}


def write_private_trajectory_audit(
    output_dir: str,
    company_id: str,
    result: TrajectoryMorphResult,
) -> str:
    """Write private trajectory audit (not included in public ZIP)."""
    out = Path(output_dir)
    qa_dir = out / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)

    audit_path = qa_dir / "trajectory_morph_audit.json"
    audit_path.write_text(json.dumps({
        "company_id": company_id,
        "num_source_observations": len(result.source_returns),
        "num_morphed_observations": len(result.morphed_returns),
        "max_perturbation_pct": max(
            abs((m - s) / s) * 100 if s != 0 else 0
            for s, m in zip(result.source_prices, result.morphed_prices, strict=False)
        ),
    }, indent=2))
    return str(audit_path)
