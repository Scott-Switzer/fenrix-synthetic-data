"""Structured data transformations (Phase 4F).

Three variants:
- S0_CONTROL: Non-releasable attack control (exact source returns preserved)
- S1_BASIC: Rebasing, volume normalization, corporate action generalization
- S2_PRIVACY: Log returns, residual removal, winsorization, pseudo-price reconstruction

All transforms are deterministic. Parameters are recorded privately.
Transformed prices are NOT tradable historical prices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TransformVariant(StrEnum):
    S0_CONTROL = "s0_control"
    S1_BASIC = "s1_basic"
    S2_PRIVACY = "s2_privacy"
    S2_INCOMPLETE = "s2_incomplete_reference_data"


@dataclass
class TransformResult:
    """Result of applying a structured transformation."""

    variant: TransformVariant
    series_id: str
    transformed: dict[str, list[float]]  # column -> values
    parameters: dict  # Private: transformation parameters used
    parameter_hash: str = ""
    row_count: int = 0
    is_deterministic: bool = True
    warnings: list[str] = field(default_factory=list)
    releasable: bool = False
    s2_status: str = ""  # "complete", "incomplete_reference_data", "not_applicable"


@dataclass
class OhlcvRecord:
    """Single OHLCV bar."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_close: float | None = None


def _validate_ohlcv(records: list[OhlcvRecord]) -> list[str]:
    """Validate OHLCV invariants. Returns list of violations."""
    violations: list[str] = []
    for r in records:
        if r.high < r.low:
            violations.append(f"{r.date}: high < low")
        if r.high < r.open or r.high < r.close:
            violations.append(f"{r.date}: high < open or close")
        if r.low > r.open or r.low > r.close:
            violations.append(f"{r.date}: low > open or close")
        if r.volume < 0:
            violations.append(f"{r.date}: negative volume")
    return violations


def transform_s0_control(
    records: list[OhlcvRecord],
    base_price: float = 100.0,
) -> TransformResult:
    """S0_CONTROL: Non-releasable attack control.

    - Relative trading-day index (0, 1, 2, ...)
    - Starting close price rebased to base_price
    - Exact source return sequence retained
    - NOT RELEASABLE
    """
    import hashlib
    import json

    if not records:
        return TransformResult(
            variant=TransformVariant.S0_CONTROL,
            series_id="",
            transformed={},
            parameters={"base_price": base_price},
            row_count=0,
        )

    first_close = records[0].close
    factor = base_price / first_close if first_close != 0 else 1.0

    day_index = list(range(len(records)))
    close_rebased = [r.close * factor for r in records]

    params = {
        "variant": "s0_control",
        "base_price": base_price,
        "first_close": first_close,
        "factor": factor,
    }
    param_hash = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]

    return TransformResult(
        variant=TransformVariant.S0_CONTROL,
        series_id="s0_control",
        transformed={
            "day_index": [float(d) for d in day_index],
            "close_rebased": close_rebased,
        },
        parameters=params,
        parameter_hash=param_hash,
        row_count=len(records),
        releasable=False,
        warnings=["S0_CONTROL is a non-releasable attack control variant"],
    )


def transform_s1_basic(
    records: list[OhlcvRecord],
    base_price: float = 100.0,
) -> TransformResult:
    """S1_BASIC: Rebasing and normalization.

    - Relative trading-day index
    - Prices rebased to base_price (100)
    - Volume converted to rolling percentile
    - OHLC represented through normalized price relationships
    - Absolute shares and market cap removed
    - Corporate-action labels removed or generalized
    """
    import hashlib
    import json

    if not records:
        return TransformResult(
            variant=TransformVariant.S1_BASIC,
            series_id="",
            transformed={},
            parameters={"base_price": base_price},
            row_count=0,
        )

    first_close = records[0].close
    factor = base_price / first_close if first_close != 0 else 1.0

    day_index = list(range(len(records)))
    open_vals = [r.open * factor for r in records]
    high_vals = [r.high * factor for r in records]
    low_vals = [r.low * factor for r in records]
    close_vals = [r.close * factor for r in records]

    # Volume normalized to rolling percentile (20-day window)
    volume_vals = [float(r.volume) for r in records]
    vol_norm: list[float] = []
    window = min(20, len(records))
    for i in range(len(records)):
        start = max(0, i - window + 1)
        window_slice = volume_vals[start : i + 1]
        if window_slice:
            rank = sorted(window_slice).index(volume_vals[i])
            vol_norm.append(rank / len(window_slice) if len(window_slice) > 1 else 0.5)
        else:
            vol_norm.append(0.5)

    params = {
        "variant": "s1_basic",
        "base_price": base_price,
        "first_close": first_close,
        "factor": factor,
    }
    param_hash = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]

    return TransformResult(
        variant=TransformVariant.S1_BASIC,
        series_id="s1_basic",
        transformed={
            "day_index": [float(d) for d in day_index],
            "open": open_vals,
            "high": high_vals,
            "low": low_vals,
            "close": close_vals,
            "volume_percentile": vol_norm,
        },
        parameters=params,
        parameter_hash=param_hash,
        row_count=len(records),
        releasable=True,
        warnings=[],
    )


