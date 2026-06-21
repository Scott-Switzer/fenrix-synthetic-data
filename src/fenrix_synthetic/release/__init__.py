"""Release gate and dossier subsystem.

- Release gate: Deterministic PASS/FAIL/REVIEW_REQUIRED assessment
- Release dossier: Sanitized bundle generation for SYNTH_001
- Evidence manifest: Canonical evidence for release gate consumption
- Pseudonym paths: Deterministic public aliases for export paths and identifiers
- Namespace scanner: Recursive leak scanner for paths, filenames, JSON, Parquet, ZIP
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
from .namespace_scanner import scan_release_tree
from .pseudonym_paths import (
    PseudonymPathMap,
    build_pseudonym_path_map,
    build_xbrl_cik_patterns,
)

__all__ = [
    "EvidenceManifest",
    "EvidenceReference",
    "GateCondition",
    "PseudonymPathMap",
    "ReleaseDecision",
    "ReleaseGateResult",
    "build_checksums",
    "build_pseudonym_path_map",
    "build_xbrl_cik_patterns",
    "create_evidence_manifest",
    "evaluate_release_gate",
    "generate_dossier",
    "scan_release_tree",
    "validate_dossier",
]
