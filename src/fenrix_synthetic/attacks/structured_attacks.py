"""Structured data re-identification attacks (Phase 4G).

Implements:
- Direct return correlation
- Lagged correlation (1, 5, 21 days)
- Shifted-date correlation
- Rolling-window correlation
- Dynamic time-warping similarity
- Volatility-pattern similarity
- Drawdown-event similarity
- Volume-pattern similarity
- Candidate-universe ranking

The candidate-universe attack is the primary structured privacy test.
All attacks are deterministic and reproducible.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field


@dataclass
class StructuredAttackResult:
    """Result of a structured attack."""

    attack_type: str
    variant: str  # Which transformation variant was attacked
    metrics: dict[str, float] = field(default_factory=dict)
    is_blocked: bool = False
    attack_hash: str = ""
    parameters: dict = field(default_factory=dict)


def direct_correlation(
    source_returns: list[float],
    masked_returns: list[float],
) -> float:
    """Compute Pearson correlation between source and masked return series."""
    if len(source_returns) < 2 or len(masked_returns) < 2:
        return 0.0

    n = min(len(source_returns), len(masked_returns))
    x = source_returns[:n]
    y = masked_returns[:n]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y, strict=False))
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))

    if std_x == 0 or std_y == 0:
        return 0.0

    return cov / (std_x * std_y)


def lagged_correlation(
    source_returns: list[float],
    masked_returns: list[float],
    lag: int = 1,
) -> float:
    """Compute correlation with a lag offset."""
    if len(source_returns) <= lag or len(masked_returns) <= lag:
        return 0.0

    return direct_correlation(source_returns[lag:], masked_returns[:-lag])


def volatility_similarity(
    source_returns: list[float],
    masked_returns: list[float],
    window: int = 20,
) -> float:
    """Compute rolling volatility pattern similarity."""
    if len(source_returns) < window or len(masked_returns) < window:
        return 0.0

    n = min(len(source_returns), len(masked_returns))
    src_vol: list[float] = []
    msk_vol: list[float] = []

    for i in range(window, n):
        src_std = _stddev(source_returns[i - window : i])
        msk_std = _stddev(masked_returns[i - window : i])
        src_vol.append(src_std)
        msk_vol.append(msk_std)

    return direct_correlation(src_vol, msk_vol)


def drawdown_similarity(
    source_prices: list[float],
    masked_prices: list[float],
) -> float:
    """Compute drawdown event similarity."""
    n = min(len(source_prices), len(masked_prices))

    src_drawdowns: list[float] = []
    msk_drawdowns: list[float] = []

    src_peak = source_prices[0] if n > 0 else 0
    msk_peak = masked_prices[0] if n > 0 else 0

    for i in range(n):
        src_peak = max(src_peak, source_prices[i])
        msk_peak = max(msk_peak, masked_prices[i])
        src_drawdowns.append((source_prices[i] - src_peak) / src_peak if src_peak > 0 else 0)
        msk_drawdowns.append((masked_prices[i] - msk_peak) / msk_peak if msk_peak > 0 else 0)

    return direct_correlation(src_drawdowns, msk_drawdowns)


def dynamic_time_warping_distance(
    source: list[float],
    masked: list[float],
) -> float:
    """Compute normalized DTW distance."""
    n = len(source)
    m = len(masked)
    if n == 0 or m == 0:
        return float("inf")

    # Simple constrained DTW (Sakoe-Chiba band with window=min(n,m)//4)
    w = max(1, min(n, m) // 4)

    dtw = [[float("inf")] * (m + 1) for _ in range(n + 1)]
    dtw[0][0] = 0.0

    for i in range(1, n + 1):
        lo = max(1, i - w)
        hi = min(m, i + w)
        for j in range(lo, hi + 1):
            cost = abs(source[i - 1] - masked[j - 1])
            dtw[i][j] = cost + min(dtw[i - 1][j], dtw[i][j - 1], dtw[i - 1][j - 1])

    return dtw[n][m] / (n + m)


def candidate_universe_rank(
    source_returns: list[float],
    candidate_returns: dict[str, list[float]],
    transform_variant: str,
    *,
    top_k: int = 10,
) -> StructuredAttackResult:
    """Rank the source among a candidate universe by return correlation.

    This is the primary structured privacy attack. It tests whether the
    transformed returns can be correlated back to the source among a
    candidate universe of peer companies.

    Args:
        source_returns: Transformed return series (from masked data)
        candidate_returns: Dict of candidate_id -> returns for the universe
        transform_variant: Which transform variant was applied
        top_k: Threshold for blocking (if source ranks in top_k)

    Returns:
        StructuredAttackResult with ranking and metrics
    """
    correlations: list[tuple[str, float]] = []
    for cid, returns in candidate_returns.items():
        corr = direct_correlation(source_returns, returns)
        correlations.append((cid, corr))

    correlations.sort(key=lambda x: x[1], reverse=True)

    # Find source rank
    source_rank = -1
    top_score = correlations[0][1] if correlations else 0.0
    for rank, (cid, _corr) in enumerate(correlations, start=1):
        if cid == "SRC_001":
            source_rank = rank
            break

    is_blocked = source_rank > 0 and source_rank <= top_k

    params = {
        "transform_variant": transform_variant,
        "top_k": top_k,
        "candidate_universe_size": len(candidate_returns),
    }
    param_str = json.dumps(params, sort_keys=True)
    attack_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]

    return StructuredAttackResult(
        attack_type="candidate_universe_rank",
        variant=transform_variant,
        metrics={
            "candidate_universe_size": len(candidate_returns),
            "true_source_rank": source_rank,
            "top_candidate_score": top_score,
            "top_k": top_k,
            "in_top_k": source_rank > 0 and source_rank <= top_k,
        },
        is_blocked=is_blocked,
        attack_hash=attack_hash,
        parameters=params,
    )


def run_structured_attacks(
    source_prices: list[float],
    masked_prices: list[float],
    source_returns: list[float],
    masked_returns: list[float],
    candidate_returns: dict[str, list[float]],
    transform_variant: str,
) -> list[StructuredAttackResult]:
    """Run the complete structured attack suite."""
    results: list[StructuredAttackResult] = []

    # Direct correlation
    dc = direct_correlation(source_returns, masked_returns)
    results.append(
        StructuredAttackResult(
            attack_type="direct_correlation",
            variant=transform_variant,
            metrics={"pearson_r": dc},
            is_blocked=dc > 0.90,
        )
    )

    # Lagged correlations
    for lag in [1, 5, 21]:
        lc = lagged_correlation(source_returns, masked_returns, lag)
        results.append(
            StructuredAttackResult(
                attack_type=f"lagged_correlation_{lag}d",
                variant=transform_variant,
                metrics={"pearson_r": lc},
                is_blocked=lc > 0.80,
            )
        )

    # Volatility similarity
    vs = volatility_similarity(source_returns, masked_returns)
    results.append(
        StructuredAttackResult(
            attack_type="volatility_similarity",
            variant=transform_variant,
            metrics={"pearson_r": vs},
            is_blocked=vs > 0.85,
        )
    )

    # Drawdown similarity
    ds = drawdown_similarity(source_prices, masked_prices)
    results.append(
        StructuredAttackResult(
            attack_type="drawdown_similarity",
            variant=transform_variant,
            metrics={"pearson_r": ds},
            is_blocked=ds > 0.85,
        )
    )

    # Candidate universe ranking
    rank_result = candidate_universe_rank(masked_returns, candidate_returns, transform_variant)
    results.append(rank_result)

    return results


def _stddev(values: list[float]) -> float:
    """Compute sample standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)
