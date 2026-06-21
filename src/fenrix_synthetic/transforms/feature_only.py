"""S3 feature-only structured transforms (Phase 5A).

Three variants producing only categorical/ordinal features — no prices,
no returns, no dates, no reconstructable OHLC or pseudo-price paths.

- S3A_DAILY_BUCKETED_CONTROL: Daily categorical features (NON_RELEASABLE_DIAGNOSTIC)
- S3B_WEEKLY_FEATURES: Weekly aggregated categorical features (release candidate)
- S3C_BLOCK_FEATURES: Four-week block features (strongest coarsening)

Every transform is deterministic, uses only information available up to
each period, and records private parameters (bin edges, reference data).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ── Release eligibility markers ───────────────────────────────────────

# S0, S1, S2 are NOT eligible for structured release.
# See docs/phase4_runbook.md for failure rationale.
NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE = {
    "s0_control",
    "s1_basic",
    "s2_privacy",
    "s2_incomplete",
    # S3A is documented as a non-releasable diagnostic. Defense in depth:
    # it cannot reach the release boundary even if release_marker is
    # tampered to "release_candidate".
    "s3a_daily_bucketed",
}


class S3Variant(StrEnum):
    S3A_DAILY_BUCKETED = "s3a_daily_bucketed"
    S3B_WEEKLY_FEATURES = "s3b_weekly_features"
    S3C_BLOCK_FEATURES = "s3c_block_features"


class ReleaseMarker(StrEnum):
    NON_RELEASABLE_DIAGNOSTIC = "non_releasable_diagnostic"
    RELEASE_CANDIDATE = "release_candidate"


@dataclass
class FeatureTransformResult:
    """Result of a feature-only transformation."""

    variant: S3Variant
    series_id: str
    features: list[dict[str, Any]]  # One dict per period
    parameters: dict  # Private: bin edges, reference data
    parameter_hash: str = ""
    row_count: int = 0
    is_deterministic: bool = True
    warnings: list[str] = field(default_factory=list)
    release_marker: ReleaseMarker = ReleaseMarker.NON_RELEASABLE_DIAGNOSTIC
    feature_schema_version: str = "1.0.0"
    missing_periods: list[int] = field(default_factory=list)
    forbidden_fields_detected: list[str] = field(default_factory=list)


@dataclass
class OhlcvRecord:
    """Single OHLCV bar (shared with transforms/structured.py)."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_close: float | None = None


# ── Helper: log returns (private, never released) ─────────────────────


def _log_returns(closes: list[float]) -> list[float]:
    rets: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
        else:
            rets.append(0.0)
    return rets


def _stddev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    sq = [(v - mean) ** 2 for v in values]
    return float((sum(sq) / n) ** 0.5)


# ── Bucket helpers (private bin edges, exposed as category labels) ────

_BUCKET_5 = ["VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH"]
_BUCKET_3 = ["LOW", "MEDIUM", "HIGH"]
_BUCKET_DIRECTION = ["DOWN", "FLAT", "UP"]
_BUCKET_REGIME = ["BEARISH", "NEUTRAL", "BULLISH"]


def _bucket_log_returns(rets: list[float], fit_rets: list[float] | None = None) -> list[str]:
    """Bucket log returns into DOWN/FLAT/UP using in-sample fit."""
    ref = fit_rets if fit_rets is not None else rets
    if not ref:
        return ["FLAT"] * len(rets)
    std = _stddev(ref)
    threshold = 0.5 * std  # FLAT = within 0.5 sigma
    result: list[str] = []
    for r in rets:
        if r > threshold:
            result.append("UP")
        elif r < -threshold:
            result.append("DOWN")
        else:
            result.append("FLAT")
    return result


def _compute_momentum(prices: list[float], window: int) -> list[float]:
    """Compute momentum as ratio of price change over window."""
    result: list[float] = [0.0] * len(prices)
    for i in range(window, len(prices)):
        if prices[i - window] > 0:
            result[i] = (prices[i] - prices[i - window]) / prices[i - window]
    return result


