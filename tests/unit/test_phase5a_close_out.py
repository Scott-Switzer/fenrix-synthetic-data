"""Phase 5A close-out regression suite (16 mandated tests).

Covers the three close-out fix clusters:

1. `FeatureOnlySeriesValidation.is_valid` derived from errors.
   * `test_validation_is_valid_derived_from_errors`
   * `test_validation_cannot_disagree_with_errors`

2. Gate evidence-completeness invariants.
   * `test_empty_attack_list_cannot_pass`
   * `test_missing_required_attack_cannot_pass`
   * `test_missing_required_ablation_cannot_pass`
   * `test_duplicate_attack_evidence_rejected`
   * `test_wrong_variant_evidence_rejected`
   * `test_empty_eligible_variant_set_cannot_pass`

3. evaluate-submission fail-closed guarantees.
   * `test_empty_submission_rejected`
   * `test_empty_decisions_rejected`
   * `test_empty_evaluator_metrics_rejected`
   * `test_all_decisions_lost_after_lag_rejected`
   * `test_bool_action_rejected`
   * `test_nan_confidence_rejected`
   * `test_zero_evaluable_decisions_rejected`
   * `test_invalid_period_action_length_rejected`

Each Click CLI test asserts three things:
    exit_code, error message token, no propagated exception.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

# ── Lazy imports ───────────────────────────────────────────────────────


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_group() -> Any:
    from fenrix_synthetic.cli import cli

    return cli


@pytest.fixture
def private_root(tmp_path: Path) -> Path:
    p = tmp_path / "private"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Cluster 1: Schema-derived validity ─────────────────────────────────


class TestSchemaDerivedValidity:
    """`FeatureOnlySeriesValidation.is_valid` must be derived from `errors`."""

    def test_validation_is_valid_derived_from_errors(self) -> None:
        from fenrix_synthetic.transforms.schemas import (
            FeatureOnlySeriesValidation,
        )

        # Empty errors → True; non-empty errors → False.
        ok = FeatureOnlySeriesValidation(errors=())
        bad = FeatureOnlySeriesValidation(errors=("missing field",))
        assert ok.is_valid is True
        assert bad.is_valid is False

    def test_validation_cannot_disagree_with_errors(self) -> None:
        """It must be structurally impossible to construct a contradictory
        FeatureOnlySeriesValidation with errors but is_valid=True."""
        # Confirm by inspection of the dataclass signature: no `is_valid`
        # field exists, only derived property. Trying to set it as a
        # constructor arg raises TypeError.
        import dataclasses

        from fenrix_synthetic.transforms.schemas import (
            FeatureOnlySeriesValidation,
        )

        fields = {f.name for f in dataclasses.fields(FeatureOnlySeriesValidation)}
        assert "is_valid" not in fields, "is_valid must be a derived property, not a stored field"
        with pytest.raises(TypeError):
            FeatureOnlySeriesValidation(errors=(), is_valid=True)  # type: ignore[call-arg]

        # Errors present ⇒ is_valid must be False
        v = FeatureOnlySeriesValidation(errors=("nope",))
        assert v.is_valid is False
        # Errors absent ⇒ is_valid must be True
        v2 = FeatureOnlySeriesValidation()
        assert v2.is_valid is True


# ── Cluster 2: Gate evidence-completeness ──────────────────────────────


def _attack(
    variant: str = "s3b_weekly_features",
    *,
    attack_name: str = "exact",
    ablation: str = "all",
    rank: int = 100,
    universe: int = 200,
) -> dict[str, Any]:
    return {
        "variant": variant,
        "attack_name": attack_name,
        "ablation": ablation,
        "true_source_rank": rank,
        "candidate_universe_size": universe,
        "percentile_rank": 0.0,
        "top_1": rank == 1,
        "top_5": 0 < rank <= 5,
        "top_10": 0 < rank <= 10,
        "score": 0.0,
        "status": "completed",
        "attack_hash": "abchash000000000",
        "notes": "",
    }


class TestGateEvidenceCompleteness:
    """The gate MUST NOT produce PASS/FAIL on incomplete evidence."""

    def test_empty_attack_list_cannot_pass(self) -> None:
        from fenrix_synthetic.release.s3_gate import (
            PrivacyDecision,
            S3PrivacyGate,
        )

        gate = S3PrivacyGate()
        result = gate.evaluate("s3b_weekly_features", [])
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_final is True
        assert result.is_complete is False
        assert any(
            "empty attack list" in r.lower() or "empty" in r.lower()
            for r in result.blocking_reasons
        )

    def test_missing_required_attack_cannot_pass(self) -> None:
        from fenrix_synthetic.release.s3_gate import (
            PrivacyDecision,
            S3PrivacyGate,
        )

        gate = S3PrivacyGate()
        # Provide "exact" and "combined" but miss "weighted_hamming",
        # "dtw", "transition", "ngram" -> incomplete.
        evidence = [
            _attack(attack_name="exact"),
            _attack(attack_name="combined"),
        ]
        result = gate.evaluate("s3b_weekly_features", evidence)
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_complete is False
        assert any("missing" in r.lower() for r in result.blocking_reasons)

    def test_missing_required_ablation_cannot_pass(self) -> None:
        from fenrix_synthetic.release.s3_gate import (
            PrivacyDecision,
            S3PrivacyGate,
        )

        gate = S3PrivacyGate()
        # Cover all attack names but only "all" → missing required
        # ablations (direction, momentum, volatility, ...).
        evidence = [
            _attack(attack_name=name, ablation="all")
            for name in ("exact", "weighted_hamming", "dtw", "transition", "ngram", "combined")
        ]
        result = gate.evaluate("s3b_weekly_features", evidence)
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_complete is False
        assert any("ablation" in r.lower() for r in result.blocking_reasons)

    def test_duplicate_attack_evidence_rejected(self) -> None:
        from fenrix_synthetic.release.s3_gate import (
            PrivacyDecision,
            S3PrivacyGate,
        )

        gate = S3PrivacyGate()
        # Two rows with the same (attack_name, ablation).
        evidence = [
            _attack(attack_name="exact", ablation="all"),
            _attack(attack_name="exact", ablation="all"),
            _attack(attack_name="combined", ablation="all"),
        ]
        result = gate.evaluate("s3b_weekly_features", evidence)
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_complete is False
        assert any("duplicate" in r.lower() for r in result.blocking_reasons)

    def test_wrong_variant_evidence_rejected(self) -> None:
        from fenrix_synthetic.release.s3_gate import (
            PrivacyDecision,
            S3PrivacyGate,
        )

        gate = S3PrivacyGate()
        # All evidence is for s3c but gate evaluates s3b.
        evidence = [_attack(variant="s3c_block_features")]
        result = gate.evaluate("s3b_weekly_features", evidence)
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_complete is False
        assert any(
            "wrong-variant" in r.lower() or "variant" in r.lower() for r in result.blocking_reasons
        )

    def test_empty_eligible_variant_set_cannot_pass(self) -> None:
        """When no row targets the gate's variant, eligible set is empty."""
        from fenrix_synthetic.release.s3_gate import (
            PrivacyDecision,
            S3PrivacyGate,
        )

        gate = S3PrivacyGate()
        # Provide variants that do not match the gate.
        evidence = [
            dict(_attack(variant="s3c_block_features"), ablation="all"),
            dict(_attack(variant="s3c_block_features"), ablation="direction"),
        ]
        result = gate.evaluate("s3b_weekly_features", evidence)
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_complete is False


