from __future__ import annotations

from .schemas import (
    EntityDiscoveryResponse,
    ProviderCandidate,
    RiskBand,
    SanitizedCandidateSummary,
)

BOUNDARY_TOLERANCE = 5
PROVIDER_AGREEMENT_WEIGHT = 0.3
CONFIDENCE_WEIGHT = 0.25
ENTITY_TYPE_WEIGHT = 0.15
OCCURRENCE_WEIGHT = 0.1
SHAPE_WEIGHT = 0.1
REGISTRY_WEIGHT = 0.1

_RISK_VERSION = "1.0.0"


def compute_risk_score(
    candidate: ProviderCandidate,
    provider_agreement_count: int = 1,
    document_occurrence_count: int = 1,
    already_registered: bool = False,
    deterministic_should_have_caught: bool = False,
) -> tuple[float, str]:
    score = 0.0
    confidence = candidate.confidence
    score += confidence * 0.4
    score += (provider_agreement_count - 1) * 0.1
    score += min(document_occurrence_count * 0.02, 0.15)
    if already_registered:
        score += 0.1
    if deterministic_should_have_caught:
        score += 0.2
    upper = candidate.private_matched_text.upper()
    if _has_ticker_shape(upper) or _has_domain_shape(upper):
        score += 0.15
    if _has_legal_suffix(upper):
        score += 0.1
    if _is_proper_noun(upper, candidate.private_matched_text):
        score += 0.1
    score = max(0.0, min(1.0, score))
    band = _score_to_band(score)
    return (round(score, 3), band)


def _score_to_band(score: float) -> str:
    if score >= 0.75:
        return RiskBand.CRITICAL.value
    if score >= 0.55:
        return RiskBand.HIGH.value
    if score >= 0.35:
        return RiskBand.MEDIUM.value
    return RiskBand.LOW.value


def _has_ticker_shape(text: str) -> bool:
    if len(text) <= 5 and text.isupper() and not text.isalpha():
        return True
    return False


def _has_domain_shape(text: str) -> bool:
    return bool(text.endswith(".COM") or text.endswith(".ORG") or text.endswith(".NET"))


def _has_legal_suffix(text: str) -> bool:
    suffixes = [
        " INC ",
        " LLC ",
        " CORP ",
        " CORPORATION",
        " HOLDINGS",
        " PARTNERS",
        " GROUP",
        " LIMITED",
        " LP",
        " PLC",
    ]
    return any(s in f" {text} " for s in suffixes)


def _is_proper_noun(upper: str, original: str) -> bool:
    if not original:
        return False
    return original[0].isupper() and any(c.islower() for c in original)


class CandidateNormalizer:
    def normalize(
        self,
        candidates: list[ProviderCandidate],
        already_registered_ids: set[str] | None = None,
        deterministic_missed_patterns: list[str] | None = None,
    ) -> list[ProviderCandidate]:
        already_registered_ids = already_registered_ids or set()
        deterministic_missed_patterns = deterministic_missed_patterns or []
        results: list[ProviderCandidate] = []
        for c in candidates:
            if c.review_status == "pending":
                prov_count = 1
                occ_count = 1
                risk, band = compute_risk_score(
                    c,
                    provider_agreement_count=prov_count,
                    document_occurrence_count=occ_count,
                    already_registered=c.entity_id in already_registered_ids
                    if hasattr(c, "entity_id")
                    else False,
                    deterministic_should_have_caught=bool(deterministic_missed_patterns),
                )
                c.risk_score = risk
                c.risk_band = band
            results.append(c)
        return results


class CandidateDeduplicator:
    def deduplicate(
        self,
        candidates: list[ProviderCandidate],
    ) -> tuple[list[ProviderCandidate], dict[str, list[str]]]:
        group_map: dict[str, list[str]] = {}
        selected: list[ProviderCandidate] = []
        seen: dict[str, ProviderCandidate] = {}

        sorted_candidates = sorted(candidates, key=lambda c: (-c.confidence, c.original_start))

        for c in sorted_candidates:
            key = self._span_key(c)
            if key is None:
                selected.append(c)
                continue

            if key not in seen:
                group_map[c.candidate_id] = []
                seen[key] = c
                selected.append(c)
            else:
                existing = seen[key]
                existing_group_id = existing.duplicate_group_id or existing.candidate_id
                c.duplicate_group_id = existing_group_id
                group_map.setdefault(existing_group_id, [])
                if c.candidate_id not in group_map[existing_group_id]:
                    group_map[existing_group_id].append(c.candidate_id)
                existing.duplicate_group_id = existing_group_id
                if c.candidate_id not in group_map[existing_group_id]:
                    group_map[existing_group_id].append(c.candidate_id)

        return selected, group_map

    def _span_key(self, c: ProviderCandidate) -> str | None:
        if c.original_start < 0 or c.original_end <= c.original_start:
            return None
        return f"{c.original_start}:{c.original_end}"


class CandidateDisagreementResolver:
    def resolve(
        self,
        candidates: list[ProviderCandidate],
        group_map: dict[str, list[str]],
    ) -> list[ProviderCandidate]:
        return candidates


def aggregate_provider_candidates(
    responses: list[EntityDiscoveryResponse],
) -> list[ProviderCandidate]:
    all_candidates: list[ProviderCandidate] = []
    for resp in responses:
        all_candidates.extend(resp.provider_candidates)
    return all_candidates


def make_sanitized_summary(
    candidates: list[ProviderCandidate],
    group_map: dict[str, list[str]],
) -> list[SanitizedCandidateSummary]:
    summaries: list[SanitizedCandidateSummary] = []
    for c in candidates:
        summaries.append(
            SanitizedCandidateSummary(
                candidate_id=c.candidate_id,
                matched_text_hash=c.matched_text_hash,
                proposed_entity_type=c.proposed_entity_type,
                provider_name=c.provider_name,
                model_name=c.model_name,
                confidence=c.confidence,
                risk_band=c.risk_band,
                review_status=c.review_status,
                duplicate_group_id=c.duplicate_group_id,
                provider_agreement_count=len(group_map.get(c.duplicate_group_id, [])) + 1,
                document_occurrence_count=1,
            )
        )
    return summaries