def _compute_volatility(rets: list[float], window: int) -> list[float]:
    result: list[float] = [0.0] * len(rets)
    for i in range(window, len(rets)):
        result[i] = _stddev(rets[i - window : i])
    return result


def _compute_drawdown(prices: list[float]) -> list[float]:
    result: list[float] = [0.0] * len(prices)
    peak = prices[0] if prices else 0
    for i, p in enumerate(prices):
        peak = max(peak, p)
        if peak > 0:
            result[i] = (p - peak) / peak
    return result


def _bucket_quantile(
    values: list[float], fit_values: list[float] | None, n: int, labels: list[str]
) -> list[str]:
    """Bucket values into n quantile-based buckets using in-sample fit."""
    ref = sorted(fit_values) if fit_values else sorted(values)
    if not ref:
        return [labels[0]] * len(values)
    quantile_edges = [ref[int(len(ref) * (i + 1) / n) - 1] for i in range(n)]
    result: list[str] = []
    for v in values:
        bucketed = False
        for i, edge in enumerate(quantile_edges):
            if v <= edge:
                result.append(labels[min(i, len(labels) - 1)])
                bucketed = True
                break
        if not bucketed:
            result.append(labels[-1])
    return result


# ── S3A: Daily Bucketed Control (NON_RELEASABLE_DIAGNOSTIC) ───────────


