"""Identity atlas compiler.

Compiles a private IdentityAtlas into a deterministic replacement plan
that can be applied by the masking pipeline.

The compiler:
- Assigns typed pseudonyms deterministically
- Preserves coreference across entries
- Supports exact, normalized, case-insensitive, and fuzzy matching
- Records provenance for each compiled entry
- Never exposes private values outside the private boundary
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from .schemas import (
    AtlasCategory,
    IdentityAtlas,
    IdentityEntry,
    IdentitySubType,
    MatchPolicy,
)


class CompiledReplacement:
    """A single compiled replacement rule."""

    __slots__ = (
        "entry_id",
        "category",
        "sub_type",
        "normalized_value",
        "pseudonym",
        "match_policy",
        "priority",
        "pattern",
        "is_blocking",
    )

    def __init__(
        self,
        entry_id: str,
        category: AtlasCategory,
        sub_type: IdentitySubType,
        normalized_value: str,
        pseudonym: str,
        match_policy: MatchPolicy,
        priority: int,
        pattern: str,
        is_blocking: bool = True,
    ):
        self.entry_id = entry_id
        self.category = category
        self.sub_type = sub_type
        self.normalized_value = normalized_value
        self.pseudonym = pseudonym
        self.match_policy = match_policy
        self.priority = priority
        self.pattern = pattern
        self.is_blocking = is_blocking


class ReplacementPlan:
    """A deterministic replacement plan compiled from an identity atlas."""

    def __init__(
        self,
        atlas_id: str,
        company_id: str,
        atlas_hash: str,
        replacements: list[CompiledReplacement] | None = None,
    ):
        self.atlas_id = atlas_id
        self.company_id = company_id
        self.atlas_hash = atlas_hash
        self.replacements: list[CompiledReplacement] = replacements or []
        self._by_category: dict[AtlasCategory, list[CompiledReplacement]] = defaultdict(list)
        self._by_pseudonym: dict[str, CompiledReplacement] = {}
        self._blocking: list[CompiledReplacement] = []
        self._non_blocking: list[CompiledReplacement] = []

        for r in self.replacements:
            self._by_category[r.category].append(r)
            self._by_pseudonym[r.pseudonym] = r
            if r.is_blocking:
                self._blocking.append(r)
            else:
                self._non_blocking.append(r)

    def get_blocking(self) -> list[CompiledReplacement]:
        return self._blocking

    def get_non_blocking(self) -> list[CompiledReplacement]:
        return self._non_blocking

    def get_by_category(self, category: AtlasCategory) -> list[CompiledReplacement]:
        return self._by_category.get(category, [])

    def get_replacement(self, normalized_value: str) -> CompiledReplacement | None:
        for r in self.replacements:
            if r.normalized_value == normalized_value:
                return r
        return None

    def to_dict(self) -> dict:

        data = {
            "atlas_id": self.atlas_id,
            "company_id": self.company_id,
            "atlas_hash": self.atlas_hash,
            "blocking_count": len(self._blocking),
            "non_blocking_count": len(self._non_blocking),
            "total_replacements": len(self.replacements),
            "categories": {cat.value: len(entries) for cat, entries in self._by_category.items()},
        }
        return data


class AtlasCompiler:
    """Compiles an IdentityAtlas into a deterministic ReplacementPlan."""

    def __init__(self, counter_start: int = 1):
        self._counters: dict[AtlasCategory, dict[IdentitySubType, int]] = defaultdict(
            lambda: defaultdict(lambda: counter_start)
        )

    def compile(self, atlas: IdentityAtlas) -> ReplacementPlan:
        """Compile the atlas into a replacement plan.

        Assigns pseudonyms deterministically. Entries are sorted by
        (category, sub_type, entry_id) for deterministic ordering.
        """
        replacements: list[CompiledReplacement] = []

        sorted_entries = sorted(
            atlas.get_all_active(),
            key=lambda e: (e.category.value, e.sub_type.value, e.entry_id),
        )

        for entry in sorted_entries:
            counter = self._counters[entry.category][entry.sub_type]
            pseudonym = self._assign_pseudonym(entry.category, entry.sub_type, counter)
            self._counters[entry.category][entry.sub_type] = counter + 1

            # Store the pseudonym on the entry (for coreference)
            entry.assigned_pseudonym = pseudonym

            pattern = self._build_pattern(entry)
            is_blocking = self._is_blocking(entry.category, entry.sub_type)

            replacements.append(
                CompiledReplacement(
                    entry_id=entry.entry_id,
                    category=entry.category,
                    sub_type=entry.sub_type,
                    normalized_value=entry.normalized_value or entry.private_value.lower().strip(),
                    pseudonym=pseudonym,
                    match_policy=entry.match_policy,
                    priority=entry.priority,
                    pattern=pattern,
                    is_blocking=is_blocking,
                )
            )

        atlas_hash = atlas.config_hash()
        return ReplacementPlan(
            atlas_id=atlas.atlas_id,
            company_id=atlas.company_id,
            atlas_hash=atlas_hash,
            replacements=replacements,
        )

    def _assign_pseudonym(
        self, category: AtlasCategory, sub_type: IdentitySubType, counter: int
    ) -> str:
        """Assign a typed pseudonym deterministically.

        Uses typed placeholders like:
        [ISSUER], [EXECUTIVE_01], [SUBSIDIARY_01], [PRODUCT_LINE_01]
        """
        # Category-level prefix
        prefix_map = {
            AtlasCategory.ISSUER: "ISSUER",
            AtlasCategory.PEOPLE: self._sub_type_label(sub_type),
            AtlasCategory.ORGANIZATIONS: self._sub_type_label(sub_type),
            AtlasCategory.PRODUCTS: "PRODUCT_LINE",
            AtlasCategory.LOCATIONS: "LOCATION",
            AtlasCategory.DIGITAL: "DIGITAL",
            AtlasCategory.SEMANTIC_FINGERPRINTS: "FINGERPRINT",
        }

        prefix = prefix_map.get(category, "ENTITY")

        # For issuer, use a single [ISSUER] without counter (there's only one)
        if category == AtlasCategory.ISSUER and sub_type == IdentitySubType.LEGAL_NAME:
            return "[ISSUER]"

        # For categories with single entries per sub-type, use sub_type label
        if category in (AtlasCategory.LOCATIONS, AtlasCategory.DIGITAL):
            return f"[{prefix}_{counter:02d}]"

        return f"[{prefix}_{counter:02d}]"

    @staticmethod
    def _sub_type_label(sub_type: IdentitySubType) -> str:
        """Map sub-type to a short label for pseudonym generation."""
        label_map = {
            IdentitySubType.EXECUTIVE: "EXECUTIVE",
            IdentitySubType.DIRECTOR: "DIRECTOR",
            IdentitySubType.FOUNDER: "FOUNDER",
            IdentitySubType.SPOKESPERSON: "SPOKESPERSON",
            IdentitySubType.SUBSIDIARY: "SUBSIDIARY",
            IdentitySubType.ACQUIRED_COMPANY: "ACQUIRED",
            IdentitySubType.AUDITOR: "AUDITOR",
            IdentitySubType.TRANSFER_AGENT: "TRANSFER_AGENT",
            IdentitySubType.REGULATOR: "REGULATOR",
            IdentitySubType.COUNTERPARTY: "COUNTERPARTY",
        }
        return label_map.get(sub_type, sub_type.value.upper())

    @staticmethod
    def _build_pattern(entry: IdentityEntry) -> str:
        """Build a regex or literal pattern for the entry."""
        import re

        value = entry.private_value
        escaped = re.escape(value)

        if entry.match_policy == MatchPolicy.EXACT:
            return escaped
        elif entry.match_policy == MatchPolicy.NORMALIZED:
            return escaped
        elif entry.match_policy == MatchPolicy.CASE_INSENSITIVE:
            return f"(?i){escaped}"
        elif entry.match_policy == MatchPolicy.POSSESSIVE:
            return f"(?:{escaped})'s?\\b|{escaped}'"
        elif entry.match_policy == MatchPolicy.PUNCTUATION_VARIANT:
            # Handle hyphen/space variants
            if " " in value:
                dash = re.escape(value.replace(" ", "-"))
                return f"(?:{escaped}|{dash})"
            if "-" in value:
                space = re.escape(value.replace("-", " "))
                return f"(?:{escaped}|{space})"
            return escaped
        elif entry.match_policy == MatchPolicy.URL:
            domain = re.escape(value)
            return f"https?://(?:www\\.)?{domain}[^\\s]*|{domain}"
        elif entry.match_policy == MatchPolicy.DOMAIN:
            return re.escape(value)
        elif entry.match_policy == MatchPolicy.PHONE:
            return re.escape(value)
        elif entry.match_policy == MatchPolicy.REGEX:
            return value  # Raw regex
        else:
            return escaped

    @staticmethod
    def _is_blocking(category: AtlasCategory, sub_type: IdentitySubType) -> bool:
        """Determine if a match on this entry blocks release."""
        blocking_sub_types = {
            IdentitySubType.LEGAL_NAME,
            IdentitySubType.FORMER_NAME,
            IdentitySubType.TICKER,
            IdentitySubType.CIK,
            IdentitySubType.EIN,
            IdentitySubType.LEI,
            IdentitySubType.EXCHANGE_IDENTIFIER,
            IdentitySubType.EXECUTIVE,
            IdentitySubType.DIRECTOR,
            IdentitySubType.FOUNDER,
            IdentitySubType.SUBSIDIARY,
            IdentitySubType.ACQUIRED_COMPANY,
            IdentitySubType.PRODUCT_NAME,
            IdentitySubType.BRAND,
            IdentitySubType.HEADQUARTERS,
            IdentitySubType.DOMAIN,
            IdentitySubType.EMAIL_DOMAIN,
            IdentitySubType.PHONE_NUMBER,
            IdentitySubType.ACQUISITION,
            IdentitySubType.LEGAL_PROCEEDING,
            IdentitySubType.DISTINCTIVE_EVENT,
            IdentitySubType.SLOGAN,
        }
        return sub_type in blocking_sub_types


def compile_atlas(atlas: IdentityAtlas) -> ReplacementPlan:
    """Compile an IdentityAtlas into a ReplacementPlan.

    This is the main entry point for transforming a private atlas
    into a deterministic replacement plan that the masking pipeline
    can apply.
    """
    compiler = AtlasCompiler()
    return compiler.compile(atlas)


def compute_replacement_hash(plan: ReplacementPlan) -> str:
    """Compute a deterministic hash of the replacement plan.

    Used to verify reproducibility across runs.
    """
    import orjson

    data = {
        "atlas_id": plan.atlas_id,
        "company_id": plan.company_id,
        "atlas_hash": plan.atlas_hash,
        "replacements": sorted(
            [
                {
                    "entry_id": r.entry_id,
                    "pseudonym": r.pseudonym,
                    "pattern": r.pattern,
                    "priority": r.priority,
                }
                for r in plan.replacements
            ],
            key=lambda r: str(r["entry_id"]),
        ),
    }
    raw = orjson.dumps(data, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(raw).hexdigest()
