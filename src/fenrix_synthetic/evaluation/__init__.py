"""Private evaluation subsystem (Phase 5A).

Contains the blinded backtest evaluator that accepts binary trade
decisions and returns only sanitized aggregate metrics.
"""

from .backtest import (
    EvaluationRequest,
    EvaluationResult,
    PrivateBacktestEvaluator,
)

__all__ = [
    "EvaluationRequest",
    "EvaluationResult",
    "PrivateBacktestEvaluator",
]
