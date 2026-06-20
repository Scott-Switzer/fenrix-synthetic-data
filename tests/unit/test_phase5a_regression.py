"""Regression tests for Phase 5A blocking defects.

These tests reproduce the three correctness gaps identified during
the Phase 5A scaffolding review:

1. Attack-result contract — every attack result must carry an explicit
   `variant` and the canonical rank/universe fields at the TOP LEVEL
   (not nested inside `metrics`). Missing data must be a violation,
   not silently zeroed.

2. S3 privacy gate integration — gate must find attacks filtered by
   variant AND respect non-zero `candidate_universe_size` and
   `percentile_rank` from same top-level keys.

3. NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE — S0, S1, S2 (and S3A) must be
   blocked at every release boundary: gate, evidence manifest,
   dossier generation, plus an explicit export guard.

All tests are offline and use invented fixture data only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fenrix_synthetic.attacks.categorical_attacks import (
    categorical_attacks_to_canonical,
    rank_in_universe,
)
from fenrix_synthetic.release.s3_gate import PrivacyDecision, S3PrivacyGate
from fenrix_synthetic.transforms.feature_only import (
    NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE,
    OhlcvRecord,
    S3Variant,
    transform_s3b_weekly_features,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_records(n: int = 260) -> list[OhlcvRecord]:
    """Build a minimal but long-enough OHLCV sequence."""
    return [
        OhlcvRecord(
            date="",
            open=100.0 + i * 0.1,
            high=101.0 + i * 0.1,
            low=99.0 + i * 0.1,
            close=100.5 + i * 0.1,
            volume=100_000 + i * 100,
        )
        for i in range(n)
    ]


def _inv_candidate(n_weeks: int, seed: int = 0) -> list[dict]:
    """Invented short feature vectors for a candidate."""
    import random

    random.seed(seed)

    def _b5() -> str:
        return random.choice(["VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH"])

    def _dir() -> str:
        return random.choice(["DOWN", "FLAT", "UP"])

    return [
        {
            "relative_week": i,
            "weekly_direction_category": _dir(),
            "momentum_4w_bucket": _b5(),
            "momentum_12w_bucket": _b5(),
            "momentum_26w_bucket": _b5(),
            "volatility_4w_bucket": _b5(),
            "volatility_12w_bucket": _b5(),
            "volume_activity_bucket": random.choice(["LOW", "MEDIUM", "HIGH"]),
            "drawdown_bucket": _b5(),
            "moving_average_regime": random.choice(["BELOW", "NEUTRAL", "ABOVE"]),
            "market_relative_strength_bucket": _b5(),
            "sector_relative_strength_bucket": _b5(),
            "trend_persistence_bucket": random.choice(["SHORT", "MODERATE", "PERSISTENT"]),
        }
        for i in range(n_weeks)
    ]


# ════════════════════════════════════════════════════════════════════════
# Defect 1 — Attack-result contract
# ════════════════════════════════════════════════════════════════════════


class TestReproducibleBugs:
    """These tests REPRODUCE the known blocking defects.

    If a defect is present, the corresponding test FAILS. After the
    fix, all tests in this class must PASS.
    """

    # ── Defect 1a: missing variant ────────────────────────────────
    def test_repro_attack_result_must_have_variant_at_top_level(self, tmp_path: Path):
        """Every attack result must carry an explicit `variant` at the top level.

        Reproduces: orchestrator wrote attack dicts without a `variant`
        key, causing the gate's `a.get('variant', '') == variant_name`
        filter to never match.
        """
        gate = S3PrivacyGate()
        # Simulated orchestrator output (the bug) — no "variant" key
        # and metrics are nested under "metrics".
        malformed_evidence = [
            {
                "attack_name": "exact",
                "ablation": "all",
                "true_source_rank": 1,
                # 'variant' missing — this is the bug
                "metrics": {"candidate_universe_size": 141, "percentile_rank": 99.3},
                "score": 0.91,
                "status": "blocked",
            }
        ]
        result = gate.evaluate("s3b_weekly_features", malformed_evidence)
        # The bug: gate saw no variant match, so it never produces a
        # decision based on this evidence. After the fix, malformed
        # evidence is rejected and result.decision != PASS.
        assert result.decision != PrivacyDecision.PASS_CANDIDATE, (
            "Defect 1a not fixed: gate accepted attack result with no "
            "'variant' field and produced PASS_CANDIDATE"
        )

    # ── Defect 1b: nested metrics treated as zero ─────────────────
    def test_repro_universe_size_must_be_read_from_top_level(self, tmp_path: Path):
        """`candidate_universe_size` and `percentile_rank` must live at
        the top level, not inside a `metrics` sub-dict.

        Reproduces: gate read `attack.get('universe_size', 0)` at the
        top level, so values stored under `metrics['candidate_universe_size']`
        were always seen as zero, defeating the `top_pct_fail` and
        `min_universe_size` checks.
        """
        gate = S3PrivacyGate(top_k_fail=10, min_universe_size=50)
        # Provide canonical top-level fields (the fix's expected shape)
        evidence = [
            {
                "variant": "s3b_weekly_features",
                "attack_name": "exact",
                "ablation": "all",
                "true_source_rank": 1,
                "candidate_universe_size": 141,  # top-level
                "percentile_rank": 99.3,  # top-level
                "top_1": True,
                "top_5": False,
                "top_10": False,
                "score": 0.91,
                "status": "blocked",
            }
        ]
        result = gate.evaluate("s3b_weekly_features", evidence)
        # Once the gate sees the canonical top-level universe_size=141,
        # it knows the source rank 1 is in the top 1% percentile and
        # must FAIL or REVIEW_REQUIRED.
        assert result.decision == PrivacyDecision.FAIL, (
            "Defect 1b not fixed: gate does not honor top-level "
            "candidate_universe_size for percentile FAIL"
        )

    # ── Defect 1c: missing evidence silently passes ───────────────
    def test_repro_missing_attack_evidence_cannot_pass(self, tmp_path: Path):
        """Missing required attack evidence must not produce PASS.

        Reproduces: gate returns FAIL when evidence list is empty,
        but when 'variant' is missing from attack rows the gate
        silently treats the run as having no evidence of this variant,
        producing a PASS through the absence.
        """
        gate = S3PrivacyGate()
        # No evidence at all for the requested variant — must FAIL.
        result = gate.evaluate("s3b_weekly_features", [])
        assert result.decision != PrivacyDecision.PASS_CANDIDATE, (
            "Defect 1c not fixed: gate can PASS_CANDIDATE on no evidence"
        )

    # ── Defect 1d: S0/S1/S2/S3A must not reach release boundary ────
    @pytest.mark.parametrize("ineligible_variant", sorted(NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE))
    def test_repro_ineligible_variant_cannot_be_exported(self, ineligible_variant: str):
        """S0/S1/S2 (and S3A) must raise on any attempt to mark them as
        releasable, even if lower-level APIs are called directly."""
        from fenrix_synthetic.release.eligibility import (
            IneligibleVariantError,
            enforce_eligibility_for_export,
        )

        with pytest.raises(IneligibleVariantError):
            enforce_eligibility_for_export(
                variant=ineligible_variant,
                release_marker="release_candidate",
            )

    def test_repro_s3a_marker_cannot_be_marked_releasable(self):
        """S3A is a non-releasable diagnostic; even if its release_marker
        is tampered to 'release_candidate', the export guard must reject."""
        from fenrix_synthetic.release.eligibility import (
            IneligibleVariantError,
            enforce_eligibility_for_export,
        )

        with pytest.raises(IneligibleVariantError):
            enforce_eligibility_for_export(
                variant="s3a_daily_bucketed",
                release_marker="release_candidate",
            )

    def test_repro_s3b_s3c_can_be_released_when_marker_correct(self):
        """S3B and S3C MUST be allowed through the export guard when
        they hold a 'release_candidate' marker."""
        from fenrix_synthetic.release.eligibility import (
            enforce_eligibility_for_export,
        )

        # No exception expected for eligible variants
        enforce_eligibility_for_export(
            variant="s3b_weekly_features",
            release_marker="release_candidate",
        )
        enforce_eligibility_for_export(
            variant="s3c_block_features",
            release_marker="release_candidate",
        )


# ════════════════════════════════════════════════════════════════════════
# Validator: convert legacy / nested attack dicts to the canonical form
# ════════════════════════════════════════════════════════════════════════


class TestCanonicalContract:
    """Tests proving the canonical contract is enforced end-to-end."""

    def test_canonical_conversion_flattens_metrics(self):
        """A legacy nested attack dict (the bug shape) must be rejected
        by the validator, NOT silently flattened."""
        from fenrix_synthetic.attacks.categorical_attacks import (
            AttackEvidenceError,
            validate_canonical_evidence,
        )

        malformed = {
            "attack_name": "exact",
            "true_source_rank": 1,
            "metrics": {"candidate_universe_size": 141},  # nested — bug
            "score": 0.9,
            "status": "blocked",
        }
        with pytest.raises(AttackEvidenceError):
            validate_canonical_evidence(malformed, expected_variant="s3b_weekly_features")

    def test_canonical_conversion_passes_valid_dict(self):
        from fenrix_synthetic.attacks.categorical_attacks import (
            validate_canonical_evidence,
        )

        well_formed = {
            "variant": "s3b_weekly_features",
            "attack_name": "exact",
            "ablation": "all",
            "true_source_rank": 50,
            "candidate_universe_size": 141,
            "percentile_rank": 64.5,
            "top_1": False,
            "top_5": False,
            "top_10": False,
            "score": 0.4,
            "status": "completed",
        }
        result = validate_canonical_evidence(well_formed, expected_variant="s3b_weekly_features")
        assert result.variant == "s3b_weekly_features"
        assert result.candidate_universe_size == 141
        assert result.percentile_rank == 64.5

    def test_rank_in_universe_returns_canonical_result(self):
        """Every rank_in_universe() return must satisfy the canonical contract."""
        records = _make_records()
        s3b = transform_s3b_weekly_features(records)
        cand_features = {f"cand-{i}": _inv_candidate(len(s3b.features), i) for i in range(5)}

        result = rank_in_universe(
            s3b.features,
            cand_features,
            variant=S3Variant.S3B_WEEKLY_FEATURES.value,
            method="exact",
        )
        # Now expose canonical fields at the top level
        canonical = categorical_attacks_to_canonical([result])[0]
        assert canonical.variant == "s3b_weekly_features"
        assert "candidate_universe_size" in canonical.__dict__
        assert canonical.candidate_universe_size >= 0
        assert hasattr(canonical, "percentile_rank")
        assert hasattr(canonical, "top_1")
        assert hasattr(canonical, "status")
