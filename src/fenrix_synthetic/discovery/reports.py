from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .schemas import ProviderCandidate, SanitizedCandidateSummary


@dataclass
class SanitizedDiscoveryReport:
    company_id: str
    document_artifact_id: str
    input_hash: str
    provider_name: str
    model_name: str
    model_version: str
    total_candidates: int = 0
    candidates_by_type: dict[str, int] = field(default_factory=dict)
    candidates_by_band: dict[str, int] = field(default_factory=dict)
    candidates_by_status: dict[str, int] = field(default_factory=dict)
    pending_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    deferred_count: int = 0
    duplicate_count: int = 0
    already_registered_count: int = 0
    provider_agreement_groups: int = 0
    duplicate_groups: int = 0
    average_latency_ms: float = 0.0
    total_token_count: int = 0
    warnings: list[str] = field(default_factory=list)
    status: str = "completed"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "document_artifact_id": self.document_artifact_id,
            "input_hash": self.input_hash,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "total_candidates": self.total_candidates,
            "candidates_by_type": self.candidates_by_type,
            "candidates_by_band": self.candidates_by_band,
            "candidates_by_status": self.candidates_by_status,
            "pending_count": self.pending_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "deferred_count": self.deferred_count,
            "duplicate_count": self.duplicate_count,
            "already_registered_count": self.already_registered_count,
            "provider_agreement_groups": self.provider_agreement_groups,
            "duplicate_groups": self.duplicate_groups,
            "average_latency_ms": round(self.average_latency_ms, 1),
            "total_token_count": self.total_token_count,
            "warnings": self.warnings,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class PrivateDiscoveryArtifact:
    company_id: str
    document_artifact_id: str
    input_hash: str
    candidates: list[ProviderCandidate]
    sanitized_summaries: list[SanitizedCandidateSummary]
    raw_provider_responses: list[dict[str, Any]]
    review_records: list[dict[str, Any]]
    provider_config_hashes: list[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build_sanitized_report(
    candidates: list[ProviderCandidate],
    provider_name: str,
    model_name: str,
    model_version: str,
    company_id: str,
    document_artifact_id: str,
    input_hash: str,
    latency_ms: float,
    token_count: int | None,
    warnings: list[str],
    duplicate_groups: int,
) -> SanitizedDiscoveryReport:
    type_counts: dict[str, int] = {}
    band_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}

    for c in candidates:
        type_counts[c.proposed_entity_type] = type_counts.get(c.proposed_entity_type, 0) + 1
        band_counts[c.risk_band] = band_counts.get(c.risk_band, 0) + 1
        status_counts[c.review_status] = status_counts.get(c.review_status, 0) + 1

    return SanitizedDiscoveryReport(
        company_id=company_id,
        document_artifact_id=document_artifact_id,
        input_hash=input_hash,
        provider_name=provider_name,
        model_name=model_name,
        model_version=model_version,
        total_candidates=len(candidates),
        candidates_by_type=type_counts,
        candidates_by_band=band_counts,
        candidates_by_status=status_counts,
        pending_count=status_counts.get("pending", 0),
        accepted_count=status_counts.get("accepted", 0),
        rejected_count=status_counts.get("rejected", 0),
        deferred_count=status_counts.get("deferred", 0),
        duplicate_count=status_counts.get("duplicate", 0),
        already_registered_count=status_counts.get("already_registered", 0),
        provider_agreement_groups=0,
        duplicate_groups=duplicate_groups,
        average_latency_ms=latency_ms,
        total_token_count=token_count or 0,
        warnings=warnings,
    )
