"""SEC-specific data models.

Company reference and filing schemas for the SEC extraction pipeline.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


class CompanyReference(BaseModel):
    """Private company source reference with resolved CIK."""

    company_id: str = Field(..., description="Internal company ID, e.g., C001")
    ticker: str = Field(..., description="Stock ticker symbol, e.g., HBAN")
    cik: str = Field(..., description="10-digit zero-padded SEC CIK")
    config_source: str = Field(default="config", description="Source of this reference")
    config_hash: str = Field(default="", description="SHA-256 of configuration used")


class FilingReference(BaseModel):
    """Reference to a specific SEC filing."""

    accession_number: str = Field(..., description="SEC accession number")
    cik: str = Field(..., description="10-digit zero-padded CIK")
    form: str = Field(..., description="Form type, e.g., 10-K")
    filing_date: str = Field(..., description="Filing date (YYYY-MM-DD)")
    report_date: str = Field(default="", description="Report date / fiscal period end (YYYY-MM-DD)")
    primary_document: str = Field(..., description="Primary document filename")
    filing_url: str = Field(..., description="Full SEC URL to the filing document")
    discovery_timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="When this filing was discovered"
    )


class RawArtifact(BaseModel):
    """A raw downloaded SEC filing artifact."""

    artifact_id: str = Field(..., description="Unique artifact identifier")
    company_id: str = Field(..., description="Internal company ID")
    accession_number: str = Field(..., description="SEC accession number")
    source_url: str = Field(..., description="Source URL")
    local_path: Path = Field(..., description="Local relative path")
    media_type: str = Field(default="text/html", description="MIME type")
    byte_count: int = Field(..., description="File size in bytes")
    sha256: str = Field(..., description="SHA-256 hash of file content")
    sidecar_path: Path | None = Field(default=None, description="Path to .sha256 sidecar")
    retrieval_timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="When the file was retrieved"
    )


class BronzeDocument(BaseModel):
    """A bronze-layer extracted document artifact."""

    artifact_id: str = Field(..., description="Unique artifact identifier")
    company_id: str = Field(..., description="Internal company ID")
    source_artifact_id: str = Field(..., description="Source raw artifact ID")
    source_raw_sha256: str = Field(..., description="SHA-256 of source raw file")
    normalized_path: Path = Field(..., description="Local relative path to normalized text")
    sections_path: Path | None = Field(
        default=None, description="Local relative path to sections metadata"
    )
    text_sha256: str = Field(..., description="SHA-256 of normalized text file")
    section_count: int = Field(default=0, description="Number of sections found")
    converter_config_hash: str = Field(default="", description="Hash of extraction configuration")
    warnings: list[str] = Field(default_factory=list, description="Extraction warnings")
    extraction_timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="When extraction was performed"
    )