def _stddev(values: list[float]) -> float:
    """Population standard deviation."""
    n = len(values)
    if n < 2:
        return 0.0
    mean: float = sum(values) / n
    sq_diffs: list[float] = [(v - mean) ** 2 for v in values]
    variance: float = sum(sq_diffs) / n
    result: float = variance**0.5
    return result


def _compute_log_returns(closes: list[float]) -> list[float]:
    """Compute log returns from close prices."""
    import math

    returns: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            returns.append(math.log(closes[i] / closes[i - 1]))
        else:
            returns.append(0.0)
    return returns


def _ols_coefficients(
    y: list[float], x1: list[float], x2: list[float] | None = None
) -> tuple[float, float, float]:
    """Estimate OLS coefficients (alpha, beta1, beta2) via normal equations.

    Returns (alpha, beta1, beta2) where beta2=0 if x2 is None.
    """
    n = len(y)
    if n == 0:
        return 0.0, 0.0, 0.0

    mean_y = sum(y) / n
    mean_x1 = sum(x1) / n
    mean_x2 = sum(x2) / n if x2 else 0.0

    # Centered variables
    yc = [yi - mean_y for yi in y]
    x1c = [xi - mean_x1 for xi in x1]
    x2c = [xi - mean_x2 for xi in x2] if x2 else [0.0] * n

    # Normal equations for two regressors
    s11 = sum(a * b for a, b in zip(x1c, x1c, strict=False))
    s12 = sum(a * b for a, b in zip(x1c, x2c, strict=False))
    s22 = sum(a * b for a, b in zip(x2c, x2c, strict=False))
    sy1 = sum(a * b for a, b in zip(yc, x1c, strict=False))
    sy2 = sum(a * b for a, b in zip(yc, x2c, strict=False))

    denom = s11 * s22 - s12 * s12 if x2 else s11
    if x2 and denom != 0:
        beta1 = (sy1 * s22 - s12 * sy2) / denom
        beta2 = (s11 * sy2 - s12 * sy1) / denom
    elif s11 != 0:
        beta1 = sy1 / s11
        beta2 = 0.0
    else:
        beta1 = 0.0
        beta2 = 0.0

    alpha = mean_y - beta1 * mean_x1 - beta2 * mean_x2
    return alpha, beta1, beta2


def _winsorize(values: list[float], pct: float) -> list[float]:
    """Deterministic winsorization."""
    if not values:
        return values
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    lower_idx = max(0, int(n * pct))
    upper_idx = min(n - 1, int(n * (1 - pct)))
    lower = sorted_vals[lower_idx]
    upper = sorted_vals[upper_idx]
    return [lower if v < lower else upper if v > upper else v for v in values]


