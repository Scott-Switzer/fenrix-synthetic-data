"""Utility evaluation subsystem.

Measures how well masking preserves analytical utility
for unstructured text, structured time-series, and
feature-only categorical data (Phase 5A).

Does NOT claim transformed data reproduces actual investment performance.
"""

from .feature_only import (
    FeatureUtilityResult,
    evaluate_feature_utility,
)
from .structured import (
    StructuredUtilityResult,
    evaluate_structured_utility,
    ma_crossover_agreement,
    max_drawdown_distortion,
    momentum_agreement,
    return_sign_agreement,
    spearman_rank_correlation,
    validate_ohlcv,
    volatility_distortion,
)
from .unstructured import (
    UnstructuredUtilityResult,
    evaluate_unstructured_utility,
)

__all__ = [
    "FeatureUtilityResult",
    "StructuredUtilityResult",
    "UnstructuredUtilityResult",
    "evaluate_feature_utility",
    "evaluate_structured_utility",
    "evaluate_unstructured_utility",
    "ma_crossover_agreement",
    "max_drawdown_distortion",
    "momentum_agreement",
    "return_sign_agreement",
    "spearman_rank_correlation",
    "validate_ohlcv",
    "volatility_distortion",
]
