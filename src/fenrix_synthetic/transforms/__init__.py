"""Structured data transformation subsystem.

Three variants:
- S0_CONTROL: Non-releasable attack control
- S1_BASIC: Rebasing and normalization
- S2_PRIVACY: Log returns, winsorization, pseudo-price reconstruction
"""

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
]
