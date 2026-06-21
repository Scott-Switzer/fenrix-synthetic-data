"""Atlas completeness validation (Phase 4R)."""

from __future__ import annotations

from .schemas import AtlasCategory, IdentityAtlas, IdentitySubType

# Required sub-types per category for a complete atlas
_REQUIRED_BY_CATEGORY: dict[AtlasCategory, set[IdentitySubType]] = {
    AtlasCategory.ISSUER: {
        IdentitySubType.LEGAL_NAME,
        IdentitySubType.TICKER,
    },
    AtlasCategory.PEOPLE: {
        IdentitySubType.EXECUTIVE,
    },
    AtlasCategory.ORGANIZATIONS: {
        IdentitySubType.AUDITOR,
    },
    AtlasCategory.PRODUCTS: set(),
    AtlasCategory.LOCATIONS: {
        IdentitySubType.HEADQUARTERS,
    },
    AtlasCategory.DIGITAL: {
        IdentitySubType.DOMAIN,
    },
    AtlasCategory.SEMANTIC_FINGERPRINTS: set(),
}

# Recommended sub-types (warn if missing)
_RECOMMENDED_BY_CATEGORY: dict[AtlasCategory, set[IdentitySubType]] = {
    AtlasCategory.ISSUER: {
        IdentitySubType.FORMER_NAME,
        IdentitySubType.CIK,
        IdentitySubType.LEI,
    },
    AtlasCategory.PEOPLE: {
        IdentitySubType.DIRECTOR,
        IdentitySubType.FOUNDER,
    },
    AtlasCategory.ORGANIZATIONS: {
        IdentitySubType.SUBSIDIARY,
    },
    AtlasCategory.PRODUCTS: {
        IdentitySubType.BRAND,
        IdentitySubType.PRODUCT_NAME,
    },
    AtlasCategory.LOCATIONS: {
        IdentitySubType.OFFICE,
    },
    AtlasCategory.DIGITAL: {
        IdentitySubType.EMAIL_DOMAIN,
        IdentitySubType.WEBSITE,
    },
    AtlasCategory.SEMANTIC_FINGERPRINTS: {
        IdentitySubType.DISTINCTIVE_EVENT,
    },
}


def validate_atlas_completeness(atlas: IdentityAtlas) -> tuple[bool, list[str], dict[str, float]]:
    """Validate atlas completeness by category.

    Returns (is_minimally_complete, warnings, scores_by_category).

    An empty atlas is rejected for real pilots. Synthetic test fixtures
    must be explicitly marked.
    """
    warnings: list[str] = []
    scores: dict[str, float] = {}

    if not atlas.entries or len(atlas.get_all_active()) == 0:
        warnings.append(
            "Identity atlas has zero entries. This is rejected for real pilots. "
            "Empty atlases are only allowed for explicitly marked synthetic test fixtures."
        )
        return False, warnings, scores

    # Check minimum required
    present_by_category: dict[AtlasCategory, set[IdentitySubType]] = {}
    for entry in atlas.get_all_active():
        present_by_category.setdefault(entry.category, set()).add(entry.sub_type)

    all_minimal = True
    for cat in AtlasCategory:
        required = _REQUIRED_BY_CATEGORY.get(cat, set())
        recommended = _RECOMMENDED_BY_CATEGORY.get(cat, set())
        present = present_by_category.get(cat, set())

        missing_required = required - present
        missing_recommended = recommended - present

        if missing_required:
            all_minimal = False
            warnings.append(
                f"Category {cat.value}: missing required sub-types: "
                f"{[s.value for s in sorted(missing_required)]}"
            )

        if missing_recommended:
            warnings.append(
                f"Category {cat.value}: missing recommended sub-types: "
                f"{[s.value for s in sorted(missing_recommended)]}"
            )

        # Score: fraction of (required + recommended) that are present
        total = required | recommended
        scores[cat.value] = len(present & total) / max(1, len(total))

    return all_minimal, warnings, scores


def atlas_completeness_ok(atlas: IdentityAtlas) -> bool:
    """Quick check: is the atlas complete enough for a real pilot?"""
    ok, _, _ = validate_atlas_completeness(atlas)
    return ok