def transform_s3a_daily_bucketed(
    records: list[OhlcvRecord],
    base_price: float = 100.0,
    fit_window_days: int = 252,
    market_returns: list[float] | None = None,
    sector_returns: list[float] | None = None,
) -> FeatureTransformResult:
    """S3A: Daily categorical/ordinal features only.

    Features (per relative trading day):
    - return_direction: DOWN / FLAT / UP
    - momentum_5d_bucket: ordinal bucket (5)
    - momentum_21d_bucket: ordinal bucket (5)
    - momentum_63d_bucket: ordinal bucket (5)
    - volatility_21d_bucket: ordinal bucket (5)
    - volume_activity_21d_bucket: ordinal bucket (3)
    - drawdown_bucket: ordinal bucket (5)
    - moving_average_state: ABOVE / BELOW / CROSSED
    - market_relative_bucket: ordinal bucket (5) vs market
    - sector_relative_bucket: ordinal bucket (5) vs sector

    No prices, no returns, no dates, no reconstructable path.
    NON_RELEASABLE_DIAGNOSTIC — attack diagnostic only.
    """
    if len(records) < 2:
        return FeatureTransformResult(
            variant=S3Variant.S3A_DAILY_BUCKETED,
            series_id="",
            features=[],
            parameters={"error": "not enough records"},
            row_count=0,
            release_marker=ReleaseMarker.NON_RELEASABLE_DIAGNOSTIC,
        )

    closes = [r.close for r in records]
    rets = _log_returns(closes)
    volumes = [r.volume for r in records]
    n = len(rets)

    fit_n = min(fit_window_days, n)

    # Private parameters
    params = {
        "variant": "s3a_daily_bucketed",
        "fit_window_days": fit_window_days,
        "n_records": len(records),
        "n_returns": n,
        "has_market": market_returns is not None,
        "has_sector": sector_returns is not None,
    }

    # Compute features using only in-sample fit
    raw_mom_5 = _compute_momentum(closes, 5)
    raw_mom_21 = _compute_momentum(closes, 21)
    raw_mom_63 = _compute_momentum(closes, 63)
    raw_vol_21 = _compute_volatility(rets, 21)
    raw_dd = _compute_drawdown(closes)

    mom5_fit = raw_mom_5[:fit_n]
    mom21_fit = raw_mom_21[:fit_n]
    mom63_fit = raw_mom_63[:fit_n]
    vol21_fit = raw_vol_21[:fit_n]
    dd_fit = raw_dd[:fit_n]

    dirs = _bucket_log_returns(rets, rets[:fit_n])
    mom5_b = _bucket_quantile(raw_mom_5, mom5_fit, 5, _BUCKET_5)
    mom21_b = _bucket_quantile(raw_mom_21, mom21_fit, 5, _BUCKET_5)
    mom63_b = _bucket_quantile(raw_mom_63, mom63_fit, 5, _BUCKET_5)
    vol21_b = _bucket_quantile(raw_vol_21, vol21_fit, 5, _BUCKET_5)
    dd_b = _bucket_quantile(raw_dd, dd_fit, 5, _BUCKET_5)

    # Volume activity: rolling volume percentile bucketed
    vol_act_b: list[str] = []
    for i in range(n):
        start = max(0, i - 20)
        window_slice = volumes[start : i + 1]
        if window_slice:
            rank = sorted(window_slice).index(volumes[i]) / len(window_slice)
            if rank < 0.33:
                vol_act_b.append("LOW")
            elif rank < 0.67:
                vol_act_b.append("MEDIUM")
            else:
                vol_act_b.append("HIGH")
        else:
            vol_act_b.append("MEDIUM")

    # Moving average state
    ma_b: list[str] = []
    for i in range(n):
        if i < 20:
            ma_b.append("NEUTRAL")
        else:
            ma_short = sum(closes[i - 5 : i]) / 5
            ma_long = sum(closes[i - 20 : i]) / 20
            if ma_short > ma_long * 1.01:
                ma_b.append("ABOVE")
            elif ma_short < ma_long * 0.99:
                ma_b.append("BELOW")
            else:
                ma_b.append("CROSSED")

    # Market/sector relative buckets
    mkt_rel_b: list[str] = []
    sec_rel_b: list[str] = []

    if market_returns and len(market_returns) >= n:
        mkt_excess = [rets[i] - market_returns[i] for i in range(n)]
        mkt_rel_b = _bucket_quantile(mkt_excess, mkt_excess[:fit_n], 5, _BUCKET_5)
    else:
        mkt_rel_b = ["MEDIUM"] * n

    if sector_returns and len(sector_returns) >= n:
        sec_excess = [rets[i] - sector_returns[i] for i in range(n)]
        sec_rel_b = _bucket_quantile(sec_excess, sec_excess[:fit_n], 5, _BUCKET_5)
    else:
        sec_rel_b = ["MEDIUM"] * n

    # Build feature rows
    features: list[dict[str, Any]] = []
    missing: list[int] = []
    for i in range(n):
        f = {
            "relative_day": i,
            "return_direction": dirs[i],
            "momentum_5d_bucket": mom5_b[i],
            "momentum_21d_bucket": mom21_b[i],
            "momentum_63d_bucket": mom63_b[i],
            "volatility_21d_bucket": vol21_b[i],
            "volume_activity_21d_bucket": vol_act_b[i],
            "drawdown_bucket": dd_b[i],
            "moving_average_state": ma_b[i],
            "market_relative_bucket": mkt_rel_b[i],
            "sector_relative_bucket": sec_rel_b[i],
        }
        # Mark early periods with missing history
        if i < 20:
            missing.append(i)
        features.append(f)

    param_str = json.dumps(params, sort_keys=True)
    param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]

    return FeatureTransformResult(
        variant=S3Variant.S3A_DAILY_BUCKETED,
        series_id="s3a_daily_bucketed",
        features=features,
        parameters=params,
        parameter_hash=param_hash,
        row_count=n,
        release_marker=ReleaseMarker.NON_RELEASABLE_DIAGNOSTIC,
        warnings=[
            "S3A is a non-releasable attack diagnostic. Its purpose is to determine whether daily categorical traces remain identifiable."
        ],
        missing_periods=missing,
    )


# ── S3B: Weekly Feature-Only Candidate ────────────────────────────────


