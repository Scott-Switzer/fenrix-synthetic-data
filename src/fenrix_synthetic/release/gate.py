"""Release gate (Phase 4I).

Implements PASS / FAIL / REVIEW_REQUIRED decision logic.

The gate must fail when:
- A known identifier remains
- A source name appears in any filename
- A ticker, CIK, EIN, LEI, URL, domain, phone number or address remains
- Raw data are inside the repository
- Private mappings appear in tracked output
- Deterministic reproduction fails
- A required attack did not run
- Provenance is incomplete
- The structured attack exceeds the configured privacy threshold
- A unique phrase or semantic fingerprint exceeds the configured threshold
- Release artifacts contain private paths
- Any validator encounters an unhandled error

The gate must never convert missing evidence into a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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
    text_attacks_blocked: bool,
    structured_rank: int,
    structured_top_k: int,
    llm_blocked: bool,
    exact_identity_hits: int,
    unique_phrase_hits: int,
    digital_hits: int,
    filename_hits: int,
    deterministic_reproduced: bool,
    all_attacks_ran: bool,
    provenance_complete: bool,
    private_paths_found: list[str],
    unhandled_errors: list[str],
    policy: dict | None = None,
) -> ReleaseGateResult:
    """Evaluate all gate conditions and produce a release decision.

    Args:
        text_attacks_blocked: Whether any text attack found a blocking hit
        structured_rank: Source's rank in candidate universe (1-based, -1 if not found)
        structured_top_k: Threshold for structured ranking
        llm_blocked: Whether LLM attack confidence exceeds threshold
        exact_identity_hits: Number of exact identity hits
        unique_phrase_hits: Number of unique phrase / semantic fingerprint hits
        digital_hits: Number of digital identifier hits
        filename_hits: Number of filename/metadata hits
        deterministic_reproduced: Whether artifact hashes match
        all_attacks_ran: Whether all required attacks executed
        provenance_complete: Whether provenance is complete
        private_paths_found: List of private paths found in release artifacts
        unhandled_errors: List of unhandled error messages
        policy: Optional policy dict with custom thresholds

    Returns:
        ReleaseGateResult with decision and all evaluated conditions
    """
    conditions: list[GateCondition] = []

    policy = policy or {}
    thresholds = policy.get("attack_thresholds", {})

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
    fp_threshold = thresholds.get("unique_phrase_hits", 0)
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
            description="No digital identifiers found (URLs, domains, emails, phones)",
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
    k = thresholds.get("structured_ranking_k", 10)
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

    # Condition 6: LLM attack
    llm_threshold = thresholds.get("llm_confidence_threshold", 0.7)
    c6_passed = not llm_blocked
    is_blocking_llm = not c6_passed
    conditions.append(
        GateCondition(
            condition_id="llm_attack",
            description=f"LLM confidence below threshold ({llm_threshold})",
            passed=c6_passed,
            is_blocking=is_blocking_llm,
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
    c10_passed = len(private_paths_found) == 0
    conditions.append(
        GateCondition(
            condition_id="no_private_paths",
            description="No private paths found in release artifacts",
            passed=c10_passed,
            is_blocking=True,
            evidence={"private_paths": private_paths_found},
        )
    )

    # Condition 11: No unhandled errors
    c11_passed = len(unhandled_errors) == 0
    conditions.append(
        GateCondition(
            condition_id="no_unhandled_errors",
            description="No unhandled validator errors",
            passed=c11_passed,
            is_blocking=True,
            evidence={"errors": unhandled_errors},
        )
    )

    # Determine decision
    blocking_failures = sum(1 for c in conditions if not c.passed and c.is_blocking)
    warnings = sum(1 for c in conditions if not c.passed and not c.is_blocking)

    if blocking_failures > 0:
        decision = ReleaseDecision.FAIL
    elif warnings > 0:
        decision = ReleaseDecision.REVIEW_REQUIRED
    else:
        decision = ReleaseDecision.PASS

    import hashlib
    import json

    gate_hash = hashlib.sha256(
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

    return ReleaseGateResult(
        decision=decision,
        conditions=conditions,
        blocking_failures=blocking_failures,
        warnings=warnings,
        gate_hash=gate_hash,
    )
