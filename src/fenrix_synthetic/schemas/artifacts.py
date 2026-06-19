"""Core artifact and stage enums."""

from enum import StrEnum


class ArtifactType(StrEnum):
    """Types of artifacts in the pipeline."""

    SOURCE_HTML = "source_html"
    RAW_MANIFEST = "raw_manifest"
    BRONZE_TEXT = "bronze_text"
    BRONZE_MANIFEST = "bronze_manifest"


class StageName(StrEnum):
    """Pipeline stage names for M0+M1."""

    DISCOVER = "discover"
    INGEST = "ingest"
    EXTRACT = "extract"
    MANIFEST = "manifest"


class StageStatus(StrEnum):
    """Stage execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
