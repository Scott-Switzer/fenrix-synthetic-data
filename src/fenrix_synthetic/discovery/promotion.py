from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from .review import ReviewQueue
from .schemas import AmendmentProposal, ProviderCandidate


@dataclass
class PromotionResult:
    success: bool
    proposal_id: str
    previous_registry_hash: str
    new_registry_hash: str
    promotion_id: str
    candidates_promoted: list[str]
    errors: list[str] = field(default_factory=list)


@dataclass
class RegistryConflict:
    conflict_type: str
    existing_value: str
    proposed_value: str
    description: str


class ProposalConflictError(Exception):
    def __init__(self, conflicts: list[RegistryConflict]) -> None:
        self.conflicts = conflicts
        super().__init__(f"Proposal has {len(conflicts)} conflict(s)")


def create_proposals_from_reviews(
    queue: ReviewQueue,
    candidates: dict[str, ProviderCandidate],
    source_document_refs: list[str],
) -> list[AmendmentProposal]:
    proposals: list[AmendmentProposal] = []
    accepted_reviews = [r for r in queue.all_reviews() if r.review_status == "accepted"]
    by_type: dict[str, list[Any]] = {}

    for review in accepted_reviews:
        key = f"{review.entity_type}:{review.canonical_value}"
        by_type.setdefault(key, []).append(review)

    for _group_key, group_reviews in by_type.items():
        first = group_reviews[0]
        proposal_id = f"prop-{uuid.uuid4().hex[:8]}"
        candidate_ids = [r.candidate_id for r in group_reviews]

        proposal = AmendmentProposal(
            proposal_id=proposal_id,
            candidate_ids=candidate_ids,
            evidence_refs=candidate_ids,
            proposed_entity_type=first.entity_type or "UNKNOWN",
            proposed_canonical_entity=first.canonical_value or "",
            proposed_aliases=[first.alias_value] if first.alias_value else [],
            match_policy=first.match_policy or "LITERAL",
            boundary_policy="exact",
            case_policy="preserve",
            mutation_policies=["add_alias"],
            pseudonym_class="DISCOVERED",
            reviewer_decision="accept",
            reviewer_reason=first.reviewer_reason,
            review_timestamp=first.timestamp,
            source_document_refs=source_document_refs,
            conflict_analysis={},
        )
        proposals.append(proposal)

    return proposals


def validate_proposal(
    proposal: AmendmentProposal,
    existing_entity_ids: set[str],
    existing_alias_ids: set[str],
    existing_canonical_values: set[str],
    existing_alias_values: set[str],
) -> list[RegistryConflict]:
    conflicts: list[RegistryConflict] = []

    proposed_canonical = proposal.proposed_canonical_entity.upper().strip()
    if proposed_canonical in existing_canonical_values:
        conflicts.append(
            RegistryConflict(
                conflict_type="canonical_entity_exists",
                existing_value=proposed_canonical,
                proposed_value=proposal.proposed_canonical_entity,
                description=f"Canonical entity '{proposal.proposed_canonical_entity}' already exists in registry",
            )
        )

    for alias in proposal.proposed_aliases:
        alias_upper = alias.upper().strip()
        if alias_upper in existing_alias_values:
            conflicts.append(
                RegistryConflict(
                    conflict_type="alias_value_exists",
                    existing_value=alias_upper,
                    proposed_value=alias,
                    description=f"Alias value '{alias}' already exists in registry",
                )
            )

    return conflicts


def compute_registry_hash(entities: list[dict], aliases: list[dict]) -> str:
    content = (
        f"entities={sorted(str(e) for e in entities)}:aliases={sorted(str(a) for a in aliases)}"
    )
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def promote_proposal(
    proposal: AmendmentProposal,
    current_registry: dict,
    temporary: bool = False,
) -> tuple[dict, PromotionResult]:
    reg_entities = list(current_registry.get("entities", []))
    reg_aliases = list(current_registry.get("aliases", []))
    reg_meta = current_registry.get("registry", {})

    prev_hash = compute_registry_hash(reg_entities, reg_aliases)

    entity_id = f"ent-discovered-{uuid.uuid4().hex[:8]}"
    # alias IDs are generated per-alias in the loop below

    if proposal.proposed_entity_type:
        reg_entities.append(
            {
                "entity_id": entity_id,
                "entity_type": proposal.proposed_entity_type,
                "canonical_private_value": proposal.proposed_canonical_entity,
                "source_references": proposal.source_document_refs,
                "discovered_via": "review_queue",
                "proposal_id": proposal.proposal_id,
            }
        )

    for alias_val in proposal.proposed_aliases:
        reg_aliases.append(
            {
                "alias_id": f"ali-discovered-{uuid.uuid4().hex[:8]}",
                "canonical_entity_id": entity_id,
                "private_alias_value": alias_val,
                "entity_type": proposal.proposed_entity_type,
                "match_policy": proposal.match_policy,
                "priority": 200,
                "discovered_via": "review_queue",
                "proposal_id": proposal.proposal_id,
            }
        )

    if temporary:
        for ent in reg_entities:
            ent["registry_scope"] = "temporary"
        for als in reg_aliases:
            als["registry_scope"] = "temporary"

    new_hash = compute_registry_hash(reg_entities, reg_aliases)

    result_registry = dict(current_registry)
    result_registry["entities"] = reg_entities
    result_registry["aliases"] = reg_aliases
    result_registry["registry"] = dict(reg_meta)
    result_registry["registry"]["registry_id"] = reg_meta.get("registry_id", "reg-temp")
    result_registry["registry"]["last_promotion_hash"] = new_hash

    result = PromotionResult(
        success=True,
        proposal_id=proposal.proposal_id,
        previous_registry_hash=prev_hash,
        new_registry_hash=new_hash,
        promotion_id=f"promo-{uuid.uuid4().hex[:8]}",
        candidates_promoted=proposal.candidate_ids,
    )

    return result_registry, result
