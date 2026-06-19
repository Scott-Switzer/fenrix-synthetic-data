from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ReviewStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    DUPLICATE = "duplicate"
    ALREADY_REGISTERED = "already_registered"


class RiskBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ProviderCandidate:
    candidate_id: str
    company_id: str
    document_artifact_id: str
    chunk_ids: list[str] = field(default_factory=list)
    original_start: int = 0
    original_end: int = 0
    private_matched_text: str = ""
    matched_text_hash: str = ""
    proposed_entity_type: str = ""
    provider_label: str = ""
    provider_name: str = ""
    model_name: str = ""
    model_version: str = ""
    confidence: float = 0.0
    context_hash: str = ""
    discovery_policy_hash: str = ""
    chunking_policy_hash: str = ""
    duplicate_group_id: str = ""
    overlap_status: str = ""
    provider_evidence: dict[str, Any] = field(default_factory=dict)
    risk_score: float = 0.0
    risk_band: str = ""
    review_status: str = ReviewStatus.PENDING.value
    reviewer_decision: str = ""
    reviewer_reason: str = ""
    review_timestamp: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.matched_text_hash and self.private_matched_text:
            self.matched_text_hash = _hash_text(self.private_matched_text)


@dataclass
class DiscoveryChunk:
    chunk_id: str
    document_artifact_id: str
    chunk_index: int
    start_offset: int
    end_offset: int
    text: str
    input_hash: str = ""
    chunk_hash: str = ""
    section_hint: str = ""

    def __post_init__(self) -> None:
        if not self.input_hash:
            self.input_hash = _hash_text(self.text)
        if not self.chunk_hash:
            self.chunk_hash = _hash_text(f"{self.chunk_id}:{self.text}")


@dataclass
class EntityDiscoveryResponse:
    request_id: str
    provider_name: str
    model_name: str
    model_version: str
    company_id: str
    document_artifact_id: str
    chunk_id: str
    input_hash: str
    labels_requested: list[str]
    provider_candidates: list[ProviderCandidate]
    latency_ms: float = 0.0
    usage_token_count: int | None = None
    warnings: list[str] = field(default_factory=list)
    raw_response_hash: str = ""
    provider_config_hash: str = ""
    validation_counters: Any | None = None


@dataclass
class AmendmentProposal:
    proposal_id: str
    candidate_ids: list[str]
    evidence_refs: list[str]
    proposed_entity_type: str
    proposed_canonical_entity: str
    proposed_aliases: list[str]
    match_policy: str
    boundary_policy: str
    case_policy: str
    mutation_policies: list[str]
    pseudonym_class: str
    reviewer_decision: str
    reviewer_reason: str
    review_timestamp: datetime
    source_document_refs: list[str]
    conflict_analysis: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class DiscoveryReviewRecord:
    record_id: str
    candidate_id: str
    previous_status: str
    new_status: str
    reviewer_reason: str
    proposal_id: str | None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class SanitizedCandidateSummary:
    candidate_id: str
    opaque_id: str
    proposed_entity_type: str
    provider_name: str
    model_name: str
    confidence: float
    risk_band: str
    review_status: str
    duplicate_group_id: str
    provider_agreement_count: int = 0
    document_occurrence_count: int = 0
