from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_OPAQUE_ID_VERSION = "v2"


def _opaque_id(document_artifact_id: str, entity_type: str, start: int, end: int) -> str:
    """Deterministic opaque finding ID from non-private fields only.

    Derived from: version prefix, document artifact ID, entity type,
    start offset, end offset. Never includes private text, aliases,
    company names, tickers, domains, or hashes of private values.
    """
    return hashlib.sha256(
        f"opaque:{_OPAQUE_ID_VERSION}:{document_artifact_id}:{entity_type}:{start}:{end}".encode()
    ).hexdigest()[:16]


@dataclass
class CoverageResult:
    company_id: str = ""
    document_artifact_id: str = ""
    total_discovered: int = 0
    total_masked: int = 0
    total_unmasked: int = 0
    coverage_pct: float = 0.0
    high_confidence_unmasked: int = 0
    entity_type_breakdown: dict[str, int] = field(default_factory=dict)
    unmasked_by_type: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    status: str = "completed"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        sanitized_unmasked: dict[str, list[dict[str, Any]]] = {}
        for dtype, entities in self.unmasked_by_type.items():
            sanitized_unmasked[dtype] = [
                {
                    "opaque_id": _opaque_id(
                        self.document_artifact_id,
                        dtype,
                        e.get("start", 0),
                        e.get("end", e.get("start", 0) + 1),
                    ),
                    "confidence": e.get("confidence", 0.0),
                }
                for e in entities
            ]
        return {
            "company_id": self.company_id,
            "document_artifact_id": self.document_artifact_id,
            "total_discovered": self.total_discovered,
            "total_masked": self.total_masked,
            "total_unmasked": self.total_unmasked,
            "coverage_pct": self.coverage_pct,
            "high_confidence_unmasked": self.high_confidence_unmasked,
            "entity_type_breakdown": self.entity_type_breakdown,
            "unmasked_by_type": sanitized_unmasked,
            "warnings": self.warnings,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class CoverageReport:
    def compute(
        self,
        discovered_entities: list,
        masked_accepted_spans: list[tuple[int, int]],
        company_id: str = "",
        document_artifact_id: str = "",
    ) -> CoverageResult:
        from ..masking.discovery import DiscoveryResult, ResidualEntityDiscoverer

        discoverer = ResidualEntityDiscoverer()
        result: DiscoveryResult = discoverer.compute_coverage(
            discovered_entities, masked_accepted_spans
        )

        coverage = CoverageResult(
            company_id=company_id,
            document_artifact_id=document_artifact_id,
            total_discovered=result.total_found,
            total_masked=result.masked_count,
            total_unmasked=result.unmasked_count,
            coverage_pct=result.coverage_pct,
            high_confidence_unmasked=len(result.unmasked_high_confidence),
        )

        for entity in discovered_entities:
            dtype = entity.discovery_type if hasattr(entity, "discovery_type") else "unknown"
            coverage.entity_type_breakdown[dtype] = coverage.entity_type_breakdown.get(dtype, 0) + 1

            if not discoverer._is_covered_by_spans(entity.start, entity.end, masked_accepted_spans):
                coverage.unmasked_by_type.setdefault(dtype, []).append(
                    {
                        "text": entity.text[:80] if hasattr(entity, "text") else "",
                        "start": entity.start if hasattr(entity, "start") else 0,
                        "end": entity.end if hasattr(entity, "end") else 0,
                        "confidence": entity.confidence if hasattr(entity, "confidence") else 0.0,
                    }
                )

        if result.unmasked_high_confidence:
            coverage.warnings.append(
                f"{len(result.unmasked_high_confidence)} high-confidence entities were not masked"
            )

        if coverage.coverage_pct < 50 and coverage.total_discovered > 0:
            coverage.warnings.append(
                f"Coverage is low ({coverage.coverage_pct}%): "
                f"most discovered entities were not masked"
            )

        return coverage
