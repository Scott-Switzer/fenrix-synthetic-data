"""Release gate and dossier subsystem.

- Release gate: Deterministric PASS/FAIL/REVIEW_REQUIRED assessment
- Release dossier: Sanitized bundle generation for SYNTH_001
"""

from .dossier import build_checksums, generate_dossier, validate_dossier
from .gate import (
    GateCondition,
    ReleaseDecision,
    ReleaseGateResult,
    evaluate_release_gate,
)

__all__ = [
    "GateCondition",
    "ReleaseDecision",
    "ReleaseGateResult",
    "build_checksums",
    "evaluate_release_gate",
    "generate_dossier",
    "validate_dossier",
]
