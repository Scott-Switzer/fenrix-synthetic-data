"""Release gate (Phase 4R2).

Implements PASS / FAIL / REVIEW_REQUIRED decision logic.
The gate consumes an EvidenceManifest as its authoritative input.
It verifies completeness, consistency, and run integrity before
evaluating individual privacy conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fenrix_synthetic.release.evidence import EvidenceManifest


class ReleaseDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass
class GateCondition:
    """A single evaluated condition in the release gate."""

    condition_id: str
    description: str
    passed: bool
    is_blocking: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass
class ReleaseGateResult:
    """Result of running the release gate assessment."""

    decision: ReleaseDecision
    conditions: list[GateCondition] = field(default_factory=list)
    blocking_failures: int = 0
    warnings: int = 0
    gate_hash: str = ""

    @property
    def all_passed(self) -> bool:
        return self.decision == ReleaseDecision.PASS


def evaluate_release_gate(
    *,
    text_attacks_blocked: bool = False,
    structured_rank: int = -1,
    structured_top_k: int = 10,
    llm_blocked: bool = False,
    exact_identity_hits: int = 0,
    unique_phrase_hits: int = 0,
    digital_hits: int = 0,
    filename_hits: int = 0,
    deterministic_reproduced: bool = True,
    all_attacks_ran: bool = True,
    provenance_complete: bool = True,
    private_paths_found: list[str] | None = None,
    unhandled_errors: list[str] | None = None,
    policy: dict[str, Any] | None = None,
    evidence_manifest: EvidenceManifest | None = None,
) -> ReleaseGateResult:
    """Evaluate all gate conditions and produce a release decision.

    When an EvidenceManifest is provided, it is validated first —
    completeness, consistency (run_id/source_id/release_id match),
    artifact hashes, and stale artifact detection — before individual
    conditions are evaluated. Additional gate conditions are added
    from the manifest.

    Args:
        text_attacks_blocked: Whether any text attack found a blocking hit
        structured_rank: Source rank in candidate universe (-1 if not found)
        structured_top_k: Threshold for structured ranking
        llm_blocked: Whether LLM attack confidence exceeds threshold
        exact_identity_hits: Number of exact identity hits
        unique_phrase_hits: Number of unique phrase / semantic fingerprint hits
        digital_hits: Number of digital identifier hits
        filename_hits: Number of filename/metadata hits
        deterministic_reproduced: Whether artifact hashes match
        all_attacks_ran: Whether all required attacks executed
        provenance_complete: Whether provenance is complete
        private_paths_found: Private paths found in release artifacts
        unhandled_errors: Unhandled error messages
        policy: Optional policy dict with custom thresholds
        evidence_manifest: Canonical EvidenceManifest (primary input when available)

    Returns:
        ReleaseGateResult with decision and all evaluated conditions
    """
    conditions: list[GateCondition] = []
    policy = policy or {}
    thresholds = policy.get("attack_thresholds", {})

    # ── Evidence manifest validation ────────────────────────────────
    if evidence_manifest is not None:
        # Condition M1: Manifest completeness
        manifest_valid, manifest_issues = evidence_manifest.validate_completeness()
        conditions.append(
            GateCondition(
                condition_id="evidence_manifest_complete",
                description="All required evidence types are present in manifest",
                passed=manifest_valid,
                is_blocking=True,
                evidence={"issues": manifest_issues[:10]},
            )
        )
        if not manifest_valid:
            # Cannot trust manifest — evaluate no further
            return ReleaseGateResult(
                decision=ReleaseDecision.FAIL,
                conditions=conditions,
                blocking_failures=1,
                warnings=0,
                gate_hash=_compute_gate_hash(ReleaseDecision.FAIL, 1, 0, conditions),
            )

        # Condition M2: No placeholder entries
        placeholders = [
            r.evidence_type
            for r in evidence_manifest.references
            if r.artifact_hash == "placeholder" or r.artifact_hash == ""
        ]
        conditions.append(
            GateCondition(
                condition_id="manifest_no_placeholders",
                description="No evidence entries are placeholders",
                passed=len(placeholders) == 0,
                is_blocking=True,
                evidence={"placeholders": placeholders},
            )
        )

        # Condition M3: All evidence from same run
        mismatched_run = [
            r.evidence_type
            for r in evidence_manifest.references
            if r.run_id and r.run_id != evidence_manifest.run_id
        ]
        conditions.append(
            GateCondition(
                condition_id="manifest_same_run",
                description="All evidence belongs to the current run",
                passed=len(mismatched_run) == 0,
                is_blocking=True,
                evidence={"mismatched": mismatched_run},
            )
        )

        # Condition M4: Source/release IDs match
        id_mismatch = [
            r.evidence_type
            for r in evidence_manifest.references
            if (
                (r.source_id and r.source_id != evidence_manifest.source_id)
                or (r.release_id and r.release_id != evidence_manifest.release_id)
            )
        ]
        conditions.append(
            GateCondition(
                condition_id="manifest_id_consistency",
                description="All evidence has consistent source/release IDs",
                passed=len(id_mismatch) == 0,
                is_blocking=True,
                evidence={"mismatched": id_mismatch},
            )
        )

        # Condition M5: Required evidence types present
        required_types = evidence_manifest.get_required_types()
        present_types = {r.evidence_type for r in evidence_manifest.references}
        missing_types = required_types - present_types
        conditions.append(
            GateCondition(
                condition_id="manifest_required_types",
                description="All required evidence types are present",
                passed=len(missing_types) == 0,
                is_blocking=True,
                evidence={"missing": sorted(missing_types)},
            )
        )

    # ── Individual conditions ───────────────────────────────────────
    # Condition 1: Exact identity scan
    c1_passed = exact_identity_hits == 0
    conditions.append(
        GateCondition(
            condition_id="exact_identity",
            description="No exact identity matches found",
            passed=c1_passed,
            is_blocking=True,
            evidence={"hits": exact_identity_hits},
        )
    )

    # Condition 2: Unique phrase / semantic fingerprint scan
    fp_threshold = int(thresholds.get("unique_phrase_hits", 0))
    c2_passed = unique_phrase_hits <= fp_threshold
    conditions.append(
        GateCondition(
            condition_id="unique_phrase",
            description=f"Unique phrase hits ({unique_phrase_hits}) <= threshold ({fp_threshold})",
            passed=c2_passed,
            is_blocking=True,
            evidence={"hits": unique_phrase_hits, "threshold": fp_threshold},
        )
    )

    # Condition 3: Digital identifier scan
    c3_passed = digital_hits == 0
    conditions.append(
        GateCondition(
            condition_id="digital_identifiers",
            description="No digital identifiers found",
            passed=c3_passed,
            is_blocking=True,
            evidence={"hits": digital_hits},
        )
    )

    # Condition 4: Filename and metadata scan
    c4_passed = filename_hits == 0
    conditions.append(
        GateCondition(
            condition_id="filename_metadata",
            description="No source identifiers in filenames or metadata",
            passed=c4_passed,
            is_blocking=True,
            evidence={"hits": filename_hits},
        )
    )

    # Condition 5: Structured attack ranking
    k = int(thresholds.get("structured_ranking_k", 10))
    c5_passed = structured_rank < 0 or structured_rank > k
    conditions.append(
        GateCondition(
            condition_id="structured_ranking",
            description=f"Source rank ({structured_rank}) outside top {k}",
            passed=c5_passed,
            is_blocking=True,
            evidence={"rank": structured_rank, "top_k": k},
        )
    )

    # Condition 6: LLM attack (non-blocking if LLM is optional)
    llm_threshold = thresholds.get("llm_confidence_threshold", 0.7)
    c6_passed = not llm_blocked
    llm_required = thresholds.get("llm_attack_required", False)
    conditions.append(
        GateCondition(
            condition_id="llm_attack",
            description=f"LLM confidence below threshold ({llm_threshold})",
            passed=c6_passed,
            is_blocking=llm_required,
            evidence={"threshold": llm_threshold},
        )
    )

    # Condition 7: Deterministic reproduction
    c7_passed = deterministic_reproduced
    conditions.append(
        GateCondition(
            condition_id="deterministic_reproduction",
            description="Artifact hashes match expected values",
            passed=c7_passed,
            is_blocking=True,
        )
    )

    # Condition 8: All attacks ran
    c8_passed = all_attacks_ran
    conditions.append(
        GateCondition(
            condition_id="all_attacks_ran",
            description="All required attacks executed",
            passed=c8_passed,
            is_blocking=True,
        )
    )

    # Condition 9: Provenance complete
    c9_passed = provenance_complete
    conditions.append(
        GateCondition(
            condition_id="provenance_complete",
            description="Provenance records are complete",
            passed=c9_passed,
            is_blocking=True,
        )
    )

    # Condition 10: No private paths in release
    pf = private_paths_found or []
    c10_passed = len(pf) == 0
    conditions.append(
        GateCondition(
            condition_id="no_private_paths",
            description="No private paths found in release artifacts",
            passed=c10_passed,
            is_blocking=True,
            evidence={"private_paths": pf},
        )
    )

    # Condition 11: No unhandled errors
    ue = unhandled_errors or []
    c11_passed = len(ue) == 0
    conditions.append(
        GateCondition(
            condition_id="no_unhandled_errors",
            description="No unhandled validator errors",
            passed=c11_passed,
            is_blocking=True,
            evidence={"errors": ue},
        )
    )

    # ── Decision ────────────────────────────────────────────────────
    blocking_failures = sum(1 for c in conditions if not c.passed and c.is_blocking)
    warnings = sum(1 for c in conditions if not c.passed and not c.is_blocking)

    if blocking_failures > 0:
        decision = ReleaseDecision.FAIL
    elif warnings > 0:
        decision = ReleaseDecision.REVIEW_REQUIRED
    else:
        decision = ReleaseDecision.PASS

    return ReleaseGateResult(
        decision=decision,
        conditions=conditions,
        blocking_failures=blocking_failures,
        warnings=warnings,
        gate_hash=_compute_gate_hash(decision, blocking_failures, warnings, conditions),
    )


def _compute_gate_hash(
    decision: ReleaseDecision,
    blocking_failures: int,
    warnings: int,
    conditions: list[GateCondition],
) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            {
                "decision": decision.value,
                "blocking_failures": blocking_failures,
                "warnings": warnings,
                "conditions": [
                    {"id": c.condition_id, "passed": c.passed, "blocking": c.is_blocking}
                    for c in conditions
                ],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
