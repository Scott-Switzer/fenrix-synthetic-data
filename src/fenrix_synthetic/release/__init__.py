"""Release gate and dossier subsystem.

- Release gate: Deterministic PASS/FAIL/REVIEW_REQUIRED assessment
- Release dossier: Sanitized bundle generation for SYNTH_001
- Evidence manifest: Canonical evidence for release gate consumption
"""

from .dossier import build_checksums, generate_dossier, validate_dossier
from .evidence import (
    EvidenceManifest,
    EvidenceReference,
    create_evidence_manifest,
)
from .gate import (
    GateCondition,
    ReleaseDecision,
    ReleaseGateResult,
    evaluate_release_gate,
)

__all__ = [
    "EvidenceManifest",
    "EvidenceReference",
    "GateCondition",
    "ReleaseDecision",
    "ReleaseGateResult",
    "build_checksums",
    "create_evidence_manifest",
    "evaluate_release_gate",
    "generate_dossier",
    "validate_dossier",
]
