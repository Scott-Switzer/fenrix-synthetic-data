"""Canonical CategoricalAttackEvidence contract (Phase 5A defect repair).

Every categorical attack result MUST conform to this single, validated
contract before it can reach the S3 privacy gate, the evidence manifest,
or the release dossier. The contract defines:

* `variant`: which S3 variant this attack was run against (must match
  the variant being assessed).
* `attack_name`: a stable identifier for the similarity method.
* `ablation`: which feature subset was used ("all", "direction", ...).
* `true_source_rank`: integer rank of the known source under this attack
  (-1 if not found in the universe).
* `candidate_universe_size`: number of candidates considered.
* `percentile_rank`: percentile of the source rank (0-100).
* `top_1`, `top_5`, `top_10`: flags for fast threshold checks.
* `score`: the similarity score assigned to the source.
* `status`: AttackStatus enum — `completed`, `blocked`,
  `missing_evidence`, or `malformed`.

Note: Non-Pydantic dataclass is used because the rest of the project
relies on stdlib dataclasses. Validation logic is explicit and deterministic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class AttackStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    MISSING_EVIDENCE = "missing_evidence"
    MALFORMED = "malformed"


class AttackEvidenceError(Exception):
    """Raised when an attack evidence dict does not conform to the canonical contract."""


# Fields that must be present at the TOP LEVEL of the dict, not nested
_REQUIRED_TOP_LEVEL_FIELDS = frozenset(
    {
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
)

# Fields that are exempted from the "no nested metrics" rule
_ALLOWED_FLAT_FIELDS = frozenset({"true_source_rank", "candidate_universe_size", "percentile_rank"})


@dataclass
class CategoricalAttackEvidence:
    """Canonical, validated categorical attack evidence.

    Producers (in attacks/, orchestrator/) must call
    `validate_canonical_evidence()` before marshalling to JSON, and
    consumers (s3_gate, evidence manifest) must read the same fields
    by name.
    """

    variant: str
    attack_name: str
    ablation: str
    true_source_rank: int
    candidate_universe_size: int
    percentile_rank: float
    top_1: bool
    top_5: bool
    top_10: bool
    score: float | None = None
    status: AttackStatus = AttackStatus.COMPLETED
    attack_hash: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def is_correct_variant(self, expected_variant: str) -> bool:
        return self.variant == expected_variant


@dataclass
class EvidenceValidationResult:
    is_valid: bool
    issues: list[str] = field(default_factory=list)


def validate_canonical_evidence(
    evidence: dict[str, Any],
    expected_variant: str | None = None,
) -> CategoricalAttackEvidence:
    """Validate a dict against the canonical contract.

    Raises AttackEvidenceError on:
    - Missing required fields
    - Nested `metrics` dicts where flat fields are expected
    - Variant mismatch with expected_variant
    - Type errors on required types

    Returns a CategoricalAttackEvidence on success.
    """
    issues: list[str] = []

    if not isinstance(evidence, dict):
        raise AttackEvidenceError(f"Evidence must be a dict, got {type(evidence).__name__}")

    # Reject nested metrics with canonical names — this was bug 1b.
    if isinstance(evidence.get("metrics"), dict):
        nested = evidence["metrics"]
        for canonical_field in _ALLOWED_FLAT_FIELDS:
            if canonical_field in nested:
                issues.append(f"Field '{canonical_field}' must not be nested under 'metrics'")

    # Required fields at top level
    missing = _REQUIRED_TOP_LEVEL_FIELDS - set(evidence.keys())
    if missing:
        issues.append(f"Missing required fields at top level: {sorted(missing)}")

    if issues:
        raise AttackEvidenceError("Malformed attack evidence: " + "; ".join(issues))

    # Type checks
    rank = evidence.get("true_source_rank")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < -1:
        raise AttackEvidenceError(f"true_source_rank must be int >= -1, got {rank!r}")

    uni = evidence.get("candidate_universe_size")
    if not isinstance(uni, int) or isinstance(uni, bool) or uni < 0:
        raise AttackEvidenceError(f"candidate_universe_size must be int >= 0, got {uni!r}")

    pct = evidence.get("percentile_rank")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool):
        raise AttackEvidenceError(f"percentile_rank must be numeric, got {pct!r}")
    if pct < 0.0 or pct > 100.0:
        raise AttackEvidenceError(f"percentile_rank must be in [0, 100], got {pct}")

    for bool_field in ("top_1", "top_5", "top_10"):
        if not isinstance(evidence[bool_field], bool):
            raise AttackEvidenceError(
                f"{bool_field} must be bool, got {type(evidence[bool_field]).__name__}"
            )

    score_val = evidence.get("score")
    if score_val is not None and not isinstance(score_val, (int, float)):
        raise AttackEvidenceError(f"score must be numeric or None, got {type(score_val).__name__}")

    variant_val = evidence.get("variant")
    if not isinstance(variant_val, str) or not variant_val:
        raise AttackEvidenceError(f"variant must be a non-empty string, got {variant_val!r}")

    if expected_variant is not None and variant_val != expected_variant:
        raise AttackEvidenceError(
            f"Variant mismatch: expected {expected_variant!r}, got {variant_val!r}"
        )

    status_raw = evidence.get("status", "completed")
    try:
        status = AttackStatus(status_raw)
    except ValueError as exc:
        raise AttackEvidenceError(
            f"Invalid status {status_raw!r}: must be one of {[s.value for s in AttackStatus]}"
        ) from exc

    return CategoricalAttackEvidence(
        variant=variant_val,
        attack_name=str(evidence["attack_name"]),
        ablation=str(evidence["ablation"]),
        true_source_rank=rank,
        candidate_universe_size=uni,
        percentile_rank=float(pct),
        top_1=bool(evidence["top_1"]),
        top_5=bool(evidence["top_5"]),
        top_10=bool(evidence["top_10"]),
        score=float(score_val) if score_val is not None else None,
        status=status,
        attack_hash=str(evidence.get("attack_hash", "")),
        notes=str(evidence.get("notes", "")),
    )


def validate_evidence_batch(
    raw_list: list[dict[str, Any]],
    expected_variant: str,
) -> tuple[list[CategoricalAttackEvidence], list[str]]:
    """Validate a batch of evidence dicts. Returns (valid, issues)."""
    valid: list[CategoricalAttackEvidence] = []
    issues: list[str] = []
    for i, raw in enumerate(raw_list):
        try:
            valid.append(validate_canonical_evidence(raw, expected_variant))
        except AttackEvidenceError as exc:
            issues.append(f"evidence[{i}]: {exc}")
    return valid, issues


def evidence_batch_hash(evidence_list: list[CategoricalAttackEvidence]) -> str:
    """Deterministic hash of a batch of canonical evidence."""
    payload = [e.to_dict() for e in sorted(evidence_list, key=lambda x: x.attack_name)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
