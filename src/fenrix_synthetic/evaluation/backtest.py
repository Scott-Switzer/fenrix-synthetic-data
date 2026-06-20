"""Private backtest evaluator (Phase 5A, Part 10).

Accepts binary trade decisions and evaluates them against unreleased
real returns. Never exports raw returns, dates, equity curves, or
per-period P&L. Only aggregate sanitized metrics are returned.

Design:
- Input: binary actions (0=cash, 1=long) indexed by relative period
- Private: actual future returns, period mapping, costs, execution lag
- Output: sanitized aggregate metrics only
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvaluationRequest:
    """A model submission to the private evaluator.

    Attributes:
        run_id: The pilot run this submission belongs to.
        release_id: The release ID being evaluated.
        model_submission_id: Unique identifier for this model/signal.
        relative_periods: Ordered list of relative period indices.
        binary_actions: List of binary decisions (0=cash, 1=long).
        confidence: Optional confidence scores (diagnostics only).
    """

    run_id: str
    release_id: str
    model_submission_id: str
    relative_periods: list[int]
    binary_actions: list[int]
    confidence: list[float] | None = None


@dataclass
class EvaluationResult:
    """Sanitized backtest evaluation result.

    Only aggregate metrics are exposed. No raw returns, dates,
    equity curves, or per-period P&L.
    """

    run_id: str
    release_id: str
    model_submission_id: str
    evaluator_hash: str

    # Decision statistics
    total_decisions: int = 0
    evaluable_decision_count: int = 0  # post-lag decisions that actually carry a signal
    trade_rate: float = 0.0

    # Performance metrics (sanitized precision)
    directional_accuracy: float = 0.0
    hit_rate: float = 0.0
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    volatility_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0

    # Turnover and costs
    turnover: float = 0.0
    cost_impact_pct: float = 0.0

    # Benchmark-relative
    benchmark_total_return_pct: float = 0.0
    benchmark_annualized_return_pct: float = 0.0
    benchmark_volatility_pct: float = 0.0
    benchmark_sharpe_ratio: float = 0.0
    benchmark_max_drawdown_pct: float = 0.0

    # In/out of sample
    in_sample_decisions: int = 0
    in_sample_total_return_pct: float = 0.0
    in_sample_sharpe_ratio: float = 0.0
    out_of_sample_decisions: int = 0
    out_of_sample_total_return_pct: float = 0.0
    out_of_sample_sharpe_ratio: float = 0.0

    # Validation
    is_valid: bool = True
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Precision control
    precision_decimals: int = 2

    def to_sanitized_dict(self) -> dict[str, Any]:
        """Return sanitized dict with controlled precision."""
        metrics = {
            "run_id": self.run_id,
            "release_id": self.release_id,
            "model_submission_id": self.model_submission_id,
            "evaluator_hash": self.evaluator_hash,
            "is_valid": self.is_valid,
        }

        # Round all float values to configured precision
        float_attrs = [
            "trade_rate",
            "directional_accuracy",
            "hit_rate",
            "total_return_pct",
            "annualized_return_pct",
            "volatility_pct",
            "sharpe_ratio",
            "max_drawdown_pct",
            "turnover",
            "cost_impact_pct",
            "benchmark_total_return_pct",
            "benchmark_annualized_return_pct",
            "benchmark_volatility_pct",
            "benchmark_sharpe_ratio",
            "benchmark_max_drawdown_pct",
            "in_sample_total_return_pct",
            "in_sample_sharpe_ratio",
            "out_of_sample_total_return_pct",
            "out_of_sample_sharpe_ratio",
        ]
        for attr in float_attrs:
            val = getattr(self, attr, 0.0)
            metrics[attr] = round(val, self.precision_decimals)

        int_attrs = [
            "total_decisions",
            "evaluable_decision_count",
            "in_sample_decisions",
            "out_of_sample_decisions",
        ]
        for attr in int_attrs:
            metrics[attr] = getattr(self, attr, 0)

        if self.validation_errors:
            metrics["validation_errors"] = self.validation_errors
        if self.warnings:
            metrics["warnings"] = self.warnings

        return metrics


class PrivateBacktestEvaluator:
    """Private backtest evaluator (blinded).

    Evaluates binary trade signals against real private returns.
    Never exports raw returns, dates, equity curves, or per-period P&L.

    The evaluator must be initialized with the private returns data:
    - actual_private_returns: list of period returns (same length as request periods)
    - in_sample_end: last in-sample period index
    - transaction_cost: cost per trade as decimal (e.g., 0.001 = 10bps)
    - execution_lag: periods between signal and execution
    - benchmark_returns: optional list of benchmark returns for comparison
    """

    def __init__(
        self,
        actual_private_returns: list[float],
        in_sample_end: int = 0,
        transaction_cost: float = 0.001,
        execution_lag: int = 1,
        benchmark_returns: list[float] | None = None,
        precision_decimals: int = 2,
    ):
        if not actual_private_returns:
            raise ValueError("actual_private_returns must not be empty")

        self._private_returns = actual_private_returns
        self._in_sample_end = min(in_sample_end, len(actual_private_returns) - 1)
        self._transaction_cost = transaction_cost
        self._execution_lag = execution_lag
        self._benchmark_returns = benchmark_returns
        self._precision_decimals = precision_decimals

        # Config hash (does NOT include actual returns)
        config = {
            "in_sample_end": in_sample_end,
            "transaction_cost": transaction_cost,
            "execution_lag": execution_lag,
            "precision": precision_decimals,
        }
        raw = json.dumps(config, sort_keys=True)
        self._evaluator_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]

    @property
    def evaluator_hash(self) -> str:
        return self._evaluator_hash

    def evaluate(
        self,
        request: EvaluationRequest,
    ) -> EvaluationResult:
        """Evaluate a model submission against private returns."""
        result = EvaluationResult(
            run_id=request.run_id,
            release_id=request.release_id,
            model_submission_id=request.model_submission_id,
            evaluator_hash=self._evaluator_hash,
            precision_decimals=self._precision_decimals,
        )

        # Validate request
        self._validate_request(request, result)
        if not result.is_valid:
            return result

        periods = request.relative_periods
        actions = request.binary_actions
        n = min(len(periods), len(actions), len(self._private_returns))

        # Apply execution lag
        executed_actions: list[int] = [0] * n
        for i in range(self._execution_lag, n):
            executed_actions[i] = actions[i - self._execution_lag]

        result.total_decisions = n
        # evaluable_decision_count: post-lag rows with finite private returns.
        # This is the authoritative count of decisions that actually survive
        # alignment and can be scored, not a simple n - lag approximation.
        result.evaluable_decision_count = sum(
            1 for i in range(self._execution_lag, n) if math.isfinite(self._private_returns[i])
        )

        # Trade rate
        trades = sum(1 for a in executed_actions if a == 1)
        result.trade_rate = round(trades / max(1, n), self._precision_decimals)

        # Calculate returns
        strategy_returns: list[float] = []
        benchmark_rets: list[float] = []

        for i in range(n):
            action = executed_actions[i]
            ret = self._private_returns[i]

            if action == 1:
                strategy_ret = ret - self._transaction_cost
            else:
                strategy_ret = 0.0  # Cash return (no risk-free rate modeled)

            strategy_returns.append(strategy_ret)

            if self._benchmark_returns and i < len(self._benchmark_returns):
                benchmark_rets.append(self._benchmark_returns[i])

        # Directional accuracy: fraction of trades where return sign matches
        trade_returns = [strategy_returns[i] for i in range(n) if executed_actions[i] == 1]
        if trade_returns:
            positive_trades = sum(1 for r in trade_returns if r > 0)
            result.directional_accuracy = round(
                positive_trades / len(trade_returns), self._precision_decimals
            )

        # Hit rate: fraction of periods with positive return
        positive_periods = sum(1 for r in strategy_returns if r > 0)
        result.hit_rate = round(positive_periods / max(1, n), self._precision_decimals)

        # Total and annualized return
        total_ret = sum(strategy_returns)
        result.total_return_pct = round(total_ret * 100, self._precision_decimals)
        result.annualized_return_pct = round(
            (total_ret / max(1, n)) * 252 * 100, self._precision_decimals
        )

        # Volatility
        if n > 1:
            mean_ret = sum(strategy_returns) / n
            variance = sum((r - mean_ret) ** 2 for r in strategy_returns) / (n - 1)
            daily_vol = variance**0.5
            result.volatility_pct = round(daily_vol * (252**0.5) * 100, self._precision_decimals)

        # Sharpe ratio
        if result.volatility_pct > 0:
            excess = result.annualized_return_pct  # No risk-free rate
            result.sharpe_ratio = round(
                excess / max(0.01, result.volatility_pct), self._precision_decimals
            )

        # Maximum drawdown
        cum_ret = 0.0
        peak = 0.0
        for r in strategy_returns:
            cum_ret += r
            peak = max(peak, cum_ret)
            dd = cum_ret - peak
            result.max_drawdown_pct = min(
                result.max_drawdown_pct, round(dd * 100, self._precision_decimals)
            )

        # Turnover
        changes = sum(1 for i in range(1, n) if executed_actions[i] != executed_actions[i - 1])
        result.turnover = round(changes / max(1, n), self._precision_decimals)

        # Cost impact
        total_costs = trades * self._transaction_cost
        result.cost_impact_pct = round(total_costs / max(1, n) * 100, self._precision_decimals)

        # Benchmark comparison
        if benchmark_rets:
            bench_total = sum(benchmark_rets)
            result.benchmark_total_return_pct = round(bench_total * 100, self._precision_decimals)
            result.benchmark_annualized_return_pct = round(
                (bench_total / max(1, n)) * 252 * 100, self._precision_decimals
            )
            if n > 1:
                mean_b = sum(benchmark_rets) / len(benchmark_rets)
                var_b = sum((r - mean_b) ** 2 for r in benchmark_rets) / (len(benchmark_rets) - 1)
                bench_vol = (var_b**0.5) * (252**0.5)
                result.benchmark_volatility_pct = round(bench_vol * 100, self._precision_decimals)
                if bench_vol > 0:
                    result.benchmark_sharpe_ratio = round(
                        result.benchmark_annualized_return_pct / max(0.01, bench_vol * 100),
                        self._precision_decimals,
                    )

        # In-sample / out-of-sample split
        in_sample = strategy_returns[: self._in_sample_end]
        out_sample = strategy_returns[self._in_sample_end :]

        result.in_sample_decisions = len(in_sample)
        if in_sample:
            result.in_sample_total_return_pct = round(
                sum(in_sample) * 100, self._precision_decimals
            )
            if len(in_sample) > 1:
                mean_is = sum(in_sample) / len(in_sample)
                var_is = sum((r - mean_is) ** 2 for r in in_sample) / (len(in_sample) - 1)
                vol_is = (var_is**0.5) * (252**0.5)
                if vol_is > 0:
                    annual_is = (sum(in_sample) / len(in_sample)) * 252
                    result.in_sample_sharpe_ratio = round(
                        annual_is / max(0.01, vol_is), self._precision_decimals
                    )

        result.out_of_sample_decisions = len(out_sample)
        if out_sample:
            result.out_of_sample_total_return_pct = round(
                sum(out_sample) * 100, self._precision_decimals
            )
            if len(out_sample) > 1:
                mean_oos = sum(out_sample) / len(out_sample)
                var_oos = sum((r - mean_oos) ** 2 for r in out_sample) / (len(out_sample) - 1)
                vol_oos = (var_oos**0.5) * (252**0.5)
                if vol_oos > 0:
                    annual_oos = (sum(out_sample) / len(out_sample)) * 252
                    result.out_of_sample_sharpe_ratio = round(
                        annual_oos / max(0.01, vol_oos), self._precision_decimals
                    )

        return result

    def _validate_request(
        self,
        request: EvaluationRequest,
        result: EvaluationResult,
    ) -> None:
        """Validate the evaluation request."""
        errors: list[str] = []

        if not request.relative_periods:
            errors.append("No relative periods provided")
        if not request.binary_actions:
            errors.append("No binary actions provided")
        if len(request.relative_periods) != len(request.binary_actions):
            errors.append(
                f"Period count ({len(request.relative_periods)}) != "
                f"action count ({len(request.binary_actions)})"
            )
        if len(request.relative_periods) > len(self._private_returns):
            errors.append(
                f"Request periods ({len(request.relative_periods)}) > "
                f"available private periods ({len(self._private_returns)})"
            )

        invalid_actions = [a for a in request.binary_actions if a not in (0, 1)]
        if invalid_actions:
            errors.append(f"Invalid binary actions: {set(invalid_actions)}")

        # Check period ordering
        for i in range(1, len(request.relative_periods)):
            if request.relative_periods[i] <= request.relative_periods[i - 1]:
                errors.append(
                    f"Periods not monotonically increasing at index {i}: "
                    f"{request.relative_periods[i - 1]} -> {request.relative_periods[i]}"
                )
                break

        result.validation_errors = errors
        result.is_valid = len(errors) == 0
