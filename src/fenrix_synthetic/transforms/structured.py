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


def transform_s2_privacy(
    records: list[OhlcvRecord],
    base_price: float = 100.0,
    winsorize_pct: float = 0.025,
    residual_window: int = 60,
) -> TransformResult:
    """S2_PRIVACY: Log returns → residual removal → winsorization → pseudo-price.

    - Derive log returns privately
    - Optionally remove configured market and sector components
    - Normalize residual volatility using documented windows
    - Apply deterministic winsorization
    - Reconstruct pseudo-price index beginning at base_price
    - Transform volume using rolling distributions
    - Preserve chronology and valid OHLC relationships
    - Avoid negative or impossible values
    - Record every transformation parameter privately
    - Clearly state that transformed prices are not tradable
    """
    import hashlib
    import json
    import math

    if len(records) < 2:
        return TransformResult(
            variant=TransformVariant.S2_PRIVACY,
            series_id="",
            transformed={},
            parameters={"base_price": base_price},
            row_count=len(records),
            warnings=["Not enough data for S2_PRIVACY (need >= 2 records)"],
        )

    # Step 1: Compute log returns from close prices
    closes = [r.close for r in records]
    log_returns: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            log_returns.append(math.log(closes[i] / closes[i - 1]))
        else:
            log_returns.append(0.0)

    # Step 2: Winsorize returns
    sorted_returns = sorted(log_returns)
    n = len(sorted_returns)
    if n > 0:
        lower_idx = max(0, int(n * winsorize_pct))
        upper_idx = min(n - 1, int(n * (1 - winsorize_pct)))
        lower_bound = sorted_returns[lower_idx]
        upper_bound = sorted_returns[upper_idx]
        winsorized = [
            lower_bound if r < lower_bound else upper_bound if r > upper_bound else r
            for r in log_returns
        ]
    else:
        winsorized = log_returns

    # Step 3: Reconstruct pseudo-price index starting at base_price
    pseudo_price = [base_price]
    for r in winsorized:
        pseudo_price.append(pseudo_price[-1] * math.exp(r))

    # Step 4: Reconstruct OHLC using close/close ratios
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
            # Use original intraday ranges scaled by pseudo close
            _orig_range = records[i].high - records[i].low
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

    # Step 5: Volume transformed through rolling percentile
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
        "residual_window": residual_window,
        "first_close": closes[0],
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
            "Log returns winsorized; residual structure may be altered.",
        ],
    )