def transform_s3b_weekly_features(
    records: list[OhlcvRecord],
    base_price: float = 100.0,
    fit_window_weeks: int = 52,
    market_returns: list[float] | None = None,
    sector_returns: list[float] | None = None,
    fundamental_metrics: dict[str, float] | None = None,
) -> FeatureTransformResult:
    """S3B: Weekly aggregated feature-only candidate.

    Aggregates daily data into relative weekly periods.
    Features are categorical/ordinal only. No prices, no returns.

    Fundamental fields (optional): valuation_bucket, profitability_bucket,
    leverage_bucket, growth_bucket, liquidity_bucket — broad ordinal only.
    """
    if len(records) < 5:
        return FeatureTransformResult(
            variant=S3Variant.S3B_WEEKLY_FEATURES,
            series_id="",
            features=[],
            parameters={"error": "not enough records"},
            row_count=0,
            release_marker=ReleaseMarker.RELEASE_CANDIDATE,
        )

    # Aggregate daily records into weekly blocks (5-trading-day periods)
    week_size = 5
    weeks: list[list[OhlcvRecord]] = []
    for i in range(0, len(records), week_size):
        week = records[i : i + week_size]
        if len(week) >= 3:
            weeks.append(week)

    if not weeks:
        return FeatureTransformResult(
            variant=S3Variant.S3B_WEEKLY_FEATURES,
            series_id="",
            features=[],
            parameters={"error": "no complete weeks"},
            row_count=0,
            release_marker=ReleaseMarker.RELEASE_CANDIDATE,
        )

    n_weeks = len(weeks)

    # Compute weekly features
    weekly_direction: list[str] = []
    weekly_vol: list[float] = []
    weekly_ma_regime: list[str] = []
    weekly_trend_persistence: list[str] = []

    week_closes: list[float] = []
    week_volumes: list[float] = []

    for w in weeks:
        w_close = w[-1].close
        w_open = w[0].open
        w_high = max(r.high for r in w)
        w_low = min(r.low for r in w)
        w_vol = sum(r.volume for r in w)
        week_closes.append(w_close)
        week_volumes.append(w_vol)

        # Weekly direction
        if w_close > w_open * 1.005:
            weekly_direction.append("UP")
        elif w_close < w_open * 0.995:
            weekly_direction.append("DOWN")
        else:
            weekly_direction.append("FLAT")

        # Intra-week volatility
        if w_open > 0:
            weekly_vol.append((w_high - w_low) / w_open)
        else:
            weekly_vol.append(0.0)

    # Compute momentum over weekly closes
    mom_4w = _compute_momentum(week_closes, 4)
    mom_12w = _compute_momentum(week_closes, 12)
    mom_26w = _compute_momentum(week_closes, 26)

    # Weekly returns for volatility computation
    week_rets = _log_returns(week_closes)
    vol_4w = _compute_volatility(week_rets, 4)
    vol_12w = _compute_volatility(week_rets, 12)
    dd_w = _compute_drawdown(week_closes)

    # Compute market/sector relative on weekly basis
    mkt_excess_w: list[float] = [0.0] * n_weeks
    sec_excess_w: list[float] = [0.0] * n_weeks
    if market_returns:
        mkt_weekly = (
            market_returns[:n_weeks]
            if len(market_returns) >= n_weeks
            else market_returns + [0.0] * (n_weeks - len(market_returns))
        )
        mkt_excess_w = [
            week_rets[i] - mkt_weekly[i] if i < len(mkt_weekly) else 0.0 for i in range(n_weeks)
        ]
    if sector_returns:
        sec_weekly = (
            sector_returns[:n_weeks]
            if len(sector_returns) >= n_weeks
            else sector_returns + [0.0] * (n_weeks - len(sector_returns))
        )
        sec_excess_w = [
            week_rets[i] - sec_weekly[i] if i < len(sec_weekly) else 0.0 for i in range(n_weeks)
        ]

    fit_n = min(fit_window_weeks, n_weeks)
    fit_mom4 = mom_4w[:fit_n]
    fit_mom12 = mom_12w[:fit_n]
    fit_mom26 = mom_26w[:fit_n]
    fit_vol4 = vol_4w[:fit_n]
    fit_vol12 = vol_12w[:fit_n]
    fit_dd = dd_w[:fit_n]
    fit_mkt = mkt_excess_w[:fit_n]
    fit_sec = sec_excess_w[:fit_n]

    def _pad_to(vals: list[str], length: int, default: str = "MEDIUM") -> list[str]:
        return vals + [default] * (length - len(vals))

    mom4_b = _pad_to(_bucket_quantile(mom_4w, fit_mom4, 5, _BUCKET_5), n_weeks)
    mom12_b = _pad_to(_bucket_quantile(mom_12w, fit_mom12, 5, _BUCKET_5), n_weeks)
    mom26_b = _pad_to(_bucket_quantile(mom_26w, fit_mom26, 5, _BUCKET_5), n_weeks)
    vol4_b = _pad_to(_bucket_quantile(vol_4w, fit_vol4, 5, _BUCKET_5), n_weeks)
    vol12_b = _pad_to(_bucket_quantile(vol_12w, fit_vol12, 5, _BUCKET_5), n_weeks)
    dd_w_b = _pad_to(_bucket_quantile(dd_w, fit_dd, 5, _BUCKET_5), n_weeks)
    mkt_rel_w_b = _pad_to(_bucket_quantile(mkt_excess_w, fit_mkt, 5, _BUCKET_5), n_weeks)
    sec_rel_w_b = _pad_to(_bucket_quantile(sec_excess_w, fit_sec, 5, _BUCKET_5), n_weeks)

    # MA regime
    for i in range(n_weeks):
        if i < 4:
            weekly_ma_regime.append("NEUTRAL")
        else:
            ma_fast = sum(week_closes[i - 4 : i]) / 4
            ma_slow = sum(week_closes[i - 12 : i]) / 12 if i >= 12 else sum(week_closes[:i]) / i
            if ma_fast > ma_slow * 1.01:
                weekly_ma_regime.append("ABOVE")
            elif ma_fast < ma_slow * 0.99:
                weekly_ma_regime.append("BELOW")
            else:
                weekly_ma_regime.append("NEUTRAL")

    # Trend persistence
    for i in range(n_weeks):
        if i < 4:
            weekly_trend_persistence.append("SHORT")
        else:
            same_direction = sum(
                1 for j in range(max(0, i - 3), i + 1) if weekly_direction[j] == weekly_direction[i]
            )
            if same_direction >= 3:
                weekly_trend_persistence.append("PERSISTENT")
            elif same_direction >= 2:
                weekly_trend_persistence.append("MODERATE")
            else:
                weekly_trend_persistence.append("SHORT")

    features: list[dict[str, Any]] = []
    missing: list[int] = []
    for i in range(n_weeks):
        f: dict[str, Any] = {
            "relative_week": i,
            "weekly_direction_category": weekly_direction[i],
            "momentum_4w_bucket": mom4_b[i],
            "momentum_12w_bucket": mom12_b[i],
            "momentum_26w_bucket": mom26_b[i],
            "volatility_4w_bucket": vol4_b[i],
            "volatility_12w_bucket": vol12_b[i],
            "volume_activity_bucket": "MEDIUM",
            "drawdown_bucket": dd_w_b[i],
            "moving_average_regime": weekly_ma_regime[i],
            "market_relative_strength_bucket": mkt_rel_w_b[i],
            "sector_relative_strength_bucket": sec_rel_w_b[i],
            "trend_persistence_bucket": weekly_trend_persistence[i],
        }

        # Add optional fundamental buckets
        if fundamental_metrics:
            f["valuation_bucket"] = _bucket_quantile(
                [fundamental_metrics.get("pe", 15.0)], [15.0, 20.0, 25.0], 3, _BUCKET_3
            )[0]
            f["profitability_bucket"] = _bucket_quantile(
                [fundamental_metrics.get("roe", 0.1)], [0.05, 0.10, 0.15], 3, _BUCKET_3
            )[0]
            f["leverage_bucket"] = _bucket_quantile(
                [fundamental_metrics.get("debt_equity", 1.0)], [0.5, 1.0, 2.0], 3, _BUCKET_3
            )[0]

        if i < 4:
            missing.append(i)
        features.append(f)

    params = {
        "variant": "s3b_weekly_features",
        "fit_window_weeks": fit_window_weeks,
        "n_weeks": n_weeks,
        "has_fundamentals": fundamental_metrics is not None,
    }
    param_str = json.dumps(params, sort_keys=True)
    param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]

    return FeatureTransformResult(
        variant=S3Variant.S3B_WEEKLY_FEATURES,
        series_id="s3b_weekly_features",
        features=features,
        parameters=params,
        parameter_hash=param_hash,
        row_count=n_weeks,
        release_marker=ReleaseMarker.RELEASE_CANDIDATE,
        warnings=[
            "S3B uses weekly categorical features only. No prices, returns, or dates are released. Privacy depends on whether categorical traces remain issuer-specific."
        ],
        missing_periods=missing,
    )


