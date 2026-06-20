"""S3 feature-only privacy gate policy (Phase 5A, Part 8).

Configurable empirical thresholds for categorical sequence attacks.

Default conservative policy:
- FAIL when source ranks in top 10 under any credible attack
- FAIL when continuous values can be reconstructed
- REVIEW_REQUIRED when source ranks in top 1%
- PASS_CANDIDATE only when source outside top 1% for all attacks

Does NOT claim mathematical anonymity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fenrix_synthetic.attacks.canonical_evidence import (
    validate_evidence_batch,
)
from fenrix_synthetic.release.eligibility import (
    IneligibleVariantError,
    assert_releasable_variant,
)


class PrivacyDecision(Enum):
    PASS_CANDIDATE = "pass_candidate"
    REVIEW_REQUIRED = "review_required"
    FAIL = "fail"


# Required attack and ablation keys for evidence-completeness checks
# (Phase 5A close-out). An attack record missing any of these is
# invalid evidence — it can never produce PASS or REVIEW_REQUIRED; the
# gate returns decision=FAIL with `is_complete=False` so the CLI can
# route to exit 2 (input invalid) rather than exit 3 (privacy fail).
REQUIRED_ATTACK_NAMES: frozenset[str] = frozenset(
    {
        "exact",
        "weighted_hamming",
        "dtw",
        "transition",
        "ngram",
        "combined",
    }
)

REQUIRED_ABLATION_NAMES: frozenset[str] = frozenset(
    {
        "all",
        "direction",
        "momentum",
        "volatility",
        "drawdown",
        "market_relative",
        "sector_relative",
        "technical_state",
    }
)
SUPPORTED_ATTACK_NAMES: frozenset[str] = frozenset(
    REQUIRED_ATTACK_NAMES | {"lagged_1", "lagged_5", "lagged_21"}
)
# Ablations include "all" as the default non-ablation group plus the
# feature-group ablations.
SUPPORTED_ABLATIONS: frozenset[str] = REQUIRED_ABLATION_NAMES

# ── MVP policy v1 shared attack-key contract ─────────────────────────
# Both generator and gate MUST read the same literal keys.
_MVP_REQUIRED_KEYS: frozenset[str] = frozenset()  # "attack_name/ablation" keys


def load_mvp_policy(policy_path: str) -> frozenset[str]:
    """Load the shared s3b-mvp-v1 policy and return required attack keys.

    Returns a frozenset of "attack_name/ablation" strings.
    Both generator and gate must call this to stay in sync.
    """
    from pathlib import Path as _Path

    global _MVP_REQUIRED_KEYS
    raw = json.loads(_Path(policy_path).read_text())
    if not isinstance(raw, dict):
        raise ValueError("MVP policy must be a JSON object")
    keys = raw.get("required_attack_keys", [])
    if not isinstance(keys, list) or not keys:
        raise ValueError("MVP policy requires non-empty 'required_attack_keys' list")
    _MVP_REQUIRED_KEYS = frozenset(str(k) for k in keys)
    return _MVP_REQUIRED_KEYS


def clear_mvp_policy() -> None:
    """Reset MVP policy global state (for test isolation).

    After calling this, the gate falls back to legacy attack-name + ablation-name
    completeness checks.
    """
    global _MVP_REQUIRED_KEYS
    _MVP_REQUIRED_KEYS = frozenset()


RECOGNIZED_RELEASABLE_VARIANTS: frozenset[str] = frozenset(
    {
        "s3b_weekly_features",
        "s3c_block_features",
    }
)


class EvidenceCompletenessError(Exception):
    """Raised when the evidence batch cannot satisfy the required schema."""


@dataclass
class S3AttackEvidence:
    """Evidence from a single categorical attack."""

    attack_type: str
    variant: str
    ablation_group: str
    true_source_rank: int
    universe_size: int
    percentile_rank: float
    in_top_10: bool
    in_top_1_pct: bool
    source_score: float = 0.0
    score_margin: float = 0.0


@dataclass
class S3PrivacyGateResult:
    """Result of S3 privacy gate evaluation."""

    variant: str
    decision: PrivacyDecision = PrivacyDecision.FAIL
    blocking_reasons: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    evidence: list[S3AttackEvidence] = field(default_factory=list)
    gate_hash: str = ""
    warnings: list[str] = field(default_factory=list)
    is_final: bool = False  # True only for PASS_CANDIDATE or FAIL
    is_complete: bool = True  # False when evidence is structurally incomplete
    is_ineligible: bool = False  # True when the variant is structurally ineligible


class S3PrivacyGate:
    """Configurable S3 privacy gate with empirical thresholds.

    Default conservative policy:
    - FAIL: source in top 10 under any credible attack, or reconstruction succeeds
    - REVIEW_REQUIRED: source in top 1%, unstable results, missing attacks
    - PASS_CANDIDATE: source outside top 1% for all attacks, all evidence complete
    """

    def __init__(
        self,
        top_k_fail: int = 10,
        top_pct_fail: float = 5.0,
        top_pct_review: float = 1.0,
        min_universe_size: int = 50,
    ):
        self._top_k_fail = top_k_fail
        self._top_pct_fail = top_pct_fail
        self._top_pct_review = top_pct_review
        self._min_universe_size = min_universe_size

        params = {
            "top_k_fail": top_k_fail,
            "top_pct_fail": top_pct_fail,
            "top_pct_review": top_pct_review,
            "min_universe_size": min_universe_size,
        }
        raw = json.dumps(params, sort_keys=True)
        self._policy_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]

    @property
    def policy_hash(self) -> str:
        return self._policy_hash

    def evaluate(
        self,
        variant: str,
        attack_results: list[dict[str, Any]],
        *,
        reconstruction_succeeded: bool = False,
        forbidden_fields_found: list[str] | None = None,
    ) -> S3PrivacyGateResult:
        """Evaluate S3 privacy for a variant.

        Args:
            variant: The S3 variant (s3a, s3b, s3c).
            attack_results: List of attack result dictionaries with keys:
                attack_type, true_source_rank, universe_size, percentile_rank,
                ablation_group, source_score, score_margin.
            reconstruction_succeeded: Whether a reconstruction attack succeeded.
            forbidden_fields_found: Any forbidden fields detected in output.

        Returns:
            S3PrivacyGateResult with decision and reasons.
        """
        result = S3PrivacyGateResult(variant=variant)
        blocking_reasons: list[str] = []
        review_reasons: list[str] = []
        evidence_list: list[S3AttackEvidence] = []

        # Defense-in-depth: refuse to evaluate ineligible variants even
        # if a caller invokes this API directly. Ineligibility is a
        # STRUCTURAL property of the variant and yields FAIL is_final
        # with exit code 5 at the CLI boundary. The structured marker
        # `result.is_ineligible` lets the CLI route on the flag rather
        # than coupling to the rejection text.
        try:
            assert_releasable_variant(variant)
        except IneligibleVariantError as exc:
            blocking_reasons.append(f"[INELIGIBLE_VARIANT] {exc}")
            result.blocking_reasons = blocking_reasons
            result.decision = PrivacyDecision.FAIL
            result.is_final = True
            result.is_ineligible = True
            return self._finalize(result)

        # Reconstruction is an INDEPENDENT short-circuit: regardless of
        # evidence content, if reconstruction succeeded the gate refuses
        # the candidate. Check this BEFORE completeness so the
        # "reconstruction succeeded" blocking reason is always surfaced.
        if reconstruction_succeeded:
            result.blocking_reasons.append(
                "[RECONSTRUCTION_SUCCEEDED] Reconstruction attack succeeded: continuous values can be inferred"
            )
            result.decision = PrivacyDecision.FAIL
            result.is_final = True
            return self._finalize(result)

        # Check forbidden fields BEFORE completeness for the same reason:
        # forbidden-field detection is independent of evidence shape.
        forbidden: list[str] = forbidden_fields_found or []
        if forbidden:
            result.blocking_reasons.append(
                f"[FORBIDDEN_FIELDS] Forbidden fields detected in output: {forbidden[:5]}"
            )
            result.decision = PrivacyDecision.FAIL
            result.is_final = True
            return self._finalize(result)

        # ── Evidence-completeness precheck (Phase 5A close-out) ─────
        # An empty / incomplete / duplicate / wrong-variant evidence
        # set can never pass. The CLI uses `is_complete` to route to
        # exit 2 (input invalid) rather than exit 3 (privacy fail).
        completeness_issues = self._check_evidence_completeness(variant, attack_results)
        if completeness_issues:
            # Prefix every completeness issue with the stable token so
            # tests can assert on [INCOMPLETE_EVIDENCE] without coupling
            # to the specific reason text.
            blocking_reasons.extend(f"[INCOMPLETE_EVIDENCE] {r}" for r in completeness_issues)
            result.blocking_reasons = blocking_reasons
            result.decision = PrivacyDecision.FAIL
            result.is_final = True
            result.is_complete = False
            return self._finalize(result)

        # Evaluate each attack (only those that passed canonical validation).
        # First, run the canonical validator on the entire batch.
        valid_evidence, evidence_issues = validate_evidence_batch(
            attack_results, expected_variant=variant
        )
        if evidence_issues:
            blocking_reasons.append(
                "[MALFORMED_EVIDENCE] Malformed attack evidence rejected: "
                + "; ".join(evidence_issues[:3])
            )

        # id-based probe: O(1) membership per row instead of O(n)
        # string-equality probing. Keys are stable per evaluate() call
        # because valid_evidence is built from canonicalized dicts.
        _valid_id_keys = frozenset(id(e.to_dict()) for e in valid_evidence)

        for attack in attack_results:
            probe = dict(attack)
            probe.setdefault("variant", variant)
            # Maintain the legacy fallback for evaluators that pass rows
            # without canonicalization: if no evidence_issues were
            # reported, accept every row.
            if id(probe) in _valid_id_keys or not evidence_issues:
                rank = int(attack.get("true_source_rank", -1))
                universe = int(attack.get("candidate_universe_size", 0))
                percentile = float(attack.get("percentile_rank", 0.0))
                score = float(attack.get("score", 0.0))
                margin = float(attack.get("score_margin", 0.0))
            else:
                continue

            attack_name = attack.get("attack_name", attack.get("attack_type", "unknown"))
            group = attack.get("ablation", attack.get("ablation_group", "all"))

            evidence_list.append(
                S3AttackEvidence(
                    attack_type=attack_name,
                    variant=attack.get("variant", variant),
                    ablation_group=group,
                    true_source_rank=rank,
                    universe_size=universe,
                    percentile_rank=percentile,
                    in_top_10=rank > 0 and rank <= 10,
                    in_top_1_pct=percentile >= 99.0,
                    source_score=score,
                    score_margin=margin,
                )
            )

            # FAIL conditions
            if rank > 0 and rank <= self._top_k_fail:
                blocking_reasons.append(
                    f"Attack '{attack_name}' ({group}): source rank "
                    f"{rank}/{universe} (in top {self._top_k_fail})"
                )

            if universe > 0 and rank > 0:
                pct_rank = (1.0 - rank / universe) * 100.0
                if pct_rank >= 100.0 - self._top_pct_fail:
                    blocking_reasons.append(
                        f"Attack '{attack_name}' ({group}): source in top "
                        f"{self._top_pct_fail}% (rank {rank}/{universe})"
                    )

            # REVIEW_REQUIRED conditions
            if rank > 0 and percentile >= 99.0:
                review_reasons.append(
                    f"Attack '{attack_name}' ({group}): source in top 1% (rank {rank}/{universe})"
                )

        # Forbidden fields also block
        if forbidden:
            blocking_reasons.append(f"Forbidden fields detected in output: {forbidden[:5]}")

        # Check universe size
        max_universe = max((e.universe_size for e in evidence_list), default=0)
        if max_universe < self._min_universe_size:
            review_reasons.append(
                f"Candidate universe ({max_universe}) below minimum ({self._min_universe_size})"
            )

        # Final blocking on empty evidence list (belt-and-braces)
        if not evidence_list:
            blocking_reasons.append(
                "[INCOMPLETE_EVIDENCE] No valid attack evidence provided (empty or all malformed)"
            )

        # Determine decision
        result.evidence = evidence_list
        result.blocking_reasons = blocking_reasons
        result.review_reasons = review_reasons

        if blocking_reasons:
            result.decision = PrivacyDecision.FAIL
            result.is_final = True
        elif review_reasons:
            result.decision = PrivacyDecision.REVIEW_REQUIRED
            if not result.warnings:
                result.warnings.append(
                    "REVIEW_REQUIRED does not expire. Re-review is needed "
                    "after any policy or universe change."
                )
        else:
            result.decision = PrivacyDecision.PASS_CANDIDATE
            result.warnings.append(
                "PASS_CANDIDATE describes empirical attack resistance, "
                "not proof against every possible adversary."
            )

        return self._finalize(result)

    def _finalize(self, result: S3PrivacyGateResult) -> S3PrivacyGateResult:
        """Compute gate hash and return."""
        components = {
            "variant": result.variant,
            "decision": result.decision.value,
            "blocking_reasons": result.blocking_reasons,
            "review_reasons": result.review_reasons,
            "policy_hash": self._policy_hash,
        }
        raw = json.dumps(components, sort_keys=True)
        result.gate_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return result

    def _check_evidence_completeness(
        self,
        variant: str,
        attack_results: list[dict[str, Any]],
    ) -> list[str]:
        """Verify evidence completeness BEFORE threshold evaluation.

        Returns a list of issues. Empty list = complete.

        Rules enforced (Phase 5A close-out):

        * Zero attacks can never pass.
        * Zero eligible variants can never pass.
        * Missing ablations can never pass.
        * Duplicate attack/ablation combinations are rejected.
        * An attack for the wrong variant is rejected.
        * Unsupported attack names are rejected.
        """
        issues: list[str] = []
        if not attack_results:
            issues.append(f"empty attack list for variant={variant!r}")
            # Legacy alias used by older assertions + downstream logs.
            issues.append("No valid attack evidence provided (empty or all malformed)")
            return issues

        # Variant coverage — the input must target the gate's variant.
        invalid_variant_records = [
            a
            for a in attack_results
            if isinstance(a, dict) and a.get("variant") and a.get("variant") != variant
        ]
        if invalid_variant_records:
            issues.append(
                f"wrong-variant attack evidence: expected variant={variant!r}; "
                f"received {len(invalid_variant_records)} row(s) with mismatched variant"
            )

        # Empty eligible-variant set: at least one record must carry the
        # matching variant for this gate.
        eligible = [
            a
            for a in attack_results
            if isinstance(a, dict) and (not a.get("variant") or a.get("variant") == variant)
        ]
        if not eligible:
            issues.append(f"empty eligible-variant set for variant={variant!r}")

        # Unsupported attack names / ablations.
        bad_attacks: list[str] = [
            str(a.get("attack_name", a.get("attack_type", "?")))
            for a in eligible
            if isinstance(a, dict)
            and (a.get("attack_name") or a.get("attack_type")) not in SUPPORTED_ATTACK_NAMES
        ]
        if bad_attacks:
            sorted_attacks: list[str] = sorted(set(bad_attacks))
            issues.append(f"unsupported attack names: {sorted_attacks}")

        bad_ablations: list[str] = [
            str(a.get("ablation", a.get("ablation_group", "?")))
            for a in eligible
            if isinstance(a, dict)
            and (a.get("ablation") or a.get("ablation_group")) not in SUPPORTED_ABLATIONS
        ]
        if bad_ablations:
            sorted_ablations: list[str] = sorted(set(bad_ablations))
            issues.append(f"unsupported ablation groups: {sorted_ablations}")

        # Duplicate attack+ablation combinations.
        seen: set[tuple[str, str]] = set()
        duplicates: list[str] = []
        for a in eligible:
            if not isinstance(a, dict):
                continue
            name = a.get("attack_name", a.get("attack_type", "?"))
            abl = a.get("ablation", a.get("ablation_group", "all"))
            key = (str(name), str(abl))
            if key in seen:
                duplicates.append(f"{name}/{abl}")
            else:
                seen.add(key)
        if duplicates:
            issues.append(f"duplicate attack+ablation combinations: {sorted(set(duplicates))}")

        # Required coverage — if MVP policy is loaded, validate against
        # the exact 16 attack-name/ablation keys (no more, no less).
        # Otherwise fall back to the legacy attack-name + ablation-name sets.
        if _MVP_REQUIRED_KEYS:
            observed_keys: set[str] = {
                f"{str(a.get('attack_name', a.get('attack_type', '')))}/"
                f"{str(a.get('ablation', a.get('ablation_group', 'all')))}"
                for a in eligible
                if isinstance(a, dict)
            }
            missing_keys: list[str] = sorted(_MVP_REQUIRED_KEYS - observed_keys)
            if missing_keys:
                issues.append(f"missing required attack keys (MVP policy): {missing_keys}")
            additional_keys: list[str] = sorted(observed_keys - _MVP_REQUIRED_KEYS)
            if additional_keys:
                issues.append(
                    f"additional unexpected attack keys not in MVP policy: {additional_keys}"
                )
            # Also verify no duplicate keys (using fresh variable names to
            # avoid shadowing the earlier duplicate check variables).
            policy_dupes: list[str] = []
            policy_seen: set[str] = set()
            for a in eligible:
                if not isinstance(a, dict):
                    continue
                policy_key = (
                    f"{str(a.get('attack_name', a.get('attack_type', '')))}/"
                    f"{str(a.get('ablation', a.get('ablation_group', 'all')))}"
                )
                if policy_key in policy_seen:
                    policy_dupes.append(policy_key)
                else:
                    policy_seen.add(policy_key)
            if policy_dupes:
                issues.append(f"duplicate attack keys in evidence: {sorted(set(policy_dupes))}")
        else:
            observed_attacks = {
                str(a.get("attack_name", a.get("attack_type", "")))
                for a in eligible
                if isinstance(a, dict)
            }
            observed_ablations = {
                str(a.get("ablation", a.get("ablation_group", "")))
                for a in eligible
                if isinstance(a, dict)
            }
            missing_attacks: list[str] = sorted(REQUIRED_ATTACK_NAMES - observed_attacks)
            if missing_attacks:
                issues.append(f"missing required attacks: {missing_attacks}")
            missing_ablations: list[str] = sorted(REQUIRED_ABLATION_NAMES - observed_ablations)
            if missing_ablations:
                issues.append(f"missing required ablations: {missing_ablations}")

        return issues
