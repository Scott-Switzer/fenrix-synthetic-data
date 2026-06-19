from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ConflictStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SHADOWED = "shadowed"


class MatchResult(BaseModel):
    span_id: str
    document_artifact_id: str
    original_start: int
    original_end: int
    entity_id: str
    alias_id: str
    entity_type: str
    match_policy: str
    priority: int
    matched_text_hash: str
    replacement: str
    conflict_status: ConflictStatus = ConflictStatus.ACCEPTED
    detection_source: str = "deterministic"


class MaskingAudit(BaseModel):
    audit_id: str
    company_id: str
    document_artifact_id: str
    source_bronze_artifact_id: str
    registry_id: str
    masking_policy_hash: str
    total_matches: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    shadowed_count: int = 0
    overlap_count: int = 0
    spans: list[MatchResult] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MaskingSummary(BaseModel):
    company_id: str
    document_artifact_id: str
    input_artifact_id: str
    input_hash: str
    output_hash: str
    registry_hash: str
    masking_policy_hash: str
    pseudonym_policy_version: str = ""
    match_count: int = 0
    replacement_count: int = 0
    overlap_count: int = 0
    residual_hit_count: int = 0
    warnings: list[str] = []
    status: str = "completed"
