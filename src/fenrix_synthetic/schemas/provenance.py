"""Source provenance record for reused code tracking."""

from datetime import datetime

from pydantic import BaseModel, Field


class SourceProvenanceRecord(BaseModel):
    """Records origin of reused code per AGENTS.md §39-49."""

    source_repository: str = Field(..., description="Source repository name")
    source_path: str = Field(..., description="Path in source repository")
    source_commit: str = Field(..., description="Full git commit SHA")
    original_responsibility: str = Field(..., description="Original responsibility in source")
    reason_for_reuse: str = Field(..., description="Why reuse instead of reimplement")
    dependencies: list[str] = Field(default_factory=list, description="Dependencies introduced")
    modifications: list[str] = Field(default_factory=list, description="Modifications made")
    applicable_license: str = Field(..., description="Applicable license (MIT, Apache-2.0, etc.)")
    attribution: str | None = Field(default=None, description="Required attribution text")
    tests_added: list[str] = Field(default_factory=list, description="New test paths added")
    recorded_at: datetime = Field(
        default_factory=datetime.utcnow, description="Record creation time"
    )
    recorded_by: str = Field(default="fenrix-synthetic", description="Recorder identity")
