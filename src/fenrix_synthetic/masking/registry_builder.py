"""Build a populated EntityRegistry from a compiled ReplacementPlan.

Converts every CompiledReplacement into appropriate EntityRegistry
entries and aliases, preserving typed pseudonyms, match policies,
and deterministic ordering. Fails closed on malformed or unsupported
entries. Empty plans fail for non-test identifiers.
"""

from __future__ import annotations

from fenrix_synthetic.atlas.compiler import ReplacementPlan
from fenrix_synthetic.atlas.schemas import (
    IdentitySubType,
)
from fenrix_synthetic.atlas.schemas import (
    MatchPolicy as AtlasMatchPolicy,
)
from fenrix_synthetic.identity import EntityType
from fenrix_synthetic.identity import MatchPolicy as RegMatchPolicy
from fenrix_synthetic.identity.entity_registry import EntityRegistry

# ── Mapping: Atlas sub-type → EntityType ──────────────────────────
_ENTITY_TYPE_MAP: dict[IdentitySubType, EntityType] = {
    IdentitySubType.LEGAL_NAME: EntityType.COMPANY,
    IdentitySubType.FORMER_NAME: EntityType.FORMER_COMPANY_NAME,
    IdentitySubType.TICKER: EntityType.TICKER,
    IdentitySubType.CIK: EntityType.CIK,
    IdentitySubType.LEI: EntityType.COMPANY,  # fallback
    IdentitySubType.EXCHANGE_IDENTIFIER: EntityType.TICKER,
    IdentitySubType.EXECUTIVE: EntityType.EXECUTIVE,
    IdentitySubType.DIRECTOR: EntityType.BOARD_MEMBER,
    IdentitySubType.FOUNDER: EntityType.EXECUTIVE,  # fallback
    IdentitySubType.SUBSIDIARY: EntityType.SUBSIDIARY,
    IdentitySubType.ACQUIRED_COMPANY: EntityType.ACQUISITION_TARGET,
    IdentitySubType.AUDITOR: EntityType.AUDITOR,
    IdentitySubType.TRANSFER_AGENT: EntityType.AUDITOR,  # fallback
    IdentitySubType.REGULATOR: EntityType.REGULATOR,
    IdentitySubType.BRAND: EntityType.BRAND,
    IdentitySubType.PRODUCT_NAME: EntityType.PRODUCT,
    IdentitySubType.SERVICE_NAME: EntityType.PRODUCT,
    IdentitySubType.PROGRAM_NAME: EntityType.PROPRIETARY_PLATFORM,
    IdentitySubType.HEADQUARTERS: EntityType.HEADQUARTERS,
    IdentitySubType.OFFICE: EntityType.FACILITY,
    IdentitySubType.BRANCH: EntityType.FACILITY,
    IdentitySubType.DOMAIN: EntityType.COMPANY_DOMAIN,
    IdentitySubType.EMAIL_DOMAIN: EntityType.COMPANY_EMAIL_DOMAIN,
    IdentitySubType.WEBSITE: EntityType.COMPANY_DOMAIN,
}

# ── Mapping: Atlas MatchPolicy → Registry MatchPolicy ────────────
_MATCH_POLICY_MAP: dict[AtlasMatchPolicy, RegMatchPolicy] = {
    AtlasMatchPolicy.EXACT: RegMatchPolicy.LITERAL,
    AtlasMatchPolicy.NORMALIZED: RegMatchPolicy.LITERAL,
    AtlasMatchPolicy.CASE_INSENSITIVE: RegMatchPolicy.CASE_INSENSITIVE,
    AtlasMatchPolicy.FUZZY: RegMatchPolicy.CASE_INSENSITIVE,
    AtlasMatchPolicy.POSSESSIVE: RegMatchPolicy.POSSESSIVE,
    AtlasMatchPolicy.PUNCTUATION_VARIANT: RegMatchPolicy.PUNCTUATION_VARIANT,
    AtlasMatchPolicy.WHITESPACE_VARIANT: RegMatchPolicy.WHITESPACE_VARIANT,
    AtlasMatchPolicy.DOMAIN: RegMatchPolicy.DOMAIN_FULL,
    AtlasMatchPolicy.URL: RegMatchPolicy.URL_FULL,
    AtlasMatchPolicy.ABBREVIATION: RegMatchPolicy.CASE_INSENSITIVE,
}


def register_from_plan(
    plan: ReplacementPlan,
    company_id: str,
    registry_id: str = "",
    *,
    reject_empty: bool = True,
    test_fixture: bool = False,
) -> EntityRegistry:
    """Build a populated EntityRegistry from a compiled ReplacementPlan.

    Args:
        plan: Compiled replacement plan from IdentityAtlas
        company_id: Source identifier (SRC_001)
        registry_id: Unique registry identifier
        reject_empty: If True, raise ValueError for zero entries
        test_fixture: If True, allow empty plans (invented tests only)

    Returns:
        EntityRegistry populated with entities and aliases

    Raises:
        ValueError: Empty plan rejected, or malformed entry
    """
    if not plan.replacements and reject_empty and not test_fixture:
        raise ValueError(
            "ReplacementPlan has zero entries. "
            "Empty plans are rejected for real pilots. "
            "Only invented test fixtures with explicit test_fixture=True may use empty plans."
        )

    reg = EntityRegistry.create(
        company_id=company_id,
        registry_id=registry_id or f"reg-{company_id}-{plan.atlas_hash[:12]}",
        config_hash=plan.atlas_hash,
    )

    # Sort by priority (higher first) then by length (longer first) for
    # deterministic, correct replacement ordering
    sorted_entries = sorted(
        plan.replacements,
        key=lambda r: (-r.priority, -len(r.normalized_value), r.entry_id),
    )

    # Track which entity_ids we've already registered
    entity_ids_registered: set[str] = set()
    alias_counter: dict[str, int] = {}

    for cr in sorted_entries:
        entity_type = _ENTITY_TYPE_MAP.get(cr.sub_type, EntityType.COMPANY)
        match_policy = _MATCH_POLICY_MAP.get(cr.match_policy, RegMatchPolicy.LITERAL)

        # Register the entity if not already registered
        entity_id = f"ent-{cr.entry_id}"
        if entity_id not in entity_ids_registered:
            try:
                reg.add_entity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    canonical_value=cr.normalized_value,
                    source_refs=[cr.entry_id],
                )
                entity_ids_registered.add(entity_id)
                # Override pseudonym with the compiled one
                entity = reg.get_entity(entity_id)
                if entity is not None:
                    entity.assigned_pseudonym = cr.pseudonym
            except ValueError:
                # Entity already exists (shouldn't happen with our tracking)
                pass

        # Register the alias
        alias_counter.setdefault(entity_id, 0)
        alias_counter[entity_id] += 1
        alias_id = f"ali-{cr.entry_id}-{alias_counter[entity_id]:03d}"

        try:
            reg.add_alias(
                alias_id=alias_id,
                entity_id=entity_id,
                alias_value=cr.normalized_value,
                entity_type=entity_type,
                match_policy=match_policy,
                priority=cr.priority,
            )
        except ValueError as exc:
            # Log and continue; malformed entries fail closed
            raise ValueError(
                f"Failed to register alias {alias_id} for entry {cr.entry_id}: {exc}"
            ) from exc

    return reg
