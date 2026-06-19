from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .schemas import (
    DiscoveryReviewRecord,
    ProviderCandidate,
    ReviewStatus,
)


class ReviewError(Exception):
    pass


class MissingReasonError(ReviewError):
    pass


class InvalidTransitionError(ReviewError):
    pass


_REQUIRED_REASONS: set[str] = {
    ReviewStatus.ACCEPTED.value,
    ReviewStatus.REJECTED.value,
    ReviewStatus.DEFERRED.value,
}


@dataclass
class CandidateReview:
    candidate_id: str
    review_status: str = ReviewStatus.PENDING.value
    reviewer_reason: str = ""
    reviewer_decision: str = ""
    proposal_id: str | None = None
    entity_type: str | None = None
    canonical_value: str | None = None
    alias_value: str | None = None
    match_policy: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class ReviewQueue:
    def __init__(self, company_id: str, document_artifact_id: str) -> None:
        self._company_id = company_id
        self._document_artifact_id = document_artifact_id
        self._reviews: dict[str, CandidateReview] = {}
        self._records: list[DiscoveryReviewRecord] = []

    def add_candidate(self, candidate: ProviderCandidate) -> None:
        if candidate.candidate_id in self._reviews:
            return
        self._reviews[candidate.candidate_id] = CandidateReview(
            candidate_id=candidate.candidate_id,
        )

    def get_review(self, candidate_id: str) -> CandidateReview | None:
        return self._reviews.get(candidate_id)

    def pending_count(self) -> int:
        return sum(
            1 for r in self._reviews.values() if r.review_status == ReviewStatus.PENDING.value
        )

    def accepted_count(self) -> int:
        return sum(
            1 for r in self._reviews.values() if r.review_status == ReviewStatus.ACCEPTED.value
        )

    def rejected_count(self) -> int:
        return sum(
            1 for r in self._reviews.values() if r.review_status == ReviewStatus.REJECTED.value
        )

    def all_reviews(self) -> list[CandidateReview]:
        return list(self._reviews.values())

    def accept(
        self,
        candidate_id: str,
        reviewer_reason: str,
        entity_type: str,
        canonical_value: str,
        alias_value: str,
        match_policy: str = "LITERAL",
    ) -> None:
        if not reviewer_reason or not reviewer_reason.strip():
            raise MissingReasonError("Accept decision requires a reviewer reason")

        if not entity_type or not entity_type.strip():
            raise MissingReasonError("Accept decision requires an entity type")

        review = self._reviews.get(candidate_id)
        if review is None:
            raise ReviewError(f"Candidate {candidate_id} not in review queue")

        prev = review.review_status
        review.review_status = ReviewStatus.ACCEPTED.value
        review.reviewer_reason = reviewer_reason
        review.reviewer_decision = "accept"
        review.entity_type = entity_type
        review.canonical_value = canonical_value
        review.alias_value = alias_value
        review.match_policy = match_policy
        review.timestamp = datetime.now(UTC)

        self._records.append(
            DiscoveryReviewRecord(
                record_id=f"rec-{uuid.uuid4().hex[:8]}",
                candidate_id=candidate_id,
                previous_status=prev,
                new_status=ReviewStatus.ACCEPTED.value,
                reviewer_reason=reviewer_reason,
                proposal_id=None,
            )
        )

    def reject(
        self,
        candidate_id: str,
        reviewer_reason: str,
    ) -> None:
        if not reviewer_reason or not reviewer_reason.strip():
            raise MissingReasonError("Reject decision requires a reviewer reason")

        review = self._reviews.get(candidate_id)
        if review is None:
            raise ReviewError(f"Candidate {candidate_id} not in review queue")

        prev = review.review_status
        review.review_status = ReviewStatus.REJECTED.value
        review.reviewer_reason = reviewer_reason
        review.reviewer_decision = "reject"
        review.timestamp = datetime.now(UTC)

        self._records.append(
            DiscoveryReviewRecord(
                record_id=f"rec-{uuid.uuid4().hex[:8]}",
                candidate_id=candidate_id,
                previous_status=prev,
                new_status=ReviewStatus.REJECTED.value,
                reviewer_reason=reviewer_reason,
                proposal_id=None,
            )
        )

    def defer(
        self,
        candidate_id: str,
        reviewer_reason: str,
    ) -> None:
        if not reviewer_reason or not reviewer_reason.strip():
            raise MissingReasonError("Defer decision requires a reviewer reason")

        review = self._reviews.get(candidate_id)
        if review is None:
            raise ReviewError(f"Candidate {candidate_id} not in review queue")

        prev = review.review_status
        review.review_status = ReviewStatus.DEFERRED.value
        review.reviewer_reason = reviewer_reason
        review.reviewer_decision = "defer"
        review.timestamp = datetime.now(UTC)

        self._records.append(
            DiscoveryReviewRecord(
                record_id=f"rec-{uuid.uuid4().hex[:8]}",
                candidate_id=candidate_id,
                previous_status=prev,
                new_status=ReviewStatus.DEFERRED.value,
                reviewer_reason=reviewer_reason,
                proposal_id=None,
            )
        )

    def mark_duplicate(
        self,
        candidate_id: str,
        reviewer_reason: str,
        proposal_id: str | None = None,
    ) -> None:
        if not reviewer_reason or not reviewer_reason.strip():
            raise MissingReasonError("Duplicate marking requires a reviewer reason")

        review = self._reviews.get(candidate_id)
        if review is None:
            raise ReviewError(f"Candidate {candidate_id} not in review queue")

        prev = review.review_status
        review.review_status = ReviewStatus.DUPLICATE.value
        review.reviewer_reason = reviewer_reason
        review.reviewer_decision = "duplicate"
        review.proposal_id = proposal_id
        review.timestamp = datetime.now(UTC)

        self._records.append(
            DiscoveryReviewRecord(
                record_id=f"rec-{uuid.uuid4().hex[:8]}",
                candidate_id=candidate_id,
                previous_status=prev,
                new_status=ReviewStatus.DUPLICATE.value,
                reviewer_reason=reviewer_reason,
                proposal_id=proposal_id,
            )
        )

    def review_records(self) -> list[DiscoveryReviewRecord]:
        return list(self._records)
