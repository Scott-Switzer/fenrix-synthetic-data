"""Validation and offset reconciliation between GLiNER output and Fenrix provider candidates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from ...schemas import DiscoveryChunk, ProviderCandidate


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
    quarantine_samples: list[dict[str, Any]] = field(default_factory=list)

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
            "quarantine_count": len(self.quarantine_samples),
        }


@dataclass
class ValidationResult:
    candidates: list[ProviderCandidate]
    counters: ValidationCounters
    warnings: list[str]


def validate_entity(
    raw: dict[str, Any],
    chunk: DiscoveryChunk,
    counters: ValidationCounters,
) -> tuple[ProviderCandidate | None, str | None]:
    """Validate one raw GLiNER entity dict against the chunk.

    Returns (ProviderCandidate, None) on success or (None, reject_reason) on
    rejection. Rejection reasons are appended to counters and returned so the
    caller can collect them as warnings.
    """
    counters.total_received += 1

    required = ("text", "label", "start", "end", "score")
    if not all(k in raw for k in required):
        counters.rejected_missing_fields += 1
        counters.quarantine_samples.append({"reason": "missing_fields", "raw": dict(raw)})
        return None, "missing required fields"

    label = raw["label"]
    if not label or not isinstance(label, str):
        counters.rejected_missing_label += 1
        counters.quarantine_samples.append({"reason": "missing_label", "raw": dict(raw)})
        return None, "missing or non-string label"

    try:
        start = int(raw["start"])
        end = int(raw["end"])
    except (TypeError, ValueError):
        counters.rejected_invalid_offsets += 1
        counters.quarantine_samples.append({"reason": "non_integer_offsets", "raw": dict(raw)})
        return None, "non-integer offset"

    if start < 0 or end <= start:
        counters.rejected_invalid_offsets += 1
        counters.quarantine_samples.append({"reason": "invalid_offsets", "raw": dict(raw)})
        return None, f"invalid offsets start={start} end={end}"

    if end > len(chunk.text):
        counters.rejected_out_of_range += 1
        counters.quarantine_samples.append({"reason": "out_of_range", "raw": dict(raw)})
        return None, f"end={end} exceeds chunk length {len(chunk.text)}"

    score = raw["score"]
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        counters.rejected_non_numeric_score += 1
        counters.quarantine_samples.append({"reason": "non_numeric_score", "raw": dict(raw)})
        return None, "non-numeric score"
    score_f = float(score)
    if not 0.0 <= score_f <= 1.0:
        counters.rejected_score_out_of_range += 1
        counters.quarantine_samples.append({"reason": "score_out_of_range", "raw": dict(raw)})
        return None, f"score {score_f} outside [0,1]"

    chunk_slice = chunk.text[start:end]
    expected_text = raw["text"]
    if not isinstance(expected_text, str):
        counters.rejected_text_mismatch += 1
        counters.quarantine_samples.append({"reason": "non_string_text", "raw": dict(raw)})
        return None, "non-string matched text"
    if chunk_slice != expected_text:
        counters.rejected_text_mismatch += 1
        counters.quarantine_samples.append(
            {
                "reason": "text_mismatch",
                "raw": dict(raw),
                "expected": chunk_slice,
            }
        )
        return None, "matched text does not equal chunk[start:end]"

    return _build_candidate(raw, chunk, start, end, label, score_f), None


def _build_candidate(
    raw: dict[str, Any],
    chunk: DiscoveryChunk,
    start: int,
    end: int,
    label: str,
    score: float,
) -> ProviderCandidate:
    candidate_id = f"gliner-{uuid.uuid4().hex[:8]}"
    matched_text = raw["text"]
    original_start = chunk.start_offset + start
    original_end = chunk.start_offset + end
    return ProviderCandidate(
        candidate_id=candidate_id,
        company_id="C001",
        document_artifact_id=chunk.document_artifact_id,
        chunk_ids=[chunk.chunk_id],
        original_start=original_start,
        original_end=original_end,
        private_matched_text=matched_text,
        proposed_entity_type="UNKNOWN",
        provider_label=label,
        provider_name="gliner_local",
        model_name="",
        model_version="",
        confidence=score,
        context_hash=chunk.chunk_hash,
        discovery_policy_hash="",
        chunking_policy_hash=chunk.input_hash,
        duplicate_group_id="",
        overlap_status="",
        provider_evidence={
            "raw_label": label,
            "raw_score": score,
            "raw_start": start,
            "raw_end": end,
        },
        risk_score=0.0,
        risk_band="low",
    )


def validate_and_convert(
    raw_entities: list[dict[str, Any]],
    chunk: DiscoveryChunk,
    company_id: str,
    provider_name: str,
    model_name: str,
    model_version: str,
    label_mapping: Any | None = None,
    config_hash: str = "",
) -> ValidationResult:
    """Validate a batch and convert valid entities to ProviderCandidates.

    Label mapping can be applied after validation if passed.
    Unknown labels remain as 'UNKNOWN' in `proposed_entity_type` and the
    raw label is retained in `provider_label` and `provider_evidence`.
    """
    counters = ValidationCounters()
    candidates: list[ProviderCandidate] = []
    warnings: list[str] = []

    for raw in raw_entities or []:
        candidate, reason = validate_entity(raw, chunk, counters)
        if candidate is None:
            if reason is not None:
                warnings.append(reason)
            continue

        candidate.company_id = company_id
        candidate.provider_name = provider_name
        candidate.model_name = model_name
        candidate.model_version = model_version
        candidate.discovery_policy_hash = config_hash

        if label_mapping is not None and hasattr(label_mapping, "to_canonical"):
            canonical = label_mapping.to_canonical(candidate.provider_label)
            candidate.proposed_entity_type = canonical

        candidates.append(candidate)
        counters.accepted += 1

    return ValidationResult(candidates=candidates, counters=counters, warnings=warnings)
