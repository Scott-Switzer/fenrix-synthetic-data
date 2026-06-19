"""Pilot manifest schemas for SRC_001 ingestion (Phase 4D).

Strict schemas for:
- source manifest
- document records
- structured-series records
- provenance
- relative dates
- hashes
- extractor versions

All real data lives under FENRIX_PRIVATE_ROOT. No raw data in repository fixtures.
Synthetic fixtures only for testing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class DocumentType(StrEnum):
    FILING_10K = "10-K"
    FILING_10Q = "10-Q"
    FILING_8K = "8-K"
    NEWS_HEADLINE = "news_headline"
    EARNINGS_RELEASE = "earnings_release"
    EARNINGS_TRANSCRIPT = "earnings_transcript"
    OTHER = "other"


class SourceDocument(BaseModel):
    """A single source document record."""

    document_id: str
    document_type: DocumentType
    source_path: str  # Relative to FENRIX_PRIVATE_ROOT
    content_hash: str = ""
    char_count: int = 0
    filing_date: str = ""  # YYYY-MM-DD
    report_date: str = ""  # YYYY-MM-DD
    accession_number: str = ""
    extractor_version: str = ""
    provenance: str = ""
    metadata: dict = Field(default_factory=dict)

    @field_validator("document_id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("document_id must not be empty")
        return v.strip()


class SeriesFormat(StrEnum):
    OHLCV = "ohlcv"
    RATIOS = "ratios"


class SourceSeries(BaseModel):
    """A single structured data series record."""

    series_id: str
    format: SeriesFormat
    source_path: str  # Relative to FENRIX_PRIVATE_ROOT
    content_hash: str = ""
    row_count: int = 0
    start_date: str = ""  # YYYY-MM-DD
    end_date: str = ""
    columns: list[str] = Field(default_factory=list)
    extractor_version: str = ""
    provenance: str = ""

    @field_validator("series_id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("series_id must not be empty")
        return v.strip()


class SourceManifest(BaseModel):
    """Manifest of all source inputs for a pilot run."""

    manifest_id: str
    schema_version: str = "1.0.0"
    company_id: str  # SRC_001
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    documents: list[SourceDocument] = Field(default_factory=list)
    series: list[SourceSeries] = Field(default_factory=list)
    extractor_versions: dict[str, str] = Field(default_factory=dict)
    manifest_hash: str = ""

    @field_validator("company_id")
    @classmethod
    def validate_company_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("company_id must not be empty")
        # Never accept real company names as company_id
        if v.strip() not in (
            "SRC_001",
            "TEST-CO-001",
            "provider-health-check",
            "provider-prepare-check",
        ):
            raise ValueError(f"company_id must be SRC_001 or a known test identifier, got '{v}'")
        return v.strip()

    def config_hash(self) -> str:
        import hashlib

        import orjson

        data = {
            "manifest_id": self.manifest_id,
            "schema_version": self.schema_version,
            "company_id": self.company_id,
            "documents": sorted(
                [
                    {"document_id": d.document_id, "content_hash": d.content_hash}
                    for d in self.documents
                ],
                key=lambda x: x["document_id"],
            ),
            "series": sorted(
                [{"series_id": s.series_id, "content_hash": s.content_hash} for s in self.series],
                key=lambda x: x["series_id"],
            ),
        }
        raw = orjson.dumps(data, option=orjson.OPT_SORT_KEYS)
        return hashlib.sha256(raw).hexdigest()
