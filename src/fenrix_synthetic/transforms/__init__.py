"""Structured data transformation subsystem.

Three structured-price variants:
- S0_CONTROL: Non-releasable attack control
- S1_BASIC: Rebasing and normalization
- S2_PRIVACY: Log returns, winsorization, pseudo-price reconstruction

Three feature-only variants (Phase 5A):
- S3A_DAILY_BUCKETED: Daily categorical features (NON_RELEASABLE_DIAGNOSTIC)
- S3B_WEEKLY_FEATURES: Weekly aggregated categorical features (release candidate)
- S3C_BLOCK_FEATURES: Four-week block features (strongest coarsening)
"""

from .feature_only import (
    NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE,
    FeatureTransformResult,
    ReleaseMarker,
    S3Variant,
    scan_feature_reconstructability,
    transform_s3a_daily_bucketed,
    transform_s3b_weekly_features,
    transform_s3c_block_features,
)
from .structured import (
    OhlcvRecord,
    TransformResult,
    TransformVariant,
    transform_s0_control,
    transform_s1_basic,
    transform_s2_privacy,
)

__all__ = [
    "OhlcvRecord",
    "TransformResult",
    "TransformVariant",
    "transform_s0_control",
    "transform_s1_basic",
    "transform_s2_privacy",
    "FeatureTransformResult",
    "NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE",
    "ReleaseMarker",
    "S3Variant",
    "transform_s3a_daily_bucketed",
    "transform_s3b_weekly_features",
    "transform_s3c_block_features",
    "scan_feature_reconstructability",
]
