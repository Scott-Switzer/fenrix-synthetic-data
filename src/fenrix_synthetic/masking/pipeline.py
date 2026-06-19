from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from ..identity import EntityRegistry
from ..identity.schemas import MatchPolicy, MutationPolicy
from .deterministic import (
    MatchEntry,
    get_patterns_for_alias,
)
from .overlap import OverlapResolver
from .reconstruction import DocumentReconstructor
from .sanitizer import compute_text_hash, sanitize_metadata
from .schemas import ConflictStatus, MaskingAudit, MaskingSummary, MatchResult


class DeterministicMasker:
    def __init__(
        self,
        entity_registry: EntityRegistry,
        document_artifact_id: str = "",
    ):
        self._registry = entity_registry
        self._document_artifact_id = document_artifact_id
        self._resolver = OverlapResolver()
        self._reconstructor = DocumentReconstructor()

    def mask(
        self,
        text: str,
        config_hash: str = "",
    ) -> tuple[str, MaskingAudit, MaskingSummary]:
        from .deterministic import normalize_text

        registry = self._registry

        # Detect already-inserted placeholders to avoid mutating them
        placeholder_spans = [
            (m.start(), m.end())
            for m in re.finditer(r"\[[A-Za-z0-9_\s]+\]", text)
        ]

        # Normalized text for whitespace-normalized matching policies
        normalized_text = normalize_text(text)

        # Phase 1: Discover all possible matches
        matches: list[MatchEntry] = []
        for alias in registry.all_aliases():
            if not alias.active:
                continue
            patterns = get_patterns_for_alias(alias, registry)
            # Use normalized text when policy or mutation requires it
            target_text = normalized_text if (
                alias.match_policy == MatchPolicy.WHITESPACE_VARIANT
                or MutationPolicy.WHITESPACE_NORMALIZE in alias.enabled_mutation_policies
            ) else text
            for ptype, pattern, replacement, priority, flags in patterns:
                for regex_match in re.finditer(pattern, target_text, flags=flags):
                    start = regex_match.start()
                    end = regex_match.end()

                    # Do not match inside existing placeholders
                    if any(
                        ps[0] <= start < ps[1] or ps[0] < end <= ps[1]
                        for ps in placeholder_spans
                    ):
                        continue

                    matched = regex_match.group()
                    span_id = f"span-{ptype}-{len(matches):06d}"
                    entry = MatchEntry(
                        span_id=span_id,
                        document_artifact_id=self._document_artifact_id,
                        original_start=start,
                        original_end=end,
                        entity_id=alias.canonical_entity_id,
                        alias_id=alias.alias_id,
                        entity_type=alias.entity_type.value,
                        match_policy=ptype,
                        priority=priority,
                        matched_text=matched,
                        replacement=replacement,
                    )
                    matches.append(entry)

        # Phase 2: Resolve overlaps
        accepted, rejected = self._resolver.resolve(matches)

        # Phase 3: Build audit
        audit_id = f"audit-{self._document_artifact_id}-{datetime.now(UTC).isoformat()}"
        overlap_count = len(rejected)
        shadowed = [m for m in rejected if m not in accepted]
        audit = MaskingAudit(
            audit_id=audit_id,
            company_id=registry.metadata.company_id,
            document_artifact_id=self._document_artifact_id,
            source_bronze_artifact_id=self._document_artifact_id,
            registry_id=registry.metadata.registry_id,
            masking_policy_hash=config_hash,
            total_matches=len(matches),
            accepted_count=len(accepted),
            rejected_count=len(rejected),
            shadowed_count=len(shadowed),
            overlap_count=overlap_count,
            spans=[
                MatchResult(
                    span_id=m.span_id,
                    document_artifact_id=m.document_artifact_id,
                    original_start=m.original_start,
                    original_end=m.original_end,
                    entity_id=m.entity_id,
                    alias_id=m.alias_id,
                    entity_type=m.entity_type,
                    match_policy=m.match_policy,
                    priority=m.priority,
                    matched_text_hash=m.matched_text_hash,
                    replacement=m.replacement,
                    conflict_status=ConflictStatus.ACCEPTED
                    if m in accepted
                    else ConflictStatus.REJECTED,
                )
                for m in matches
            ],
        )

        # Phase 4: Apply replacements
        masked_text = self._reconstructor.apply_replacements(text, accepted)

        # Phase 5: Build summary
        input_hash = hashlib.sha256(text.encode()).hexdigest()
        output_hash = compute_text_hash(masked_text)
        summary = MaskingSummary(
            company_id=registry.metadata.company_id,
            document_artifact_id=self._document_artifact_id,
            input_artifact_id=self._document_artifact_id,
            input_hash=input_hash,
            output_hash=output_hash,
            registry_hash=registry.config_hash(),
            masking_policy_hash=config_hash,
            pseudonym_policy_version=registry.metadata.pseudonym_policy_version,
            match_count=len(matches),
            replacement_count=len(accepted),
            overlap_count=overlap_count,
        )

        return masked_text, audit, summary

    def mask_and_sanitize_metadata(
        self,
        text: str,
        metadata: dict[str, Any],
        config_hash: str = "",
    ) -> tuple[str, dict[str, Any], MaskingAudit, MaskingSummary]:
        masked_text, audit, summary = self.mask(text, config_hash)

        registry_values: set[str] = set()
        for entity in self._registry.all_entities():
            registry_values.add(entity.canonical_private_value)
        for alias in self._registry.all_aliases():
            registry_values.add(alias.private_alias_value)

        sanitized_meta = sanitize_metadata(
            metadata,
            registry_values,
            skip_keys={"artifact_id", "company_id"},
        )

        return masked_text, sanitized_meta, audit, summary
