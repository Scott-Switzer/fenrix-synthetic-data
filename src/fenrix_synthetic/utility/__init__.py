"""Utility evaluation subsystem.

Measures how well the masking preserves analytical utility
for both unstructured text and structured time-series data.

Does NOT claim transformed data reproduces actual investment performance.
"""

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
    "StructuredUtilityResult",
    "UnstructuredUtilityResult",
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
