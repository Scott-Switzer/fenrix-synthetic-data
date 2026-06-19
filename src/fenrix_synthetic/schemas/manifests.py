"""Manifest schemas for source, raw, and bronze layers."""

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SourceManifest(BaseModel):
    """Represents a source SEC filing before local processing."""

    company_id: str = Field(..., description="Internal company ID")
    source: str = Field(default="sec", description="Source system: sec, fixture, etc.")
    accession_number: str = Field(..., description="SEC accession number")
    filing_date: str = Field(..., description="Filing date (YYYY-MM-DD)")
    form_type: str = Field(..., description="Form type, e.g., 10-K, 10-Q")
    primary_document_url: str = Field(..., description="URL to primary document")
    local_path: Path = Field(..., description="Local path to downloaded file")
    content_hash: str = Field(..., description="SHA-256 hash of downloaded file")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class RawManifest(BaseModel):
    """Manifest for raw layer artifacts (immutable source)."""

    artifact_id: str = Field(..., description="Unique artifact identifier")
    artifact_type: str = Field(default="raw_manifest", description="Artifact type")
    company_id: str = Field(..., description="Internal company ID")
    source_manifest_hash: str = Field(..., description="SHA-256 of source manifest")
    configuration_hash: str = Field(..., description="SHA-256 of configuration used")
    content_hash: str = Field(..., description="SHA-256 of artifact content")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    pipeline_version: str = Field(..., description="Pipeline version")
    stage: str = Field(default="ingest", description="Stage that produced this")
    status: str = Field(default="completed", description="Stage status")
    original_filename: str = Field(..., description="Original filename")
    mime_type: str = Field(default="text/html", description="MIME type")


class BronzeManifest(BaseModel):
    """Manifest for bronze layer artifacts (extracted text)."""

    artifact_id: str = Field(..., description="Unique artifact identifier")
    artifact_type: str = Field(default="bronze_manifest", description="Artifact type")
    company_id: str = Field(..., description="Internal company ID")
    source_artifact_id: str = Field(..., description="ID of source raw artifact")
    source_manifest_hash: str = Field(..., description="SHA-256 of source manifest")
    configuration_hash: str = Field(..., description="SHA-256 of configuration used")
    content_hash: str = Field(..., description="SHA-256 of artifact content")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    pipeline_version: str = Field(..., description="Pipeline version")
    stage: str = Field(default="extract", description="Stage that produced this")
    status: str = Field(default="completed", description="Stage status")
    extraction_method: str = Field(default="html_to_text", description="Extraction method used")
    sections: list[dict[str, Any]] = Field(default_factory=list, description="Document sections")
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Parsing diagnostics")
