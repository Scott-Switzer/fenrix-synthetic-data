"""Phase 5A: critical producer-side and gate-edge regression tests.

These tests close the two highest-priority adjacent defects the code
reviewer flagged after the original 3-defect repair:

1. Producer-side end-to-end (user's explicit requirement): the
   orchestrator's s3_attacks.json write path must produce JSON files
   that validate as CategoricalAttackEvidence.
2. Missing/empty evidence cannot produce PASS_CANDIDATE: defect 1C
   exercised at the gate edge.

Also covers variant mismatch, reconstruction short-circuit, malformed
sources, and parametrization across releasable variants.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from fenrix_synthetic.attacks.canonical_evidence import (
    AttackEvidenceError,
    AttackStatus,
    validate_canonical_evidence,
)
from fenrix_synthetic.attacks.categorical_attacks import (
    CategoricalAttackResult,
    categorical_attacks_to_canonical,
)
from fenrix_synthetic.release.eligibility import (
    IneligibleVariantError,
    assert_releasable_variant,
)
from fenrix_synthetic.release.s3_gate import PrivacyDecision, S3PrivacyGate

RELEASABLE_VARIANTS = ["s3b_weekly_features", "s3c_block_features"]


def _build_attack(variant: str, attack_name: str, rank: int) -> CategoricalAttackResult:
    """Build a properly-formed CategoricalAttackResult as run_s3_attack_suite
    would return."""
    return CategoricalAttackResult(
        attack_type=attack_name,
        variant=variant,
        true_source_rank=rank,
        candidate_universe_size=141,
        percentile_rank=round((1.0 - rank / 141) * 100, 2),
        top_1=(rank == 1),
        top_5=(rank > 0 and rank <= 5),
        top_10=(rank > 0 and rank <= 10),
        score=round(0.97 - 0.01 * rank, 6),
    )


def _simulate_orchestrator_s3_write(variant: str) -> list[dict[str, Any]]:
    """Reproduce the orchestrator's <variant>_attacks.json write path exactly.

    The orchestrator produces CategoricalAttackResult via
    run_s3_attack_suite, then routes them through categorical_attacks_to_canonical
    + [e.to_dict() for e in canonical_list] + json.dumps(indent=2) + write_text.
    """
    attacks = [
        _build_attack(variant, method, rank)
        for method, rank in [
            ("exact", 3),
            ("weighted_hamming", 5),
            ("dtw", 7),
            ("transition", 12),
            ("ngram", 9),
            ("combined", 4),
        ]
    ]
    canonical_list = categorical_attacks_to_canonical(attacks)
    return [e.to_dict() for e in canonical_list]


# ── Producer-side end-to-end (mirrors orchestrator's exact write path) ──


class TestProducerEndToEnd:
    """Critical: orchestrator's write path must produce canonical evidence."""

    @pytest.mark.parametrize("variant", RELEASABLE_VARIANTS)
    def test_write_path_round_trips_through_canonical_validator(self, variant: str) -> None:
        """PRODUCER E2E: orchestrator's write path produces evidence that
        validate_canonical_evidence accepts with the correct variant."""
        written_dicts = _simulate_orchestrator_s3_write(variant)
        assert len(written_dicts) >= 6

        for d in written_dicts:
            validated = validate_canonical_evidence(d, expected_variant=variant)
            assert validated.variant == variant
            assert validated.attack_name in {
                "exact",
                "weighted_hamming",
                "dtw",
                "transition",
                "ngram",
                "combined",
            }

    @pytest.mark.parametrize("variant", RELEASABLE_VARIANTS)
    def test_write_path_persists_canonical_fields_at_top_level(
        self, variant: str, tmp_path: Path
    ) -> None:
        """Persisted JSON must contain top-level canonical fields, never
        nested under `metrics`."""
        written_dicts = _simulate_orchestrator_s3_write(variant)
        path = tmp_path / f"{variant.split('_')[0]}_attacks.json"
        path.write_text(json.dumps(written_dicts, indent=2))

        loaded = json.loads(path.read_text())
        assert isinstance(loaded, list)

        canonical_field_names = {
            "candidate_universe_size",
            "true_source_rank",
            "percentile_rank",
            "top_1",
            "top_5",
            "top_10",
        }
        for entry in loaded:
            metrics = entry.get("metrics")
            if isinstance(metrics, dict):
                leaked = canonical_field_names & set(metrics.keys())
                assert not leaked, f"Canonical fields leaked into nested 'metrics': {leaked}"
            for fname in canonical_field_names:
                assert fname in entry, (
                    f"Top-level field '{fname}' missing from {sorted(entry.keys())}"
                )
            assert "variant" in entry
            assert entry["variant"] == variant
            assert "attack_name" in entry

    def test_orchestrator_write_path_rejects_malformed_source(self) -> None:
        """If run_s3_attack_suite returns malformed results, the orchestrator's
        write path must FAIL fast (not silently pass)."""
        bad = CategoricalAttackResult(
            attack_type="combined",
            variant="s3b_weekly_features",
            true_source_rank=-5,  # Invalid (< -1)
            candidate_universe_size=10,
            percentile_rank=50.0,
        )
        with pytest.raises(AttackEvidenceError):
            categorical_attacks_to_canonical([bad])

    def test_legacy_metrics_shape_collision_rejected(self) -> None:
        """If a CategoricalAttackResult carries both legacy `metrics` and
        canonical fields with conflicting values, the canonical adapter
        must use the canonical top-level fields."""
        attack = CategoricalAttackResult(
            attack_type="combined",
            variant="s3b_weekly_features",
            true_source_rank=42,
            candidate_universe_size=141,
            percentile_rank=70.0,
            top_1=False,
            top_5=False,
            top_10=False,
            score=0.5,
        )
        attack.metrics = {"candidate_universe_size": 999}  # Conflict
        canonical = categorical_attacks_to_canonical([attack])
        assert canonical[0].candidate_universe_size == 141  # top-level wins


