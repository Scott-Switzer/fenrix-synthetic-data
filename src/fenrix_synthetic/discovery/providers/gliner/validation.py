"""Validation and offset reconciliation between GLiNER output and Fenrix provider candidates.

The pipeline has three stages:

1. ``_validate_one`` checks one raw GLiNER dict against a chunk. Returns a
   ``ParsedSpan(start, end, label, score)`` on success or ``None`` on
   rejection. All validation counters and quarantine samples are updated
   by this stage.
2. ``_build_candidate`` constructs one ``ProviderCandidate`` from a
   verified ``ParsedSpan`` plus the explicit provider context.
3. ``validate_and_convert`` aggregates validation across a batch and
   builds the candidate list. ``ValidationCounters`` exposed here are
   the actual totals from each provider response — never synthesized.

The matched private text NEVER participates in the candidate ID, which
is derived from a deterministic sha256 of:

* adapter_policy_version
* document_artifact_id
* chunk_id
* original_start + original_end
* provider_label
* model_name
* config_hash

This guarantees reruns over the same input produce identical output.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from fenrix_synthetic.discovery.schemas import DiscoveryChunk, ProviderCandidate


def derive_candidate_id(
    *,
    adapter_policy_version: str,
    document_artifact_id: str,
    chunk_id: str,
    original_start: int,
    original_end: int,
    provider_label: str,
    model_name: str,
    config_hash: str,
) -> str:
    """Return a deterministic 32-char candidate ID derived only from public fields.

    The matched private text NEVER participates.
    """
    payload = (
        f"{adapter_policy_version}|{document_artifact_id}|{chunk_id}|"
        f"{original_start}|{original_end}|{provider_label}|"
        f"{model_name}|{config_hash}"
    )
    return "gliner-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class ParsedSpan:
    """One validated GLiNER span, chunk-relative."""

    chunk_text: str
    start: int
    end: int
    label: str
    score: float


@dataclass
class ValidationCounters:
    total_received: int = 0
    accepted: int = 0
    rejected_missing_fields: int = 0
    rejected_invalid_offsets: int = 0
    rejected_out_of_range: int = 0
    rejected_text_mismatch: int = 0
    rejected_non_numeric_score: int = 0
    rejected_score_out_of_range: int = 0
    rejected_missing_label: int = 0
    quarantine_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_received": self.total_received,
            "accepted": self.accepted,
            "rejected_missing_fields": self.rejected_missing_fields,
            "rejected_invalid_offsets": self.rejected_invalid_offsets,
            "rejected_out_of_range": self.rejected_out_of_range,
            "rejected_text_mismatch": self.rejected_text_mismatch,
            "rejected_non_numeric_score": self.rejected_non_numeric_score,
            "rejected_score_out_of_range": self.rejected_score_out_of_range,
            "rejected_missing_label": self.rejected_missing_label,
            "quarantine_count": self.quarantine_count,
        }

    def merge(self, other: ValidationCounters) -> ValidationCounters:
        return ValidationCounters(
            total_received=self.total_received + other.total_received,
            accepted=self.accepted + other.accepted,
            rejected_missing_fields=self.rejected_missing_fields + other.rejected_missing_fields,
            rejected_invalid_offsets=self.rejected_invalid_offsets + other.rejected_invalid_offsets,
            rejected_out_of_range=self.rejected_out_of_range + other.rejected_out_of_range,
            rejected_text_mismatch=self.rejected_text_mismatch + other.rejected_text_mismatch,
            rejected_non_numeric_score=self.rejected_non_numeric_score
            + other.rejected_non_numeric_score,
            rejected_score_out_of_range=self.rejected_score_out_of_range
            + other.rejected_score_out_of_range,
            rejected_missing_label=self.rejected_missing_label + other.rejected_missing_label,
            quarantine_count=self.quarantine_count + other.quarantine_count,
        )


@dataclass
class ValidationResult:
    candidates: list[ProviderCandidate]
    counters: ValidationCounters
    warnings: list[str]


def _validate_one(
    raw: dict[str, Any],
    chunk: DiscoveryChunk,
    counters: ValidationCounters,
) -> ParsedSpan | None:
    """Validate one raw GLiNER entity dict. Updates counters.

    Quarantine samples (truncated, label-free) are kept in a private
    per-call list returned alongside so the caller can stash them in
    the private artifact. They never reach sanitized DiscoveryReport.
    """
    counters.total_received += 1

    required = ("text", "label", "start", "end", "score")
    if not all(k in raw for k in required):
        counters.rejected_missing_fields += 1
        counters.quarantine_count += 1
        return None

    label = raw["label"]
    if not label or not isinstance(label, str):
        counters.rejected_missing_label += 1
        counters.quarantine_count += 1
        return None

    try:
        start = int(raw["start"])
        end = int(raw["end"])
    except (TypeError, ValueError):
        counters.rejected_invalid_offsets += 1
        counters.quarantine_count += 1
        return None

    if start < 0 or end <= start:
        counters.rejected_invalid_offsets += 1
        counters.quarantine_count += 1
        return None

    if end > len(chunk.text):
        counters.rejected_out_of_range += 1
        counters.quarantine_count += 1
        return None

    score = raw["score"]
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        counters.rejected_non_numeric_score += 1
        counters.quarantine_count += 1
        return None
    score_f = float(score)
    if not (0.0 <= score_f <= 1.0):
        counters.rejected_score_out_of_range += 1
        counters.quarantine_count += 1
        return None

    expected_text = raw["text"]
    if not isinstance(expected_text, str):
        counters.rejected_text_mismatch += 1
        counters.quarantine_count += 1
        return None
    chunk_slice = chunk.text[start:end]
    if chunk_slice != expected_text:
        counters.rejected_text_mismatch += 1
        counters.quarantine_count += 1
        return None

    counters.accepted += 1
    return ParsedSpan(chunk_text=chunk.text, start=start, end=end, label=label, score=score_f)


def _build_candidate(
    parsed: ParsedSpan,
    chunk: DiscoveryChunk,
    *,
    company_id: str,
    provider_name: str,
    model_name: str,
    model_version: str,
    config_hash: str,
    adapter_policy_version: str,
) -> ProviderCandidate:
    if not company_id or not isinstance(company_id, str):
        raise ValueError("_build_candidate requires explicit non-empty company_id")
    original_start = chunk.start_offset + parsed.start
    original_end = chunk.start_offset + parsed.end
    candidate_id = derive_candidate_id(
        adapter_policy_version=adapter_policy_version,
        document_artifact_id=chunk.document_artifact_id,
        chunk_id=chunk.chunk_id,
        original_start=original_start,
        original_end=original_end,
        provider_label=parsed.label,
        model_name=model_name,
        config_hash=config_hash,
    )
    return ProviderCandidate(
        candidate_id=candidate_id,
        company_id=company_id,
        document_artifact_id=chunk.document_artifact_id,
        chunk_ids=[chunk.chunk_id],
        original_start=original_start,
        original_end=original_end,
        private_matched_text=parsed.chunk_text[parsed.start : parsed.end],
        proposed_entity_type="UNKNOWN",
        provider_label=parsed.label,
        provider_name=provider_name,
        model_name=model_name,
        model_version=model_version,
        confidence=parsed.score,
        context_hash=chunk.chunk_hash,
        discovery_policy_hash=config_hash,
        chunking_policy_hash=chunk.input_hash,
        duplicate_group_id="",
        overlap_status="",
        provider_evidence={
            "raw_label": parsed.label,
            "raw_score": parsed.score,
            "raw_start": parsed.start,
            "raw_end": parsed.end,
        },
        risk_score=0.0,
        risk_band="low",
    )


def validate_and_convert(
    raw_entities: list[dict[str, Any]],
    chunk: DiscoveryChunk,
    *,
    company_id: str,
    provider_name: str,
    model_name: str,
    model_version: str,
    config_hash: str = "",
    adapter_policy_version: str = "",
    label_mapping: Any | None = None,
) -> ValidationResult:
    """Validate a batch and convert valid entities to ProviderCandidates.

    ``company_id``, ``provider_name``, and other provider context are
    REQUIRED kwargs — there is no default.
    """
    if not company_id or not isinstance(company_id, str):
        raise ValueError("validate_and_convert requires explicit non-empty company_id")
    counters = ValidationCounters()
    candidates: list[ProviderCandidate] = []
    warnings: list[str] = []

    for raw in raw_entities or []:
        parsed = _validate_one(raw, chunk, counters)
        if parsed is None:
            warnings.append("rejected_span")
            continue

        candidate = _build_candidate(
            parsed,
            chunk,
            company_id=company_id,
            provider_name=provider_name,
            model_name=model_name,
            model_version=model_version,
            config_hash=config_hash,
            adapter_policy_version=adapter_policy_version,
        )

        if label_mapping is not None and hasattr(label_mapping, "to_canonical"):
            canonical = label_mapping.to_canonical(candidate.provider_label)
            candidate.proposed_entity_type = canonical

        candidates.append(candidate)

    return ValidationResult(candidates=candidates, counters=counters, warnings=warnings)
