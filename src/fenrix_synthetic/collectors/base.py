"""Base collector interface and shared result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CollectionStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass
class CollectorResult:
    """Result from a single collection operation."""

    source: str
    artifact_type: str
    status: CollectionStatus
    requested_range: tuple[str | None, str | None]
    observed_range: tuple[str | None, str | None]
    row_count: int = 0
    column_count: int = 0
    missing_count: int = 0
    fetch_timestamp: str = ""
    parser_version: str = ""
    schema_version: str = "1.0.0"
    content_type: str = ""
    relative_path: str = ""
    byte_size: int = 0
    sha256: str = ""
    failure_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "artifact_type": self.artifact_type,
            "status": self.status.value,
            "requested_range": self.requested_range,
            "observed_range": self.observed_range,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "missing_count": self.missing_count,
            "fetch_timestamp": self.fetch_timestamp,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "content_type": self.content_type,
            "relative_path": self.relative_path,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "failure_reason": self.failure_reason,
            "metadata": self.metadata,
        }