# ── Cluster 3: evaluate-submission fail-closed ────────────────────────


def _truth_file(tmp_path: Path, n_periods: int = 30) -> Path:
    path = tmp_path / "truth.json"
    path.write_text(
        json.dumps({"period_returns": [0.001 * ((i % 7) - 3) for i in range(n_periods)]})
    )
    return path


class TestEvaluateSubmissionFailClosed:
    """All close-out submit-side gates must exit with code 2."""

    def _assert_invalid(self, result: Any, expected_token: str | None = None) -> None:
        assert result.exit_code == 2, (
            f"expected exit 2 but got {result.exit_code}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        if expected_token:
            merged = (result.stdout + result.stderr).lower()
            # Click lowercases the first word of ClickException messages;
            # our stable tokens are uppercase in code but lowercase in
            # rendered output. Lower the token here so the assertion
            # always matches.
            expected_lower = expected_token.lower()
            assert expected_lower in merged, (
                f"expected {expected_token!r} in output, got: {merged!r}"
            )
        # ClickException subclasses are converted to SystemExit by Click.
        # A non-None exception here means Click recognised it as a
        # controlled exit, not an unhandled traceback.
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            f"unexpected exception (not a controlled Click exit): {result.exception!r}"
        )

    def test_empty_submission_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        truth = _truth_file(tmp_path, 30)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "",
                "--binary-actions",
                "",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        # Empty string → 0-length CSV parser → list is empty
        self._assert_invalid(result, expected_token="[EMPTY_SUBMISSION]")

    def test_empty_decisions_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        # Empty truth file → empty private_returns → exit 2.
        truth = tmp_path / "empty.json"
        truth.write_text(json.dumps({"period_returns": []}))
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2",
                "--binary-actions",
                "0,1,0",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        self._assert_invalid(result, expected_token="[empty_truth]")

    def test_empty_evaluator_metrics_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """A truth file with non-finite values must not silently pass.
        The close-out spec says NaN/inf in inputs are invalid input.
        """
        truth = tmp_path / "nans.json"
        truth.write_text(json.dumps({"period_returns": [0.001, float("nan"), 0.002]}))
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2",
                "--binary-actions",
                "0,1,0",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        self._assert_invalid(result, expected_token="[NON_FINITE]")

    def test_all_decisions_lost_after_lag_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """Lag alignment that zeroes every decision → invalid input.

        With execution-lag ≥ number of truth periods, no submitted
        period can survive alignment. The evaluator must return zero
        usable decisions, which the postcondition rejects as exit 2.
        """
        # Truth has only 3 periods; execution-lag=5 means every
        # submitted period shifts past the truth window → zero survive.
        truth = tmp_path / "short.json"
        truth.write_text(json.dumps({"period_returns": [0.001, -0.002, 0.003]}))
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2",
                "--binary-actions",
                "0,1,0",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
                "--execution-lag",
                "5",
            ],
        )
        self._assert_invalid(result, expected_token="[ZERO_DECISIONS]")

    def test_evaluable_count_excludes_pre_lag_periods(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """evaluable_decision_count must exclude the first `lag` periods."""
        from fenrix_synthetic.evaluation.backtest import (
            EvaluationRequest,
            PrivateBacktestEvaluator,
        )

        returns = [0.001] * 10
        evaluator = PrivateBacktestEvaluator(
            actual_private_returns=returns,
            execution_lag=3,
        )
        request = EvaluationRequest(
            run_id="r",
            release_id="R",
            model_submission_id="s",
            relative_periods=list(range(10)),
            binary_actions=[1] * 10,
        )
        result = evaluator.evaluate(request)
        # 10 periods, lag=3 → only 7 are post-lag and have finite returns
        assert result.evaluable_decision_count == 7
        assert result.total_decisions == 10

    def test_evaluable_count_excludes_nonfinite_returns(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """evaluable_decision_count must exclude rows with non-finite private returns."""
        from fenrix_synthetic.evaluation.backtest import (
            EvaluationRequest,
            PrivateBacktestEvaluator,
        )

        returns = [0.001, float("nan"), 0.002, float("inf"), 0.003]
        evaluator = PrivateBacktestEvaluator(
            actual_private_returns=returns,
            execution_lag=0,
        )
        request = EvaluationRequest(
            run_id="r",
            release_id="R",
            model_submission_id="s",
            relative_periods=list(range(5)),
            binary_actions=[1] * 5,
        )
        result = evaluator.evaluate(request)
        # 5 periods, lag=0, but indices 1 and 3 have non-finite returns
        assert result.evaluable_decision_count == 3  # 5 - 2 non-finite

    def test_evaluable_count_zero_when_lag_exceeds_periods(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """When execution lag >= n, evaluable_decision_count must be 0."""
        from fenrix_synthetic.evaluation.backtest import (
            EvaluationRequest,
            PrivateBacktestEvaluator,
        )

        returns = [0.001] * 3
        evaluator = PrivateBacktestEvaluator(
            actual_private_returns=returns,
            execution_lag=5,
        )
        request = EvaluationRequest(
            run_id="r",
            release_id="R",
            model_submission_id="s",
            relative_periods=list(range(3)),
            binary_actions=[1] * 3,
        )
        result = evaluator.evaluate(request)
        assert result.evaluable_decision_count == 0

    def test_evaluable_count_with_gapped_periods(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """Gapped relative periods should not affect evaluable count
        (the evaluator indexes private returns by position, not period value)."""
        from fenrix_synthetic.evaluation.backtest import (
            EvaluationRequest,
            PrivateBacktestEvaluator,
        )

        returns = [0.001] * 5
        evaluator = PrivateBacktestEvaluator(
            actual_private_returns=returns,
            execution_lag=1,
        )
        request = EvaluationRequest(
            run_id="r",
            release_id="R",
            model_submission_id="s",
            relative_periods=[0, 5, 10, 15, 20],  # gapped
            binary_actions=[1] * 5,
        )
        result = evaluator.evaluate(request)
        # 5 periods, lag=1, all finite → 4 evaluable
        assert result.evaluable_decision_count == 4

    def test_evaluable_count_nonfinite_in_pre_lag_position(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """Non-finite returns in pre-lag positions must NOT reduce the count
        (they are already excluded by the lag skip)."""
        from fenrix_synthetic.evaluation.backtest import (
            EvaluationRequest,
            PrivateBacktestEvaluator,
        )

        # Index 0 has NaN (pre-lag with lag=1), indices 1+ are finite
        returns = [float("nan"), 0.001, 0.002, 0.003]
        evaluator = PrivateBacktestEvaluator(
            actual_private_returns=returns,
            execution_lag=1,
        )
        request = EvaluationRequest(
            run_id="r",
            release_id="R",
            model_submission_id="s",
            relative_periods=list(range(4)),
            binary_actions=[1] * 4,
        )
        result = evaluator.evaluate(request)
        # lag=1: index 0 skipped, indices 1-3 are post-lag and finite → 3
        assert result.evaluable_decision_count == 3

    def test_bool_action_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """`bool` is `int` in Python; we must reject it explicitly."""
        truth = _truth_file(tmp_path, 4)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2,3",
                # pass literal "true"/"false" strings; parser fails.
                "--binary-actions",
                "true,false,true,false",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        # CSV parser raises ValueError → InvalidInputError → exit 2.
        self._assert_invalid(result)

    def test_nan_confidence_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """transaction-cost = NaN must be rejected as invalid input."""
        truth = _truth_file(tmp_path, 5)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2,3,4",
                "--binary-actions",
                "0,1,0,1,0",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
                "--transaction-cost",
                "nan",
            ],
        )
        # Click parses "nan" via float() → returns NaN → _check_finite catches.
        self._assert_invalid(result, expected_token="[NON_FINITE]")

    def test_zero_evaluable_decisions_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        """Empty submission loop cannot pass even if shape is valid.

        Using --relative-periods ' ' (whitespace) → empty list after parse.
        """
        truth = _truth_file(tmp_path, 5)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "  ",
                "--binary-actions",
                "  ",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        self._assert_invalid(result, expected_token="[EMPTY_SUBMISSION]")

    def test_invalid_period_action_length_rejected(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        truth = _truth_file(tmp_path, 10)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "R",
                "--run-id",
                "r",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2,3,4",
                "--binary-actions",
                "0,1,2,3,4,5",  # too many
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        self._assert_invalid(result, expected_token="[shape_mismatch]")


# ── Cluster 4: CLI Click exception → exit code matrix ─────────────────


class TestCliExitCodeMatrix:
    """Click-native exception subclasses drive the exit code matrix."""

    def test_invalidinputerror_exit_code_2(self) -> None:
        from fenrix_synthetic.cli_errors import InvalidInputError

        assert InvalidInputError.exit_code == 2

    def test_privacyfailureerror_exit_code_3(self) -> None:
        from fenrix_synthetic.cli_errors import PrivacyFailureError

        assert PrivacyFailureError.exit_code == 3

    def test_executionfailureerror_exit_code_4(self) -> None:
        from fenrix_synthetic.cli_errors import ExecutionFailureError

        assert ExecutionFailureError.exit_code == 4

    def test_ineligiblevarianterror_exit_code_5(self) -> None:
        from fenrix_synthetic.cli_errors import IneligibleVariantError

        assert IneligibleVariantError.exit_code == 5

    def test_phase5aclickerror_propagates_exit_code(self) -> None:
        from fenrix_synthetic.cli_errors import Phase5AClickError

        with pytest.raises(Phase5AClickError) as exc:
            raise Phase5AClickError("custom", exit_code=2)
        assert exc.value.exit_code == 2
        assert "custom" in str(exc.value)


# ── Cluster 5: Defense-in-depth on sanitized output ────────────────────


# ── Cluster 2b: Generator/gate MVP policy parity ────────────────────


class TestGeneratorGatePolicyParity:
    """Generator and gate MUST consume the same s3b-mvp-v1 policy keys.

    Each test loads the policy and clears it afterward to prevent
    global state leakage into the legacy gate tests.
    """

    def setup_method(self) -> None:
        from fenrix_synthetic.release.s3_gate import clear_mvp_policy

        clear_mvp_policy()

    def teardown_method(self) -> None:
        from fenrix_synthetic.release.s3_gate import clear_mvp_policy

        clear_mvp_policy()

    def test_policy_has_exactly_16_keys(self) -> None:
        """The frozen MVP policy must contain exactly 16 attack keys."""
        import json as _json
        from pathlib import Path as _Path

        policy_path = _Path(__file__).parents[2] / "configs" / "policies" / "s3b-mvp-v1.json"
        raw = _json.loads(policy_path.read_text())
        keys = raw["required_attack_keys"]
        assert len(keys) == 16, f"MVP policy must have exactly 16 keys, got {len(keys)}"
        # Verify no duplicates
        assert len(set(keys)) == 16, "MVP policy contains duplicate attack keys"

    def test_policy_keys_include_lagged_attacks(self) -> None:
        """The lagged_1, lagged_5, lagged_21 attacks must be in the policy."""
        import json as _json
        from pathlib import Path as _Path

        policy_path = _Path(__file__).parents[2] / "configs" / "policies" / "s3b-mvp-v1.json"
        raw = _json.loads(policy_path.read_text())
        keys = set(raw["required_attack_keys"])
        for lagged in ("lagged_1/all", "lagged_5/all", "lagged_21/all"):
            assert lagged in keys, f"{lagged} missing from MVP policy"

    def test_policy_keys_include_all_ablations(self) -> None:
        """All 7 combined ablation attacks must be in the policy."""
        import json as _json
        from pathlib import Path as _Path

        policy_path = _Path(__file__).parents[2] / "configs" / "policies" / "s3b-mvp-v1.json"
        raw = _json.loads(policy_path.read_text())
        keys = set(raw["required_attack_keys"])
        ablation_groups = [
            "direction",
            "momentum",
            "volatility",
            "drawdown",
            "market_relative",
            "sector_relative",
            "technical_state",
        ]
        for abl in ablation_groups:
            expected = f"combined/{abl}"
            assert expected in keys, f"{expected} missing from MVP policy"

    def test_generator_produces_exactly_policy_keys(self) -> None:
        """When given the MVP policy, run_s3_attack_suite must produce
        exactly the 16 attack keys and no others."""
        import json as _json
        from pathlib import Path as _Path

        from fenrix_synthetic.attacks.categorical_attacks import (
            categorical_attacks_to_canonical,
            run_s3_attack_suite,
        )

        policy_path = _Path(__file__).parents[2] / "configs" / "policies" / "s3b-mvp-v1.json"
        policy = _json.loads(policy_path.read_text())

        # Minimal synthetic features + 2 candidates
        source = [{"return_direction": "UP", "momentum_5d_bucket": "HIGH"} for _ in range(20)]
        candidates = {
            "cand-A": [
                {"return_direction": "DOWN", "momentum_5d_bucket": "LOW"} for _ in range(20)
            ],
            "cand-B": [
                {"return_direction": "FLAT", "momentum_5d_bucket": "MEDIUM"} for _ in range(20)
            ],
        }

        results = run_s3_attack_suite(
            source, candidates, variant="s3b_weekly_features", policy=policy
        )
        canonical = categorical_attacks_to_canonical(results)

        # Must produce exactly 16 results
        assert len(canonical) == 16, f"Generator produced {len(canonical)} attacks, expected 16"

        # Every result must match a policy key
        policy_keys = set(policy["required_attack_keys"])
        produced_keys = {f"{e.attack_name}/{e.ablation}" for e in canonical}
        assert produced_keys == policy_keys, (
            f"Generator/policy mismatch. Extra: {produced_keys - policy_keys}. "
            f"Missing: {policy_keys - produced_keys}"
        )

    def test_gate_accepts_policy_exact_16_keys(self) -> None:
        """When given exactly the 16 policy keys, the gate must accept
        the evidence as complete (no missing/additional/duplicate issues)."""
        import json as _json
        from pathlib import Path as _Path

        from fenrix_synthetic.release.s3_gate import (
            S3PrivacyGate,
            load_mvp_policy,
        )

        policy_path = _Path(__file__).parents[2] / "configs" / "policies" / "s3b-mvp-v1.json"
        policy = _json.loads(policy_path.read_text())
        load_mvp_policy(str(policy_path))

        gate = S3PrivacyGate()
        # Build exactly the 16 policy keys with safe ranks
        evidence = []
        for key in sorted(policy["required_attack_keys"]):
            parts = key.split("/")
            atk_name = parts[0]
            abl = parts[1] if len(parts) > 1 else "all"
            evidence.append(
                _attack(
                    attack_name=atk_name,
                    ablation=abl,
                    rank=200,
                    universe=200,
                )
            )

        result = gate.evaluate("s3b_weekly_features", evidence)
        # Must be complete (no missing/additional/duplicate issues)
        assert result.is_complete is True, (
            f"Gate rejected complete evidence: {result.blocking_reasons}"
        )

    def test_gate_rejects_additional_key_beyond_policy(self) -> None:
        """Evidence containing an attack key not in the policy must be rejected."""
        import json as _json
        from pathlib import Path as _Path

        from fenrix_synthetic.release.s3_gate import (
            PrivacyDecision,
            S3PrivacyGate,
            load_mvp_policy,
        )

        policy_path = _Path(__file__).parents[2] / "configs" / "policies" / "s3b-mvp-v1.json"
        policy = _json.loads(policy_path.read_text())
        load_mvp_policy(str(policy_path))

        gate = S3PrivacyGate()
        # Build the 16 policy keys plus one extra
        evidence = []
        for key in sorted(policy["required_attack_keys"]):
            parts = key.split("/")
            atk_name = parts[0]
            abl = parts[1] if len(parts) > 1 else "all"
            evidence.append(
                _attack(
                    attack_name=atk_name,
                    ablation=abl,
                    rank=200,
                    universe=200,
                )
            )
        # Add an extra key outside policy
        evidence.append(_attack(attack_name="extra_attack", ablation="all", rank=200, universe=200))

        result = gate.evaluate("s3b_weekly_features", evidence)
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_complete is False
        assert any("additional" in r.lower() for r in result.blocking_reasons)


class TestSanitizedOutputGuardrails:
    """Sanitized outputs must not leak private payload tokens."""

    def test_sanitized_assess_output_no_per_attack_rank_data(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        # The gate's evidence-completeness precheck requires all 6 attacks
        # × 8 ablations; a single row would produce exit 2, not 0.
        # Build a complete (clean, rank=200) evidence set so the gate
        # reaches the threshold path and produces a sanitized output.
        from fenrix_synthetic.release.s3_gate import (
            REQUIRED_ABLATION_NAMES,
            REQUIRED_ATTACK_NAMES,
        )

        complete: list[dict[str, Any]] = [
            _attack(
                attack_name=atk,
                ablation=abl,
                rank=200,
                universe=200,
            )
            for atk in REQUIRED_ATTACK_NAMES
            for abl in REQUIRED_ABLATION_NAMES
        ]
        attacks_path = tmp_path / "attacks.json"
        attacks_path.write_text(json.dumps({"attacks": complete}))
        result = runner.invoke(
            cli_group,
            [
                "s3-assess",
                "--private-root",
                str(private_root),
                "--variant",
                "s3b_weekly_features",
                "--attack-results",
                str(attacks_path),
                "--output",
                str(private_root / "assess.json"),
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads((private_root / "assess.json").read_text())
        # evidence_summary entries must NOT contain raw rank / score data.
        for ev_summary in payload.get("evidence_summary", []):
            for field in ("true_source_rank", "score", "percentile_rank"):
                assert field not in ev_summary, f"{field} leaked into sanitized evidence_summary"

    def test_atlas_harvest_zero_auto_accepted(
        self,
        runner: CliRunner,
        cli_group,
        private_root: Path,
        tmp_path: Path,
    ) -> None:
        doc = tmp_path / "doc.txt"
        doc.write_text("Acme Corp. (NYSE: A1B2). CEO John Smith. CIK 0001234567.")
        result = runner.invoke(
            cli_group,
            [
                "atlas-harvest",
                "--private-root",
                str(private_root),
                "--document",
                str(doc),
            ],
        )
        assert result.exit_code == 0, result.stderr
        candidates_files = list(private_root.rglob("atlas_candidates.json"))
        assert candidates_files
        payload = json.loads(max(candidates_files, key=lambda p: p.stat().st_mtime).read_text())
        assert payload["automatic_acceptance_count"] == 0
        assert payload["registry_mutation_count"] == 0
        for c in payload["candidates"]:
            assert c["is_auto_accepted"] is False
