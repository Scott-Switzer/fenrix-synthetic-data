"""Evidence manifest system (Phase 4R).

Canonical evidence manifest that the release gate consumes.
All evidence artifacts are referenced by hash. The gate verifies
completeness and consistency before making a decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class EvidenceReference:
    """A reference to a single evidence artifact."""

    evidence_type: str
    artifact_path: str = ""
    artifact_hash: str = ""
    run_id: str = ""
    source_id: str = ""
    release_id: str = ""
    policy_version: str = ""
    verified: bool = False
    notes: str = ""


@dataclass
class EvidenceManifest:
    """Canonical evidence manifest for release gate assessment."""

    manifest_id: str
    run_id: str
    source_id: str
    release_id: str
    policy_version: str
    pipeline_version: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    references: list[EvidenceReference] = field(default_factory=list)
    manifest_hash: str = ""

    def add_reference(
        self,
        evidence_type: str,
        artifact_hash: str,
        verified: bool = False,
        **kwargs: Any,
    ) -> None:
        self.references.append(
            EvidenceReference(
                evidence_type=evidence_type,
                artifact_hash=artifact_hash,
                run_id=self.run_id,
                source_id=self.source_id,
                release_id=self.release_id,
                policy_version=self.policy_version,
                verified=verified,
                **kwargs,
            )
        )

    def get_required_types(self) -> set[str]:
        return {
            "source_manifest_validation",
            "atlas_compilation",
            "masking_results",
            "text_attacks",
            "structured_attacks",
            "utility_evaluation",
            "determinism_check",
            "provenance",
            "boundary_scan",
            "dossier_scan",
        }

    def validate_completeness(self) -> tuple[bool, list[str]]:
        """Check that all required evidence types are present."""
        present = {r.evidence_type for r in self.references}
        missing = self.get_required_types() - present
        issues = []
        if missing:
            issues.append(f"Missing evidence types: {sorted(missing)}")
        stale = [r for r in self.references if r.run_id != self.run_id]
        if stale:
            issues.append(f"Stale evidence from different run: {[r.evidence_type for r in stale]}")
        mismatched_source = [r for r in self.references if r.source_id != self.source_id]
        if mismatched_source:
            issues.append(
                f"Evidence from different source: {[r.evidence_type for r in mismatched_source]}"
            )
        return len(issues) == 0, issues

    def compute_hash(self) -> str:
        data = {
            "manifest_id": self.manifest_id,
            "run_id": self.run_id,
            "source_id": self.source_id,
            "release_id": self.release_id,
            "policy_version": self.policy_version,
            "pipeline_version": self.pipeline_version,
            "references": sorted(
                [
                    {
                        "evidence_type": r.evidence_type,
                        "artifact_hash": r.artifact_hash,
                        "verified": r.verified,
                    }
                    for r in self.references
                ],
                key=lambda x: str(x["evidence_type"]),
            ),
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "run_id": self.run_id,
            "source_id": self.source_id,
            "release_id": self.release_id,
            "policy_version": self.policy_version,
            "pipeline_version": self.pipeline_version,
            "created_at": self.created_at,
            "manifest_hash": self.compute_hash(),
            "references": [
                {
                    "evidence_type": r.evidence_type,
                    "artifact_hash": r.artifact_hash,
                    "verified": r.verified,
                    "notes": r.notes,
                }
                for r in self.references
            ],
        }


def create_evidence_manifest(
    run_id: str,
    source_id: str,
    release_id: str,
    pipeline_version: str = "0.1.0",
) -> EvidenceManifest:
    return EvidenceManifest(
        manifest_id=f"evid-{run_id}",
        run_id=run_id,
        source_id=source_id,
        release_id=release_id,
        policy_version="pilot_v1",
        pipeline_version=pipeline_version,
    )
