"""Core artifact and stage enums."""

from enum import StrEnum


class ArtifactType(StrEnum):
    """Types of artifacts in the pipeline."""

    SOURCE_HTML = "source_html"
    RAW_MANIFEST = "raw_manifest"
    BRONZE_TEXT = "bronze_text"
    BRONZE_MANIFEST = "bronze_manifest"


class StageName(StrEnum):
    """Pipeline stage names."""

    DISCOVER = "discover"
    INGEST = "ingest"
    EXTRACT = "extract"
    MANIFEST = "manifest"
    REGISTRY = "registry"
    MASK = "mask"
    SCAN = "scan"


class StageStatus(StrEnum):
    """Stage execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
