"""Stage checkpoint schemas for resume behavior."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .artifacts import StageName, StageStatus


class OutputArtifact(BaseModel):
    """Reference to an output artifact."""

    path: Path = Field(..., description="Path to artifact")
    hash: str = Field(..., description="SHA-256 hash of artifact content")


class StageCheckpoint(BaseModel):
    """Checkpoint for a completed stage."""

    stage: StageName = Field(..., description="Stage name")
    company_id: str = Field(..., description="Company ID")
    input_hash: str = Field(..., description="SHA-256 hash of stage inputs")
    config_hash: str = Field(..., description="SHA-256 hash of stage configuration")
    output_artifacts: list[OutputArtifact] = Field(
        default_factory=list, description="Output artifacts"
    )
    status: StageStatus = Field(default=StageStatus.COMPLETED, description="Stage status")
    completed_at: datetime = Field(
        default_factory=datetime.utcnow, description="Completion timestamp"
    )
    pipeline_version: str = Field(..., description="Pipeline version")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class CheckpointStatus(StrEnum):
    """Status of checkpoint validation."""

    VALID = "valid"
    INVALID_HASH = "invalid_hash"
    MISSING_ARTIFACT = "missing_artifact"
    CONFIG_CHANGED = "config_changed"
    VERSION_CHANGED = "version_changed"
    CORRUPT = "corrupt"


class CheckpointValidationResult(BaseModel):
    """Result of checkpoint validation."""

    stage: StageName
    company_id: str
    status: CheckpointStatus
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