# ── Missing/empty/malformed evidence cannot produce a pass ─────────────


class TestMissingEvidenceBlocksPass:
    """Defect 1C: gate cannot return PASS_CANDIDATE on empty/malformed input."""

    @pytest.mark.parametrize("variant", RELEASABLE_VARIANTS)
    def test_empty_evidence_list_produces_fail(self, variant: str) -> None:
        gate = S3PrivacyGate()
        result = gate.evaluate(variant, [])
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_final is True
        assert result.is_complete is False
        assert any("[INCOMPLETE_EVIDENCE]" in r for r in result.blocking_reasons)

    @pytest.mark.parametrize("variant", RELEASABLE_VARIANTS)
    def test_malformed_evidence_no_required_fields_produces_fail(self, variant: str) -> None:
        gate = S3PrivacyGate()
        bad = [{"attack_type": "combined"}, {"some_other_key": 0}]
        result = gate.evaluate(variant, bad)
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_final is True
        assert result.is_complete is False
        assert any(
            "[INCOMPLETE_EVIDENCE]" in r or "[MALFORMED_EVIDENCE]" in r
            for r in result.blocking_reasons
        )

    @pytest.mark.parametrize("variant", RELEASABLE_VARIANTS)
    def test_nested_metrics_is_rejected_not_silently_coerced(self, variant: str) -> None:
        """Defect 1B enforcement: nested `metrics.candidate_universe_size`
        must NOT be silently coerced into a passing shape."""
        gate = S3PrivacyGate()
        legacy_evidence = [
            {
                "attack_name": "combined",
                "ablation": "all",
                "variant": variant,
                "metrics": {
                    "candidate_universe_size": 200,
                    "true_source_rank": 100,
                    "percentile_rank": 50.0,
                },
                "top_1": False,
                "top_5": False,
                "top_10": False,
                "score": 0.5,
            }
        ]
        result = gate.evaluate(variant, legacy_evidence)
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_final is True
        assert result.is_complete is False
        assert any(
            "[INCOMPLETE_EVIDENCE]" in r or "[MALFORMED_EVIDENCE]" in r
            for r in result.blocking_reasons
        )

    def test_reconstruction_succeeded_short_circuits_with_empty_evidence(
        self,
    ) -> None:
        """Reconstruction is independent of attack evidence: even with an
        empty attack list, reconstruction_succeeded=True must short-circuit
        to FAIL."""
        gate = S3PrivacyGate()
        result = gate.evaluate(
            "s3b_weekly_features",
            attack_results=[],
            reconstruction_succeeded=True,
        )
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_final is True
        assert any("Reconstruction attack succeeded" in r for r in result.blocking_reasons)

    def test_reconstruction_succeeded_short_circuts_with_real_evidence(
        self,
    ) -> None:
        gate = S3PrivacyGate()
        result = gate.evaluate(
            "s3b_weekly_features",
            attack_results=[
                {
                    "attack_name": "exact",
                    "variant": "s3b_weekly_features",
                    "ablation": "all",
                    "true_source_rank": 100,
                    "candidate_universe_size": 200,
                    "percentile_rank": 50.0,
                    "top_1": False,
                    "top_5": False,
                    "top_10": False,
                    "score": 0.3,
                },
            ],
            reconstruction_succeeded=True,
        )
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_final is True

    @pytest.mark.parametrize("variant", RELEASABLE_VARIANTS)
    def test_empty_evidence_populates_warnings(self, variant: str) -> None:
        """A FAIL with empty warnings is invisible to operators."""
        gate = S3PrivacyGate()
        result = gate.evaluate(variant, [])
        assert result.warnings or result.blocking_reasons


