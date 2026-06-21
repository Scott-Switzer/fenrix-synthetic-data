"""Pilot orchestration for Phase 4.

Builds the SRC_001 → SYNTH_001 pilot pipeline:
load manifest → compile atlas → mask text → transform structured →
run attacks → evaluate utility → assess release → export dossier.
"""

from .manifest import (
    DocumentType,
    SeriesFormat,
    SourceDocument,
    SourceManifest,
    SourceSeries,
)
from .orchestrator import (
    RunConfig,
    RunManifest,
    StageName,
    StageResult,
    StageStatus,
    run_pilot,
)
from .schemas import (
    CandidateEntry,
    CandidateUniverse,
    load_candidate_universe,
)

__all__ = [
    "CandidateEntry",
    "CandidateUniverse",
    "DocumentType",
    "RunConfig",
    "RunManifest",
    "SeriesFormat",
    "SourceDocument",
    "SourceManifest",
    "SourceSeries",
    "StageName",
    "StageResult",
    "StageStatus",
    "load_candidate_universe",
    "run_pilot",
]
