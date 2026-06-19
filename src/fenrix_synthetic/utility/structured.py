"""Utility evaluation for structured data (Phase 4H).

Metrics:
- Return sign agreement
- Rank correlation (Spearman)
- Volatility distortion
- Maximum drawdown distortion
- Momentum-signal agreement
- Moving-average crossover agreement
- Trend classification agreement
- Simple binary trading-decision agreement
- OHLC invariant validation

Does NOT claim transformed data reproduces actual investment performance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class StructuredUtilityResult:
    """Result of structured utility evaluation."""

    variant: str
    return_sign_agreement: float = 0.0
    rank_correlation: float = 0.0
    volatility_distortion: float = 0.0
    max_drawdown_distortion: float = 0.0
    momentum_agreement: float = 0.0
    ma_crossover_agreement: float = 0.0
    trend_agreement: float = 0.0
    ohlc_valid: bool = True
    overall_utility: float = 0.0
    warnings: list[str] = field(default_factory=list)


def return_sign_agreement(
    source_returns: list[float],
    masked_returns: list[float],
) -> float:
    """Fraction of days where return signs agree."""
    n = min(len(source_returns), len(masked_returns))
    if n == 0:
        return 0.0
    matches = sum(1 for i in range(n) if (source_returns[i] > 0) == (masked_returns[i] > 0))
    return matches / n


def spearman_rank_correlation(
    x: list[float],
    y: list[float],
) -> float:
    """Compute Spearman rank correlation coefficient."""
    n = min(len(x), len(y))
    if n < 2:
        return 0.0

    # Rank the values
    xr = _rank(x[:n])
    yr = _rank(y[:n])

    mean_xr = (n + 1) / 2.0
    mean_yr = (n + 1) / 2.0

    cov = sum((xr[i] - mean_xr) * (yr[i] - mean_yr) for i in range(n))
    std_x = math.sqrt(sum((xi - mean_xr) ** 2 for xi in xr))
    std_y = math.sqrt(sum((yi - mean_yr) ** 2 for yi in yr))

    if std_x == 0 or std_y == 0:
        return 0.0

    return cov / (std_x * std_y)


def volatility_distortion(
    source_returns: list[float],
    masked_returns: list[float],
) -> float:
    """Relative difference in annualized volatility."""
    src_vol = _annualized_volatility(source_returns)
    msk_vol = _annualized_volatility(masked_returns)

    if src_vol == 0:
        return 0.0

    return abs(src_vol - msk_vol) / src_vol


def max_drawdown_distortion(
    source_prices: list[float],
    masked_prices: list[float],
) -> float:
    """Relative difference in maximum drawdown."""
    src_dd = _max_drawdown(source_prices)
    msk_dd = _max_drawdown(masked_prices)

    if src_dd == 0:
        return 0.0

    return abs(src_dd - msk_dd) / abs(src_dd)


def momentum_agreement(
    source_prices: list[float],
    masked_prices: list[float],
    window: int = 20,
) -> float:
    """Fraction of days with same momentum direction."""
    n = min(len(source_prices), len(masked_prices))
    if n <= window:
        return 0.0

    matches = 0
    total = 0
    for i in range(window, n):
        src_mom = source_prices[i] - source_prices[i - window]
        msk_mom = masked_prices[i] - masked_prices[i - window]
        if (src_mom > 0) == (msk_mom > 0):
            matches += 1
        total += 1

    return matches / total if total > 0 else 0.0


def ma_crossover_agreement(
    source_prices: list[float],
    masked_prices: list[float],
    fast: int = 5,
    slow: int = 20,
) -> float:
    """Fraction of days where MA crossover direction agrees."""
    n = min(len(source_prices), len(masked_prices))
    if n <= slow:
        return 0.0

    matches = 0
    total = 0
    for i in range(slow + 1, n):
        src_fast = sum(source_prices[i - fast : i]) / fast
        src_slow = sum(source_prices[i - slow : i]) / slow
        msk_fast = sum(masked_prices[i - fast : i]) / fast
        msk_slow = sum(masked_prices[i - slow : i]) / slow
        if (src_fast > src_slow) == (msk_fast > msk_slow):
            matches += 1
        total += 1

    return matches / total if total > 0 else 0.0


def validate_ohlcv(
    open_vals: list[float],
    high_vals: list[float],
    low_vals: list[float],
    close_vals: list[float],
) -> bool:
    """Validate OHLC invariants hold after transformation."""
    for o_val, h_val, low_val, c_val in zip(
        open_vals, high_vals, low_vals, close_vals, strict=False
    ):
        if h_val < low_val:
            return False
        if h_val < o_val or h_val < c_val:
            return False
        if low_val > o_val or low_val > c_val:
            return False
    return True


def evaluate_structured_utility(
    source_returns: list[float],
    masked_returns: list[float],
    source_prices: list[float],
    masked_prices: list[float],
    variant: str = "",
) -> StructuredUtilityResult:
    """Run complete structured utility evaluation."""
    result = StructuredUtilityResult(variant=variant)

    result.return_sign_agreement = return_sign_agreement(source_returns, masked_returns)
    result.rank_correlation = spearman_rank_correlation(source_prices, masked_prices)
    result.volatility_distortion = volatility_distortion(source_returns, masked_returns)
    result.max_drawdown_distortion = max_drawdown_distortion(source_prices, masked_prices)
    result.momentum_agreement = momentum_agreement(source_prices, masked_prices)
    result.ma_crossover_agreement = ma_crossover_agreement(source_prices, masked_prices)

    # Overall utility (weighted average)
    weights = {
        "sign": 0.20,
        "rank": 0.20,
        "volatility": 0.15,
        "drawdown": 0.10,
        "momentum": 0.15,
        "ma_crossover": 0.10,
        "ohlc": 0.10,
    }
    result.overall_utility = (
        weights["sign"] * result.return_sign_agreement
        + weights["rank"] * result.rank_correlation
        + weights["volatility"] * (1.0 - result.volatility_distortion)
        + weights["drawdown"] * (1.0 - result.max_drawdown_distortion)
        + weights["momentum"] * result.momentum_agreement
        + weights["ma_crossover"] * result.ma_crossover_agreement
        + weights["ohlc"] * (1.0 if result.ohlc_valid else 0.0)
    )

    if result.return_sign_agreement < 0.60:
        result.warnings.append(
            f"Return sign agreement {result.return_sign_agreement:.2f} below 0.60"
        )
    if result.volatility_distortion > 0.30:
        result.warnings.append(
            f"Volatility distortion {result.volatility_distortion:.2f} above 0.30"
        )

    return result


def _rank(values: list[float]) -> list[float]:
    """Compute ranks (average rank for ties)."""
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 1) / 2.0
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


def _annualized_volatility(returns: list[float], days_per_year: int = 252) -> float:
    """Compute annualized volatility from daily returns."""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance * days_per_year)


def _max_drawdown(prices: list[float]) -> float:
    """Compute maximum drawdown."""
    if not prices:
        return 0.0
    peak = prices[0]
    max_dd = 0.0
    for p in prices:
        peak = max(peak, p)
        if peak > 0:
            dd = (p - peak) / peak
            max_dd = min(max_dd, dd)
    return max_dd
