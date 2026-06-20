"""Feature-only utility evaluation (Phase 5A).

Measures whether released categorical features support the intended
decision task without requiring price path reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FeatureUtilityResult:
    """Result of feature-only utility evaluation."""

    variant: str
    directional_classification_agreement: float = 0.0
    trend_regime_agreement: float = 0.0
    momentum_state_agreement: float = 0.0
    moving_average_state_agreement: float = 0.0
    volatility_regime_agreement: float = 0.0
    drawdown_regime_agreement: float = 0.0
    market_relative_state_agreement: float = 0.0
    sector_relative_state_agreement: float = 0.0
    missing_data_rate: float = 0.0
    usable_period_retention: float = 0.0
    overall_utility: float = 0.0
    feature_stability: float = 0.0
    binary_decision_agreement: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _categorical_agreement(
    source_seq: list[str],
    masked_seq: list[str],
) -> float:
    """Fraction of periods where categories match exactly."""
    n = min(len(source_seq), len(masked_seq))
    if n == 0:
        return 0.0
    matches = sum(1 for i in range(n) if source_seq[i] == masked_seq[i])
    return matches / n


def evaluate_feature_utility(
    source_features: list[dict[str, Any]],
    masked_features: list[dict[str, Any]],
    variant: str = "",
) -> FeatureUtilityResult:
    """Evaluate utility for feature-only data.

    Compares categorical features from source vs masked/released data.
    Measures agreement on direction, trend, momentum, MA state,
    volatility, drawdown, and market-relative state.
    """
    result = FeatureUtilityResult(variant=variant)

    if not source_features or not masked_features:
        result.warnings.append("Empty source or masked features")
        result.overall_utility = 0.0
        return result

    n_src = len(source_features)
    n_msk = len(masked_features)
    result.usable_period_retention = min(n_src, n_msk) / max(1, n_src)
    result.missing_data_rate = 1.0 - result.usable_period_retention

    feature_pairs = [
        ("return_direction", "directional_classification_agreement"),
        ("weekly_direction_category", "directional_classification_agreement"),
        ("dominant_trend_regime", "trend_regime_agreement"),
        ("momentum_5d_bucket", "momentum_state_agreement"),
        ("momentum_21d_bucket", "momentum_state_agreement"),
        ("momentum_4w_bucket", "momentum_state_agreement"),
        ("momentum_12w_bucket", "momentum_state_agreement"),
        ("moving_average_state", "moving_average_state_agreement"),
        ("moving_average_regime", "moving_average_state_agreement"),
        ("volatility_21d_bucket", "volatility_regime_agreement"),
        ("volatility_4w_bucket", "volatility_regime_agreement"),
        ("volatility_regime", "volatility_regime_agreement"),
        ("drawdown_bucket", "drawdown_regime_agreement"),
        ("drawdown_regime", "drawdown_regime_agreement"),
        ("market_relative_bucket", "market_relative_state_agreement"),
        ("market_relative_strength_bucket", "market_relative_state_agreement"),
        ("market_relative_regime", "market_relative_state_agreement"),
        ("sector_relative_bucket", "sector_relative_state_agreement"),
        ("sector_relative_strength_bucket", "sector_relative_state_agreement"),
    ]

    agreements: dict[str, list[float]] = {}
    for feature_key, metric_key in feature_pairs:
        src_vals = [f.get(feature_key, "MEDIUM") for f in source_features]
        msk_vals = [f.get(feature_key, "MEDIUM") for f in masked_features]
        agree = _categorical_agreement(src_vals, msk_vals)
        agreements.setdefault(metric_key, []).append(agree)

    # Average by metric
    for metric_key, values in agreements.items():
        avg = sum(values) / len(values)
        setattr(result, metric_key, round(avg, 4))

    # Calculate binary decision agreement using a simple threshold rule
    # Rule: trade if direction is UP AND momentum is not LOW
    src_decisions: list[int] = []
    msk_decisions: list[int] = []
    n = min(n_src, n_msk)
    for i in range(n):
        src_dir = source_features[i].get(
            "return_direction",
            source_features[i].get(
                "weekly_direction_category",
                source_features[i].get("dominant_trend_regime", "NEUTRAL"),
            ),
        )
        msk_dir = masked_features[i].get(
            "return_direction",
            masked_features[i].get(
                "weekly_direction_category",
                masked_features[i].get("dominant_trend_regime", "NEUTRAL"),
            ),
        )

        src_mom = source_features[i].get(
            "momentum_5d_bucket",
            source_features[i].get(
                "momentum_4w_bucket", source_features[i].get("aggregate_momentum_bucket", "MEDIUM")
            ),
        )
        msk_mom = masked_features[i].get(
            "momentum_5d_bucket",
            masked_features[i].get(
                "momentum_4w_bucket", masked_features[i].get("aggregate_momentum_bucket", "MEDIUM")
            ),
        )

        src_action = (
            1
            if src_dir in ("UP", "BULLISH", "STRONG_UP", "MILD_UP")
            and src_mom not in ("LOW", "VERY_LOW")
            else 0
        )
        msk_action = (
            1
            if msk_dir in ("UP", "BULLISH", "STRONG_UP", "MILD_UP")
            and msk_mom not in ("LOW", "VERY_LOW")
            else 0
        )
        src_decisions.append(src_action)
        msk_decisions.append(msk_action)

    if src_decisions:
        matches = sum(1 for i in range(n) if src_decisions[i] == msk_decisions[i])
        result.binary_decision_agreement = round(matches / n, 4)

    # Feature stability: how often individual features change between periods
    changes = 0
    total = 0
    for i in range(1, n_src):
        for key in source_features[i]:
            if key.startswith("relative_"):
                continue
            src_prev = source_features[i - 1].get(key, "MEDIUM")
            src_curr = source_features[i].get(key, "MEDIUM")
            if src_prev != src_curr:
                changes += 1
            total += 1
    result.feature_stability = round(changes / max(1, total), 4) if total > 0 else 0.0

    # Overall utility (weighted average of all agreements)
    weights = {
        "directional_classification_agreement": 0.25,
        "trend_regime_agreement": 0.15,
        "momentum_state_agreement": 0.15,
        "moving_average_state_agreement": 0.10,
        "volatility_regime_agreement": 0.10,
        "drawdown_regime_agreement": 0.05,
        "market_relative_state_agreement": 0.05,
        "sector_relative_state_agreement": 0.05,
        "binary_decision_agreement": 0.10,
    }

    total_weight = 0.0
    weighted_sum = 0.0
    for metric, weight in weights.items():
        val = getattr(result, metric, 0.0)
        weighted_sum += val * weight
        total_weight += weight

    result.overall_utility = round(weighted_sum / max(1, total_weight), 4)

    if result.usable_period_retention < 0.5:
        result.warnings.append(
            f"Usable period retention {result.usable_period_retention:.2f} below 0.50"
        )

    return result
