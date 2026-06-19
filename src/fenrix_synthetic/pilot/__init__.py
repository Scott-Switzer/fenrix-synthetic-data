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

__all__ = [
    "DocumentType",
    "SeriesFormat",
    "SourceDocument",
    "SourceManifest",
    "SourceSeries",
]