# ── S3C: Four-Week Block Features ─────────────────────────────────────


def transform_s3c_block_features(
    records: list[OhlcvRecord],
    base_price: float = 100.0,
    fit_window_blocks: int = 13,
    market_returns: list[float] | None = None,
    sector_returns: list[float] | None = None,
) -> FeatureTransformResult:
    """S3C: Non-overlapping four-week block features.

    Strongest coarsening. Each block aggregates ~20 trading days into
    a single regime/category observation. No individual daily directions,
    no weekly directions, no continuous values, no pseudo-prices.
    """
    if len(records) < 20:
        return FeatureTransformResult(
            variant=S3Variant.S3C_BLOCK_FEATURES,
            series_id="",
            features=[],
            parameters={"error": "not enough records (need >= 20)"},
            row_count=0,
            release_marker=ReleaseMarker.RELEASE_CANDIDATE,
        )

    block_size = 20  # ~4 trading weeks
    blocks: list[list[OhlcvRecord]] = []
    for i in range(0, len(records), block_size):
        block = records[i : i + block_size]
        if len(block) >= 10:
            blocks.append(block)

    if not blocks:
        return FeatureTransformResult(
            variant=S3Variant.S3C_BLOCK_FEATURES,
            series_id="",
            features=[],
            parameters={"error": "no complete blocks"},
            row_count=0,
            release_marker=ReleaseMarker.RELEASE_CANDIDATE,
        )

    n_blocks = len(blocks)

    # Per-block aggregates
    block_closes: list[float] = []
    block_directions: list[list[str]] = []
    block_rets: list[list[float]] = []
    block_vols: list[float] = []

    for b in blocks:
        bc = b[-1].close
        block_closes.append(bc)
        dirs: list[str] = []
        brets: list[float] = []
        for j in range(1, len(b)):
            if b[j - 1].close > 0:
                r = math.log(b[j].close / b[j - 1].close)
            else:
                r = 0.0
            brets.append(r)
            if r > 0:
                dirs.append("UP")
            elif r < 0:
                dirs.append("DOWN")
            else:
                dirs.append("FLAT")
        block_rets.append(brets)
        block_directions.append(dirs)
        block_vols.append(b[0].close if b[0].close > 0 else 1.0)

    # Dominant trend regime (most common daily direction in block)
    regimes: list[str] = []
    for dirs in block_directions:
        up = dirs.count("UP")
        dn = dirs.count("DOWN")
        total = len(dirs)
        if up > 0.6 * total:
            regimes.append("STRONG_UP")
        elif dn > 0.6 * total:
            regimes.append("STRONG_DOWN")
        elif up > dn:
            regimes.append("MILD_UP")
        elif dn > up:
            regimes.append("MILD_DOWN")
        else:
            regimes.append("NEUTRAL")

    # Aggregate momentum (block-level)
    block_returns: list[float] = []
    for i in range(1, len(block_closes)):
        if block_closes[i - 1] > 0:
            block_returns.append(math.log(block_closes[i] / block_closes[i - 1]))
        else:
            block_returns.append(0.0)

    agg_mom_b = _bucket_quantile(
        [0.0] * n_blocks + block_returns,
        block_returns[:fit_window_blocks] if block_returns else None,
        5,
        _BUCKET_5,
    )

    # Volatility regime (per block)
    block_vol_series: list[float] = []
    for brets in block_rets:
        block_vol_series.append(_stddev(brets) if brets else 0.0)

    # Volume regime (per block)
    block_volume_series: list[float] = []
    for b in blocks:
        block_volume_series.append(sum(r.volume for r in b) / len(b))

    # Drawdown regime within each block
    block_drawdown_series: list[float] = []
    for b in blocks:
        peak = b[0].close
        max_dd = 0.0
        for rec in b:
            peak = max(peak, rec.close)
            if peak > 0:
                dd = (rec.close - peak) / peak
                max_dd = min(max_dd, dd)
        block_drawdown_series.append(max_dd)

    # Market/sector relative within each block (excess returns)
    block_excess = [0.0] * n_blocks  # relative to block_returns average
    if block_returns:
        mean_block_ret = sum(block_returns) / len(block_returns)
        block_excess = [r - mean_block_ret for r in block_returns]
        block_excess = [0.0] + block_excess  # pad first block

    # Proportion of reversal days (direction changes within block)
    reversal_freq: list[float] = []
    for dirs in block_directions:
        changes = sum(1 for j in range(1, len(dirs)) if dirs[j] != dirs[j - 1])
        reversal_freq.append(changes / max(1, len(dirs)))

    vol_regime = _bucket_quantile(
        block_vol_series, block_vol_series[:fit_window_blocks], 3, _BUCKET_3
    )
    vol_regime = vol_regime + ["MEDIUM"] * (n_blocks - len(vol_regime))

    draw_regime = _bucket_quantile(
        block_drawdown_series, block_drawdown_series[:fit_window_blocks], 3, _BUCKET_3
    )
    draw_regime = draw_regime + ["MEDIUM"] * (n_blocks - len(draw_regime))

    vol_act_regime = _bucket_quantile(
        block_volume_series, block_volume_series[:fit_window_blocks], 3, _BUCKET_3
    )
    vol_act_regime = vol_act_regime + ["MEDIUM"] * (n_blocks - len(vol_act_regime))

    mkt_rel_regime = _bucket_quantile(block_excess, block_excess[:fit_window_blocks], 3, _BUCKET_3)
    mkt_rel_regime = mkt_rel_regime + ["MEDIUM"] * (n_blocks - len(mkt_rel_regime))

    features: list[dict[str, Any]] = []
    missing: list[int] = []
    for i in range(n_blocks):
        f: dict[str, Any] = {
            "relative_block": i,
            "dominant_trend_regime": regimes[i],
            "aggregate_momentum_bucket": agg_mom_b[i] if i < len(agg_mom_b) else "MEDIUM",
            "volatility_regime": vol_regime[i] if i < len(vol_regime) else "MEDIUM",
            "volume_regime": vol_act_regime[i] if i < len(vol_act_regime) else "MEDIUM",
            "drawdown_regime": draw_regime[i] if i < len(draw_regime) else "MEDIUM",
            "market_relative_regime": mkt_rel_regime[i] if i < len(mkt_rel_regime) else "MEDIUM",
            "sector_relative_regime": "MEDIUM",
            "trend_consistency_bucket": _bucket_quantile(
                [reversal_freq[i]] if i < len(reversal_freq) else [0.5],
                reversal_freq[:fit_window_blocks] if reversal_freq else None,
                3,
                _BUCKET_3,
            )[0]
            if i < len(reversal_freq)
            else "MEDIUM",
            "reversal_frequency_bucket": _bucket_quantile(
                [reversal_freq[i]] if i < len(reversal_freq) else [0.5],
                reversal_freq[:fit_window_blocks] if reversal_freq else None,
                3,
                _BUCKET_3,
            )[0]
            if i < len(reversal_freq)
            else "MEDIUM",
        }
        if i < 1:
            missing.append(i)
        features.append(f)

    params = {
        "variant": "s3c_block_features",
        "fit_window_blocks": fit_window_blocks,
        "n_blocks": n_blocks,
        "block_size": block_size,
    }
    param_str = json.dumps(params, sort_keys=True)
    param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]

    return FeatureTransformResult(
        variant=S3Variant.S3C_BLOCK_FEATURES,
        series_id="s3c_block_features",
        features=features,
        parameters=params,
        parameter_hash=param_hash,
        row_count=n_blocks,
        release_marker=ReleaseMarker.RELEASE_CANDIDATE,
        warnings=[
            "S3C uses four-week block categorical features. Strongest coarsening but still empirical — privacy depends on attack resistance."
        ],
        missing_periods=missing,
    )