def transform_s2_privacy(
    records: list[OhlcvRecord],
    base_price: float = 100.0,
    winsorize_pct: float = 0.025,
    fit_window: int = 60,
    market_reference: list[OhlcvRecord] | None = None,
    sector_reference: list[OhlcvRecord] | None = None,
) -> TransformResult:
    """S2_PRIVACY: Log returns → market/sector residual removal → winsorization → pseudo-price.

    - Derive log returns privately
    - Remove configured market and sector components via in-sample regression
    - Normalize residual volatility deterministically
    - Apply deterministic winsorization
    - Reconstruct pseudo-price index beginning at base_price
    - Preserve chronology and valid OHLC relationships
    - Record every transformation parameter privately
    """
    import hashlib
    import json
    import math

    if len(records) < 2:
        return TransformResult(
            variant=TransformVariant.S2_INCOMPLETE,
            series_id="",
            transformed={},
            parameters={"base_price": base_price},
            row_count=len(records),
            warnings=["Not enough data for S2 (need >= 2 records)"],
            s2_status="incomplete_reference_data",
        )

    has_market = market_reference is not None and len(market_reference) >= 2
    has_sector = sector_reference is not None and len(sector_reference) >= 2

    if not has_market and not has_sector:
        # No references: produce incomplete variant honestly
        return TransformResult(
            variant=TransformVariant.S2_INCOMPLETE,
            series_id="s2_incomplete",
            transformed={},
            parameters={"base_price": base_price, "reason": "no_market_or_sector_reference"},
            row_count=len(records),
            warnings=[
                "S2_INCOMPLETE: No market or sector reference data provided. "
                "Cannot perform residual regression. "
                "Use test fixtures with references or supply --market-reference/--sector-reference."
            ],
            releasable=False,
            s2_status="incomplete_reference_data",
        )

    # Step 1: Compute log returns for source, market, sector
    src_closes = [r.close for r in records]
    src_returns = _compute_log_returns(src_closes)

    mkt_returns: list[float] = []
    sec_returns: list[float] = []

    if has_market and market_reference is not None:
        mkt_closes = [r.close for r in market_reference]
        mkt_returns = _compute_log_returns(mkt_closes)
    if has_sector and sector_reference is not None:
        sec_closes = [r.close for r in sector_reference]
        sec_returns = _compute_log_returns(sec_closes)

    # Step 2: Align series lengths (use minimum overlapping period)
    n = min(
        len(src_returns),
        len(mkt_returns) if mkt_returns else len(src_returns),
        len(sec_returns) if sec_returns else len(src_returns),
    )
    if n < fit_window:
        return TransformResult(
            variant=TransformVariant.S2_INCOMPLETE,
            series_id="s2_incomplete",
            transformed={},
            parameters={"base_price": base_price, "reason": "insufficient_overlap"},
            row_count=len(records),
            warnings=[f"S2_INCOMPLETE: Insufficient overlap ({n}) for fit window ({fit_window})"],
            releasable=False,
            s2_status="incomplete_reference_data",
        )

    src_r = src_returns[:n]
    mkt_r = mkt_returns[:n] if mkt_returns else [0.0] * n
    sec_r = sec_returns[:n] if sec_returns else [0.0] * n

    # Step 3: In-sample regression (first fit_window observations)
    fit_n = min(fit_window, n)
    alpha, beta_mkt, beta_sec = _ols_coefficients(
        src_r[:fit_n], mkt_r[:fit_n], sec_r[:fit_n] if has_sector else None
    )

    # Step 4: Remove market/sector components from FULL period
    residuals: list[float] = []
    for i in range(n):
        fitted = alpha + beta_mkt * mkt_r[i] + beta_sec * sec_r[i]
        residuals.append(src_r[i] - fitted)

    # Step 5: Normalize residual volatility to source volatility
    src_vol = _stddev(src_r)
    res_vol = _stddev(residuals)
    if res_vol > 0:
        scale = src_vol / res_vol
        residuals = [r * scale for r in residuals]

    # Step 6: Winsorize residuals
    winsorized = _winsorize(residuals, winsorize_pct)

    # Step 7: Reconstruct pseudo-price index
    pseudo_price = [base_price]
    for r in winsorized:
        pseudo_price.append(pseudo_price[-1] * math.exp(r))

    # Step 8: Reconstruct OHLC using close/close ratios
    pseudo_open: list[float] = []
    pseudo_high: list[float] = []
    pseudo_low: list[float] = []
    pseudo_close: list[float] = []

    for i in range(len(records)):
        if i == 0:
            pseudo_close.append(base_price)
            pseudo_open.append(base_price)
            pseudo_high.append(base_price)
            pseudo_low.append(base_price)
        else:
            pc = pseudo_price[i]
            pseudo_close.append(pc)
            orig_close = records[i].close
            if orig_close > 0:
                scale = pc / orig_close
                pseudo_high.append(pc + (records[i].high - orig_close) * scale)
                pseudo_low.append(pc - (orig_close - records[i].low) * scale)
                pseudo_open.append(pc - (orig_close - records[i].open) * scale)
            else:
                pseudo_open.append(pc)
                pseudo_high.append(pc)
                pseudo_low.append(pc)

    # Step 9: Volume transformed through rolling percentile
    volume_vals = [float(r.volume) for r in records]
    vol_norm: list[float] = []
    window = min(20, len(records))
    for i in range(len(records)):
        start = max(0, i - window + 1)
        window_slice = volume_vals[start : i + 1]
        if window_slice:
            rank = sorted(window_slice).index(volume_vals[i])
            vol_norm.append(rank / len(window_slice) if len(window_slice) > 1 else 0.5)
        else:
            vol_norm.append(0.5)

    day_index = list(range(len(records)))

    params = {
        "variant": "s2_privacy",
        "base_price": base_price,
        "winsorize_pct": winsorize_pct,
        "fit_window": fit_window,
        "first_close": src_closes[0],
        "alpha": alpha,
        "beta_market": beta_mkt,
        "beta_sector": beta_sec,
        "has_market": has_market,
        "has_sector": has_sector,
        "overlap_n": n,
    }
    param_hash = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]

    return TransformResult(
        variant=TransformVariant.S2_PRIVACY,
        series_id="s2_privacy",
        transformed={
            "day_index": [float(d) for d in day_index],
            "open": pseudo_open,
            "high": pseudo_high,
            "low": pseudo_low,
            "close": pseudo_close,
            "volume_percentile": vol_norm,
        },
        parameters=params,
        parameter_hash=param_hash,
        row_count=len(records),
        releasable=True,
        warnings=[
            "S2_PRIVACY transformed prices are NOT tradable historical prices.",
            f"Removed market component (beta={beta_mkt:.4f}) and sector component (beta={beta_sec:.4f}).",
        ],
        s2_status="complete",
    )
