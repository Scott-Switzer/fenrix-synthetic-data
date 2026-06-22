"""Private evidence graph and public sanitized schemas for professor bundles.

Evidence objects form a private directed graph: source artifacts produce
detected entities, which produce replacements, which produce sanitized
sections, which are cross-linked to synthetic metrics and pedagogy.

Public artifacts reference only opaque provenance keys — never private
source refs, raw tickers, CIKs, or accession numbers.

Provenance key format:
    COMPANY_001:FILING:10K:2024:ITEM7
    COMPANY_001:TABLE:10K:2024:INCOME_STATEMENT
    COMPANY_001:METRIC:DAILY_RETURNS:WINDOW_2024FY
    COMPANY_001:NEWS:NEWS_003
    COMPANY_001:QA:ENTITY_AUDIT
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EvidenceType(StrEnum):
    """Types of evidence objects in the private evidence graph."""

    SOURCE_FILING = "SOURCE_FILING"
    SOURCE_SECTION = "SOURCE_SECTION"
    SOURCE_TABLE = "SOURCE_TABLE"
    SOURCE_NEWS_ITEM = "SOURCE_NEWS_ITEM"
    SOURCE_METRIC_SERIES = "SOURCE_METRIC_SERIES"
    DETECTED_ENTITY = "DETECTED_ENTITY"
    ENTITY_REPLACEMENT = "ENTITY_REPLACEMENT"
    PRIVATE_SOURCE_REF = "PRIVATE_SOURCE_REF"
    SYNTHETIC_COMPANY_PROFILE = "SYNTHETIC_COMPANY_PROFILE"
    SANITIZED_SECTION = "SANITIZED_SECTION"
    SANITIZED_TABLE = "SANITIZED_TABLE"
    SYNTHETIC_METRIC_SERIES = "SYNTHETIC_METRIC_SERIES"
    CLASSROOM_CROSS_LINK = "CLASSROOM_CROSS_LINK"
    PEDAGOGY_EXERCISE = "PEDAGOGY_EXERCISE"
    RELEASE_GATE_REPORT = "RELEASE_GATE_REPORT"


class PrivateSourceRef(BaseModel):
    """Private reference to a source artifact. Never exported to public bundle."""

    ref_id: str
    ref_type: str
    company_id: str
    private_value: str = ""  # ticker, CIK, accession — NEVER in public output
    source_path: str = ""  # private filesystem path — NEVER in public output
    source_url: str = ""  # SEC URL — NEVER in public output
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    model_config = {"extra": "forbid"}


class SourceFiling(BaseModel):
    """A source SEC filing in the private evidence graph."""

    filing_id: str
    company_id: str
    form_type: str  # 10-K, 10-Q, 8-K
    filing_date: str
    period_end: str
    accession_ref: str  # private ref to accession — never in public output
    provenance_key: str  # public provenance key
    source_ref: PrivateSourceRef | None = None  # private only
    section_count: int = 0
    table_count: int = 0

    model_config = {"extra": "forbid"}


class SourceSection(BaseModel):
    """A section extracted from a source SEC filing."""

    section_id: str
    filing_id: str
    company_id: str
    item_id: str  # e.g., "ITEM_7", "ITEM_1A"
    item_title: str  # e.g., "Management's Discussion and Analysis"
    text_content: str  # private raw text — never in public output
    char_count: int = 0
    provenance_key: str
    source_ref: PrivateSourceRef | None = None

    model_config = {"extra": "forbid"}


class SourceTable(BaseModel):
    """A table extracted from a source SEC filing."""

    table_id: str
    filing_id: str
    company_id: str
    table_name: str  # e.g., "INCOME_STATEMENT", "BALANCE_SHEET"
    table_data: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    col_count: int = 0
    provenance_key: str
    source_ref: PrivateSourceRef | None = None

    model_config = {"extra": "forbid"}


class SourceNewsItem(BaseModel):
    """A source news item in the private evidence graph."""

    news_id: str
    company_id: str
    headline: str  # private — never in public output
    published_date: str
    source_url: str = ""  # private — never in public output
    text_content: str = ""  # private — never in public output
    provenance_key: str
    source_ref: PrivateSourceRef | None = None

    model_config = {"extra": "forbid"}


class SourceMetricSeries(BaseModel):
    """A source metric time series in the private evidence graph."""

    series_id: str
    company_id: str
    metric_type: str  # daily_prices, returns, volume, fundamentals
    data: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    date_range: str = ""
    provenance_key: str
    source_ref: PrivateSourceRef | None = None

    model_config = {"extra": "forbid"}


class DetectedEntity(BaseModel):
    """An entity detected by rules or GLiNER in source text."""

    entity_id: str
    company_id: str
    entity_type: str  # company, ticker, executive, product, etc.
    detected_text: str  # private — the actual text span, never in public output
    detection_method: str  # "rules" or "gliner"
    confidence: float = 1.0
    start_offset: int = 0
    end_offset: int = 0
    source_artifact_id: str = ""  # which section/filing it came from
    provenance_key: str

    model_config = {"extra": "forbid"}


class EntityReplacement(BaseModel):
    """A replacement applied to de-identify a detected entity."""

    replacement_id: str
    entity_id: str
    company_id: str
    original_value: str = ""  # private — never in public output
    replacement_value: str  # e.g., "Company 001"
    replacement_type: str  # pseudonym, redaction, generalization
    provenance_key: str

    model_config = {"extra": "forbid"}


class SyntheticCompanyProfile(BaseModel):
    """Synthetic company profile for public release."""

    company_id: str  # opaque, e.g., "COMPANY_001"
    synthetic_name: str
    synthetic_ticker: str
    synthetic_industry: str
    synthetic_sector: str
    synthetic_description: str
    provenance_key: str

    model_config = {"extra": "forbid"}


class SanitizedSection(BaseModel):
    """A sanitized (de-identified) section for public release."""

    section_id: str
    company_id: str
    item_id: str
    item_title: str
    sanitized_text: str  # public-safe text
    char_count: int = 0
    provenance_key: str
    replacement_count: int = 0

    model_config = {"extra": "forbid"}


class SanitizedTable(BaseModel):
    """A sanitized table for public release."""

    table_id: str
    company_id: str
    table_name: str
    sanitized_data: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    col_count: int = 0
    provenance_key: str

    model_config = {"extra": "forbid"}


class SyntheticMetricSeries(BaseModel):
    """A synthetic metric series for public release."""

    series_id: str
    company_id: str
    metric_type: str
    synthetic_data: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    date_range: str = ""
    provenance_key: str

    model_config = {"extra": "forbid"}


class ClassroomCrossLink(BaseModel):
    """A cross-link between classroom artifacts."""

    link_id: str
    company_id: str
    source_artifact: str  # e.g., "FILING:10K:2024:ITEM7"
    target_artifact: str  # e.g., "METRIC:DAILY_RETURNS:WINDOW_2024FY"
    link_type: str  # filing-to-metric, news-to-filing, etc.
    description: str
    markdown_link: str  # e.g., "[See MD&A](anonymized/COMPANY_001/sec/item7.md)"

    model_config = {"extra": "forbid"}


class PedagogyExercise(BaseModel):
    """A classroom exercise with provenance."""

    exercise_id: str
    company_id: str
    exercise_type: str  # filing-to-metric, news-to-filing, risk-factor, etc.
    question: str
    answer_stub: str = ""  # professor must fill in
    provenance_keys: list[str] = Field(default_factory=list)
    markdown_links: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ReleaseGateReport(BaseModel):
    """Release gate decision report."""

    gate_id: str
    company_id: str
    decision: str  # PASS, FAIL, REVIEW_REQUIRED
    blocking_failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    professor_ready: bool = False
    beta_status: str = "NOT_PROFESSOR_READY"
    gate_hash: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    model_config = {"extra": "forbid"}


# ── Provenance key builders ─────────────────────────────────────────────


def build_provenance_key(
    company_id: str,
    artifact_type: str,
    form_type: str = "",
    period: str = "",
    item: str = "",
) -> str:
    """Build a stable public provenance key.

    Examples:
        COMPANY_001:FILING:10K:2024:ITEM7
        COMPANY_001:TABLE:10K:2024:INCOME_STATEMENT
        COMPANY_001:METRIC:DAILY_RETURNS:WINDOW_2024FY
        COMPANY_001:NEWS:NEWS_003
        COMPANY_001:QA:ENTITY_AUDIT
    """
    parts = [company_id, artifact_type]
    if form_type:
        parts.append(form_type)
    if period:
        parts.append(period)
    if item:
        parts.append(item)
    return ":".join(parts)


def compute_opaque_id(*parts: str) -> str:
    """Compute a deterministic opaque ID from non-private parts.

    Used for IDs that must not reveal private values.
    """
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


# ── Public serialization guard ──────────────────────────────────────────


class PublicSerializationError(Exception):
    """Raised when a private field is detected in public serialization."""


# Fields that must NEVER appear in public (sanitized) output
PRIVATE_FIELDS: frozenset[str] = frozenset(
    {
        "source_ref",
        "private_value",
        "source_path",
        "source_url",
        "text_content",
        "detected_text",
        "original_value",
        "headline",
        "data",  # raw source data
        "table_data",  # raw table data
    }
)


def sanitize_for_public(obj: BaseModel) -> dict[str, Any]:
    """Serialize a model for public output, stripping private fields.

    Raises PublicSerializationError if a private field is found.
    """
    raw = obj.model_dump()
    sanitized: dict[str, Any] = {}
    for key, value in raw.items():
        if key in PRIVATE_FIELDS:
            raise PublicSerializationError(
                f"Private field '{key}' found in {type(obj).__name__} — "
                "cannot serialize for public output"
            )
        if isinstance(value, dict):
            value = _sanitize_dict_recursive(value)
        sanitized[key] = value
    return sanitized


def _sanitize_dict_recursive(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip private fields from nested dicts."""
    result: dict[str, Any] = {}
    for key, value in d.items():
        if key in PRIVATE_FIELDS:
            raise PublicSerializationError(
                f"Private field '{key}' found in nested dict — cannot serialize for public output"
            )
        if isinstance(value, dict):
            value = _sanitize_dict_recursive(value)
        result[key] = value
    return result


def validate_public_artifact(artifact: dict[str, Any]) -> list[str]:
    """Validate that a public artifact dict has no private fields.

    Returns list of violations (empty = valid).
    """
    violations: list[str] = []

    def _check(d: dict[str, Any], path: str = "") -> None:
        for key, value in d.items():
            field_path = f"{path}.{key}" if path else key
            if key in PRIVATE_FIELDS:
                violations.append(f"Private field '{field_path}' in public artifact")
            if isinstance(value, dict):
                _check(value, field_path)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        _check(item, f"{field_path}[{i}]")

    _check(artifact)
    return violations


def validate_provenance_key(key: str) -> bool:
    """Validate that a provenance key has the expected format."""
    if not key:
        return False
    parts = key.split(":")
    if len(parts) < 2:
        return False
    if not parts[0].startswith("COMPANY_"):
        return False
    return True