# ── Reconstructability scanner ─────────────────────────────────────────

_FORBIDDEN_FEATURE_FIELDS = {
    "close",
    "open",
    "high",
    "low",
    "volume",
    "price",
    "returns",
    "log_return",
    "pseudo_price",
    "reconstructed",
    "date",
    "timestamp",
    "adj_close",
    "dividend",
    "split",
}


# Known categorical field names that may contain forbidden substrings
# like 'volume' or 'moving' in their name but are safe categorical outputs.
_CATEGORICAL_FIELD_NAMES: set[str] = {
    "volume_activity_bucket",
    "volume_activity_21d_bucket",
    "volume_regime",
    "moving_average_state",
    "moving_average_regime",
    "return_direction",
    "weekly_direction_category",
    "momentum_5d_bucket",
    "momentum_21d_bucket",
    "momentum_63d_bucket",
    "momentum_4w_bucket",
    "momentum_12w_bucket",
    "momentum_26w_bucket",
    "aggregate_momentum_bucket",
    "volatility_21d_bucket",
    "volatility_4w_bucket",
    "volatility_12w_bucket",
    "volatility_regime",
    "drawdown_bucket",
    "drawdown_regime",
    "market_relative_bucket",
    "market_relative_strength_bucket",
    "market_relative_regime",
    "sector_relative_bucket",
    "sector_relative_strength_bucket",
    "sector_relative_regime",
    "trend_persistence_bucket",
    "trend_consistency_bucket",
    "reversal_frequency_bucket",
    "dominant_trend_regime",
    "valuation_bucket",
    "profitability_bucket",
    "leverage_bucket",
}


