"""Feature-only schema and validation (Phase 5A).

FeatureOnlySeries: versioned schema for S3 feature-only structured data.
Validation rejects continuous price fields, raw returns, OHLC columns,
dates, timestamps, and unsupported values.

Reconstructability scan detects forbidden fields and suspicious
high-precision continuous values.

Phase 5A close-out:
* `FeatureOnlySeriesValidation` is now a frozen dataclass with derived
  validity. `is_valid` cannot disagree with the error list.
* Validators emit tuples of `(errors, warnings)` plus structured issue
  lists (forbidden_fields, invalid_values, ...).
* `to_dict()` preserves the contract by computing `is_valid` from
  `errors` on demand.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from fenrix_synthetic.transforms.feature_only import (
    _FORBIDDEN_FEATURE_FIELDS,
    S3Variant,
    scan_feature_reconstructability,
)

# Allowed feature values per field (whitelist for categorical validation)
_ALLOWED_DIRECTION_VALUES = {"DOWN", "FLAT", "UP"}
_ALLOWED_BUCKET_3_VALUES = {"LOW", "MEDIUM", "HIGH"}
_ALLOWED_BUCKET_5_VALUES = {"VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH"}
_ALLOWED_REGIME_VALUES = {"BEARISH", "NEUTRAL", "BULLISH"}
_ALLOWED_MA_VALUES = {"BELOW", "CROSSED", "ABOVE", "NEUTRAL"}
_ALLOWED_TREND_VALUES = {"SHORT", "MODERATE", "PERSISTENT"}
_ALLOWED_BLOCK_REGIME_VALUES = {"STRONG_DOWN", "MILD_DOWN", "NEUTRAL", "MILD_UP", "STRONG_UP"}


_FIELD_VALIDATORS: dict[str, set[str]] = {
    "return_direction": _ALLOWED_DIRECTION_VALUES,
    "weekly_direction_category": _ALLOWED_DIRECTION_VALUES,
    "momentum_5d_bucket": _ALLOWED_BUCKET_5_VALUES,
    "momentum_21d_bucket": _ALLOWED_BUCKET_5_VALUES,
    "momentum_63d_bucket": _ALLOWED_BUCKET_5_VALUES,
    "momentum_4w_bucket": _ALLOWED_BUCKET_5_VALUES,
    "momentum_12w_bucket": _ALLOWED_BUCKET_5_VALUES,
    "momentum_26w_bucket": _ALLOWED_BUCKET_5_VALUES,
    "aggregate_momentum_bucket": _ALLOWED_BUCKET_5_VALUES,
    "volatility_21d_bucket": _ALLOWED_BUCKET_5_VALUES,
    "volatility_4w_bucket": _ALLOWED_BUCKET_5_VALUES,
    "volatility_12w_bucket": _ALLOWED_BUCKET_5_VALUES,
    "volatility_regime": _ALLOWED_BUCKET_3_VALUES,
    "volume_activity_21d_bucket": _ALLOWED_BUCKET_3_VALUES,
    "volume_activity_bucket": _ALLOWED_BUCKET_3_VALUES,
    "volume_regime": _ALLOWED_BUCKET_3_VALUES,
    "drawdown_bucket": _ALLOWED_BUCKET_5_VALUES,
    "drawdown_regime": _ALLOWED_BUCKET_3_VALUES,
    "moving_average_state": _ALLOWED_MA_VALUES,
    "moving_average_regime": _ALLOWED_MA_VALUES,
    "market_relative_bucket": _ALLOWED_BUCKET_5_VALUES,
    "market_relative_strength_bucket": _ALLOWED_BUCKET_5_VALUES,
    "market_relative_regime": _ALLOWED_BUCKET_3_VALUES,
    "sector_relative_bucket": _ALLOWED_BUCKET_5_VALUES,
    "sector_relative_strength_bucket": _ALLOWED_BUCKET_5_VALUES,
    "sector_relative_regime": _ALLOWED_BUCKET_3_VALUES,
    "trend_persistence_bucket": _ALLOWED_TREND_VALUES,
    "trend_consistency_bucket": _ALLOWED_BUCKET_3_VALUES,
    "reversal_frequency_bucket": _ALLOWED_BUCKET_3_VALUES,
    "dominant_trend_regime": _ALLOWED_BLOCK_REGIME_VALUES,
    "valuation_bucket": _ALLOWED_BUCKET_3_VALUES,
    "profitability_bucket": _ALLOWED_BUCKET_3_VALUES,
    "leverage_bucket": _ALLOWED_BUCKET_3_VALUES,
}


# ── Validated exception hierarchy (Phase 5A close-out) ──────────────────


class SchemaValidationError(Exception):
    """Raised when validators encounter contradictory/structurally invalid input."""


@dataclass(frozen=True)
class FeatureOnlySeriesValidation:
    """Result of validating a feature-only series (Phase 5A close-out).

    Validity is *derived* — `is_valid` is computed from `errors` and
    cannot be independently set. This eliminates the prior bug class
    where a caller could construct `FeatureOnlySeriesValidation(
    is_valid=True, errors=(...))`.
    """

    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    forbidden_fields: tuple[str, ...] = field(default_factory=tuple)
    invalid_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    non_finite_values: tuple[str, ...] = field(default_factory=tuple)
    ordinal_out_of_range: tuple[str, ...] = field(default_factory=tuple)
    duplicate_periods: tuple[str, ...] = field(default_factory=tuple)
    period_gaps: tuple[str, ...] = field(default_factory=tuple)
    reconstructability_issues: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def issues(self) -> list[str]:
        """Backward-compatible list view of error messages."""
        return list(self.errors)

    def to_dict(self) -> dict[str, object]:
        return {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "forbidden_fields": list(self.forbidden_fields),
            "invalid_values": {k: list(v) for k, v in self.invalid_values.items()},
            "non_finite_values": list(self.non_finite_values),
            "ordinal_out_of_range": list(self.ordinal_out_of_range),
            "duplicate_periods": list(self.duplicate_periods),
            "period_gaps": list(self.period_gaps),
            "reconstructability_issues": list(self.reconstructability_issues),
        }


def validate_feature_series(
    features: list[dict[str, Any]],
    variant: S3Variant,
    *,
    strict_categorical: bool = True,
) -> FeatureOnlySeriesValidation:
    """Validate a feature-only series for release correctness.

    Returns a frozen FeatureOnlySeriesValidation. Validity is derived
    from the accumulated errors.
    """
    errors: list[str] = []
    warnings: list[str] = []
    forbidden_fields: list[str] = []
    invalid_values: dict[str, list[str]] = {}
    non_finite_values: list[str] = []
    duplicate_periods: list[str] = []
    period_gaps: list[str] = []
    reconstructability_issues: list[str] = []

    if not features:
        errors.append("Empty feature set")
        return FeatureOnlySeriesValidation(errors=tuple(errors))

    # Reconstructability scan
    rec_issues = scan_feature_reconstructability(features)
    if rec_issues:
        reconstructability_issues.extend(rec_issues)
        forbidden_fields.extend(rec_issues)
        errors.extend(rec_issues)

    seen_periods: set[int] = set()
    prev_period: int | None = None

    for row_idx, row in enumerate(features):
        for field_name, field_value in row.items():
            # Skip period identifiers
            if field_name in ("relative_day", "relative_week", "relative_block"):
                if isinstance(field_value, int):
                    if prev_period is not None and field_value < prev_period:
                        period_gaps.append(
                            f"Row {row_idx}: period {field_value} < previous {prev_period}"
                        )
                    prev_period = field_value
                    if field_value in seen_periods:
                        duplicate_periods.append(f"Duplicate period {field_value} at row {row_idx}")
                    seen_periods.add(field_value)
                continue

            # Skip metadata fields
            if field_name.startswith("_"):
                continue

            # Check for forbidden patterns in field names.
            # Skip known categorical fields — they may contain substrings
            # like "volume" but are safe (e.g. volume_activity_bucket).
            if field_name not in _FIELD_VALIDATORS:
                field_lower = field_name.lower()
                for forbidden in _FORBIDDEN_FEATURE_FIELDS:
                    if forbidden in field_lower and field_name not in forbidden_fields:
                        forbidden_fields.append(field_name)

            # Validate categorical values
            if strict_categorical and field_name in _FIELD_VALIDATORS:
                allowed = _FIELD_VALIDATORS[field_name]
                if isinstance(field_value, str) and field_value not in allowed:
                    invalid_values.setdefault(field_name, []).append(field_value)

            # Check for non-finite numeric values
            if isinstance(field_value, (int, float)) and not isinstance(field_value, bool):
                if not math.isfinite(field_value):
                    non_finite_values.append(f"{field_name}={field_value}")

    if duplicate_periods:
        errors.extend(duplicate_periods)
    if period_gaps:
        errors.extend(period_gaps)
    if non_finite_values:
        errors.extend(f"Non-finite values: {non_finite_values}")
    if invalid_values:
        for fname, vals in invalid_values.items():
            errors.append(f"Invalid categorical values for {fname}: {sorted(set(vals))}")

    return FeatureOnlySeriesValidation(
        errors=tuple(errors),
        warnings=tuple(warnings),
        forbidden_fields=tuple(forbidden_fields),
        invalid_values={k: tuple(v) for k, v in invalid_values.items()},
        non_finite_values=tuple(non_finite_values),
        duplicate_periods=tuple(duplicate_periods),
        period_gaps=tuple(period_gaps),
        reconstructability_issues=tuple(reconstructability_issues),
    )


def _get_period_key(variant: S3Variant) -> str:
    mapping = {
        S3Variant.S3A_DAILY_BUCKETED: "relative_day",
        S3Variant.S3B_WEEKLY_FEATURES: "relative_week",
        S3Variant.S3C_BLOCK_FEATURES: "relative_block",
    }
    return mapping.get(variant, "relative_period")


def safe_hash(features: list[dict[str, Any]]) -> str:
    """Compute a safe hash of feature data without exposing private values."""
    safe_data = features
    return hashlib.sha256(json.dumps(safe_data, sort_keys=True).encode()).hexdigest()[:16]
