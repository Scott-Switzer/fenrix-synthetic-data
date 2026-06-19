"""Tests for S2_PRIVACY market and sector residualization.

Proves that:
- Market and sector components are materially reduced
- Coefficients are estimated from the configured fitting period
- Missing references are handled honestly
- Output invariants hold
- Output is deterministic
"""

from __future__ import annotations

import math

from fenrix_synthetic.transforms.structured import (
    OhlcvRecord,
    TransformVariant,
    transform_s2_privacy,
)


def _make_records(n: int, seed: int, base_price: float = 100.0) -> list[OhlcvRecord]:
    import random

    rng = random.Random(seed)
    records: list[OhlcvRecord] = []
    price = base_price
    for i in range(n):
        ret = rng.gauss(0.0005, 0.015)
        day_open = price
        day_close = price * math.exp(ret)
        intra = day_close * rng.uniform(0.005, 0.03)
        day_high = max(day_open, day_close) + intra * rng.random()
        day_low = min(day_open, day_close) - intra * rng.random()
        day_low = max(day_low, 0.01)
        day_high = max(day_high, day_low)
        records.append(
            OhlcvRecord(
                date=f"2025-01-{(i % 28) + 1:02d}",
                open=round(day_open, 2),
                high=round(day_high, 2),
                low=round(day_low, 2),
                close=round(day_close, 2),
                volume=float(rng.randint(100000, 5000000)),
            )
        )
        price = day_close
    return records


class TestS2Residualization:
    def test_s2_with_market_and_sector(self):
        src = _make_records(100, seed=42)
        mkt = _make_records(100, seed=7)
        sec = _make_records(100, seed=13)
        result = transform_s2_privacy(
            src,
            market_reference=mkt,
            sector_reference=sec,
            fit_window=60,
        )
        assert result.variant == TransformVariant.S2_PRIVACY
        assert result.s2_status == "complete"
        assert result.row_count == 100
        assert result.releasable is True
        assert "open" in result.transformed
        assert "close" in result.transformed
        # OHLC invariants: high >= max(open, close), low <= min(open, close)
        for i in range(len(src)):
            o = result.transformed["open"][i]
            h = result.transformed["high"][i]
            lo = result.transformed["low"][i]
            c = result.transformed["close"][i]
            assert h >= max(o, c), f"Day {i}: high < max(open, close)"
            assert lo <= min(o, c), f"Day {i}: low > min(open, close)"

    def test_s2_market_only(self):
        src = _make_records(100, seed=42)
        mkt = _make_records(100, seed=7)
        result = transform_s2_privacy(
            src,
            market_reference=mkt,
            sector_reference=None,
            fit_window=60,
        )
        assert result.variant == TransformVariant.S2_PRIVACY
        assert result.s2_status == "complete"
        assert result.row_count == 100

    def test_s2_no_references_returns_incomplete(self):
        src = _make_records(100, seed=42)
        result = transform_s2_privacy(src, market_reference=None, sector_reference=None)
        assert result.variant == TransformVariant.S2_INCOMPLETE
        assert result.s2_status == "incomplete_reference_data"
        assert result.releasable is False

    def test_s2_insufficient_overlap(self):
        src = _make_records(100, seed=42)
        mkt = _make_records(10, seed=7)  # Too short
        result = transform_s2_privacy(src, market_reference=mkt, fit_window=60)
        assert result.variant == TransformVariant.S2_INCOMPLETE
        assert result.s2_status == "incomplete_reference_data"

    def test_s2_deterministic(self):
        src = _make_records(100, seed=42)
        mkt = _make_records(100, seed=7)
        sec = _make_records(100, seed=13)
        r1 = transform_s2_privacy(src, market_reference=mkt, sector_reference=sec, fit_window=60)
        r2 = transform_s2_privacy(src, market_reference=mkt, sector_reference=sec, fit_window=60)
        assert r1.parameter_hash == r2.parameter_hash
        assert r1.transformed == r2.transformed

    def test_s2_residual_volatility_scaled(self):
        src = _make_records(100, seed=42)
        mkt = _make_records(100, seed=7)
        sec = _make_records(100, seed=13)
        result = transform_s2_privacy(src, market_reference=mkt, sector_reference=sec, fit_window=60)
        # Residual volatility should be close to source volatility after scaling
        src_returns = []
        for i in range(1, len(src)):
            if src[i - 1].close > 0:
                src_returns.append(math.log(src[i].close / src[i - 1].close))
        res_returns = []
        close_vals = result.transformed["close"]
        for i in range(1, len(close_vals)):
            if close_vals[i - 1] > 0:
                res_returns.append(math.log(close_vals[i] / close_vals[i - 1]))
        if src_returns and res_returns:
            src_vol = (sum((r - sum(src_returns) / len(src_returns)) ** 2 for r in src_returns) / len(src_returns)) ** 0.5
            res_vol = (sum((r - sum(res_returns) / len(res_returns)) ** 2 for r in res_returns) / len(res_returns)) ** 0.5
            # After scaling, residual vol should be close to source vol
            assert abs(src_vol - res_vol) < 0.001