def scan_feature_reconstructability(features: list[dict[str, Any]]) -> list[str]:
    """Scan feature rows for forbidden continuous fields.

    Returns list of violations. Empty list = clean.

    Known categorical field names are excluded from the forbidden-key
    scan — e.g., ``volume_activity_bucket`` is a categorical field, not
    a raw volume number, even though its name contains "volume".
    """
    violations: list[str] = []
    if not features:
        return violations

    sample = features[0]
    for key in sample:
        if key in _CATEGORICAL_FIELD_NAMES:
            continue
        key_lower = key.lower()
        for forbidden in _FORBIDDEN_FEATURE_FIELDS:
            if forbidden in key_lower:
                violations.append(f"Forbidden field detected: '{key}' (matches '{forbidden}')")

        # Check for high-precision continuous values (risk of reconstruction)
        val = sample[key]
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if abs(val) > 1e10 or (isinstance(val, float) and val != int(val)):
                # Only flag if it looks like a price/return
                if key_lower not in ("relative_day", "relative_week", "relative_block"):
                    violations.append(f"Suspicious continuous value in '{key}': {val}")

    # Check for date-like fields
    for key in sample:
        if any(d in key.lower() for d in ["date", "time", "timestamp"]):
            violations.append(f"Date/time field detected: '{key}'")

    return violations