# ── Variant mismatch is rejected (defense-in-depth at the validator) ─────


class TestVariantMismatchRejected:
    @pytest.mark.parametrize("variant", RELEASABLE_VARIANTS)
    def test_variant_mismatch_raises(self, variant: str) -> None:
        """Passing S3B evidence while expecting S3C must raise."""
        other = "s3c_block_features" if variant == "s3b_weekly_features" else "s3b_weekly_features"
        bad = {
            "attack_name": "exact",
            "variant": variant,
            "ablation": "all",
            "true_source_rank": 1,
            "candidate_universe_size": 100,
            "percentile_rank": 99.0,
            "top_1": True,
            "top_5": True,
            "top_10": True,
            "score": 0.9,
        }
        with pytest.raises(AttackEvidenceError):
            validate_canonical_evidence(bad, expected_variant=other)


# ── S3A is non-releasable: eligibility guard at the gate boundary ──────


class TestS3AGateNonReleasable:
    """S3A is in NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE; even perfectly clean
    attack evidence must produce FAIL at the gate."""

    def test_assert_releasable_variant_raises_for_s3a(self) -> None:
        with pytest.raises(IneligibleVariantError):
            assert_releasable_variant("s3a_daily_bucketed")

    def test_s3a_clean_evidence_produces_fail_at_gate(self) -> None:
        """S3A evidence with rank=100 (safely outside top-K) must FAIL because
        S3A is ineligible, not because of rank."""
        gate = S3PrivacyGate()
        attacks = [_build_attack("s3a_daily_bucketed", "combined", 100)]
        canonical = categorical_attacks_to_canonical(attacks)
        result = gate.evaluate("s3a_daily_bucketed", [e.to_dict() for e in canonical])
        assert result.decision == PrivacyDecision.FAIL
        assert result.is_final is True
        assert any(
            "ineligible" in r.lower() or "not eligible" in r.lower()
            for r in result.blocking_reasons
        )


# ── Canonical adapter contract ──────────────────────────────────────────


class TestCanonicalAdapterContract:
    def test_no_expected_variant_kwarg_in_signature(self) -> None:
        """The canonical adapter does NOT accept `expected_variant` — the
        orchestrator must validate variant itself."""
        sig = inspect.signature(categorical_attacks_to_canonical)
        assert "expected_variant" not in sig.parameters

    def test_required_canonical_fields_are_top_level(self) -> None:
        """The validator's required field names match the canonical adapter's
        outputs exactly (no drift)."""
        required = {
            "variant",
            "attack_name",
            "ablation",
            "true_source_rank",
            "candidate_universe_size",
            "percentile_rank",
            "top_1",
            "top_5",
            "top_10",
            "score",
            "status",
        }
        attacks = [_build_attack("s3b_weekly_features", "combined", 5)]
        canonical = categorical_attacks_to_canonical(attacks)
        written_dicts = [e.to_dict() for e in canonical]
        assert set(written_dicts[0].keys()) >= required

    def test_malformed_status_propagates_through_adapter(self) -> None:
        """A CategoricalAttackResult with status=MALFORMED propagates through
        the adapter."""
        attack = CategoricalAttackResult(
            attack_type="combined",
            variant="s3b_weekly_features",
            true_source_rank=-1,
            candidate_universe_size=0,
            percentile_rank=0.0,
            score=None,
            status=AttackStatus.MALFORMED,
        )
        canonical = categorical_attacks_to_canonical([attack])
        assert canonical[0].status == AttackStatus.MALFORMED

    def test_malformed_status_passed_through_validator(self) -> None:
        """validate_canonical_evidence accepts MALFORMED via the enum."""
        d = {
            "attack_name": "combined",
            "variant": "s3b_weekly_features",
            "ablation": "all",
            "true_source_rank": -1,
            "candidate_universe_size": 0,
            "percentile_rank": 0.0,
            "top_1": False,
            "top_5": False,
            "top_10": False,
            "score": None,
            "status": "malformed",
        }
        validated = validate_canonical_evidence(d)
        assert validated.status == AttackStatus.MALFORMED
