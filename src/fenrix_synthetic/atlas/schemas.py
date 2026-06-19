"""Identity atlas schemas for Phase 4 anonymity pilot.

Extends the existing identity registry (EntityType, MatchPolicy, Alias,
CanonicalEntity, etc.) with the full atlas schema supporting:

- issuer: legal names, former names, aliases, abbreviations, tickers, CIK, EIN, LEI
- people: executives, directors, founders, spokespersons
- organizations: subsidiaries, auditors, regulators, counterparties
- products: brands, product names, service names, program names
- locations: headquarters, offices, branches, cities, states, countries
- digital: websites, domains, email domains, social handles, phone numbers
- semantic_fingerprints: acquisitions, legal proceedings, distinctive events

Uses typed pseudonyms: [ISSUER], [EXECUTIVE_01], [SUBSIDIARY_01], etc.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class AtlasCategory(StrEnum):
    """Top-level identity atlas category."""

    ISSUER = "issuer"
    PEOPLE = "people"
    ORGANIZATIONS = "organizations"
    PRODUCTS = "products"
    LOCATIONS = "locations"
    DIGITAL = "digital"
    SEMANTIC_FINGERPRINTS = "semantic_fingerprints"


class IdentitySubType(StrEnum):
    """Sub-types within atlas categories."""

    # Issuer
    LEGAL_NAME = "legal_name"
    FORMER_NAME = "former_name"
    ALIAS = "alias"
    ABBREVIATION = "abbreviation"
    TICKER = "ticker"
    CIK = "cik"
    EIN = "ein"
    LEI = "lei"
    EXCHANGE_IDENTIFIER = "exchange_identifier"

    # People
    EXECUTIVE = "executive"
    DIRECTOR = "director"
    FOUNDER = "founder"
    SPOKESPERSON = "spokesperson"

    # Organizations
    SUBSIDIARY = "subsidiary"
    ACQUIRED_COMPANY = "acquired_company"
    AUDITOR = "auditor"
    TRANSFER_AGENT = "transfer_agent"
    REGULATOR = "regulator"
    COUNTERPARTY = "counterparty"

    # Products
    BRAND = "brand"
    PRODUCT_NAME = "product_name"
    SERVICE_NAME = "service_name"
    PROGRAM_NAME = "program_name"

    # Locations
    HEADQUARTERS = "headquarters"
    OFFICE = "office"
    BRANCH = "branch"
    CITY = "city"
    STATE = "state"
    COUNTRY = "country"

    # Digital
    WEBSITE = "website"
    DOMAIN = "domain"
    EMAIL_DOMAIN = "email_domain"
    SOCIAL_HANDLE = "social_handle"
    PHONE_NUMBER = "phone_number"

    # Semantic fingerprints
    ACQUISITION = "acquisition"
    LEGAL_PROCEEDING = "legal_proceeding"
    DISTINCTIVE_EVENT = "distinctive_event"
    UNIQUE_SEGMENT_NAME = "unique_segment_name"
    SLOGAN = "slogan"
    NAMED_PARTNERSHIP = "named_partnership"
    UNIQUE_OPERATIONAL_FACT = "unique_operational_fact"


class MatchPolicy(StrEnum):
    """Extended match policies for the atlas."""

    EXACT = "exact"
    NORMALIZED = "normalized"
    CASE_INSENSITIVE = "case_insensitive"
    FUZZY = "fuzzy"
    POSSESSIVE = "possessive"
    PUNCTUATION_VARIANT = "punctuation_variant"
    WHITESPACE_VARIANT = "whitespace_variant"
    ABBREVIATION = "abbreviation"
    DOMAIN = "domain"
    URL = "url"
    PHONE = "phone"
    REGEX = "regex"


class CasePolicy(StrEnum):
    PRESERVE = "preserve"
    LOWER = "lower"
    UPPER = "upper"


class IdentityEntry(BaseModel):
    """A single identity entry in the atlas."""

    entry_id: str
    category: AtlasCategory
    sub_type: IdentitySubType
    private_value: str  # The actual private value (never in tracked files)
    normalized_value: str = ""  # Normalized form for matching
    assigned_pseudonym: str = ""  # Typed placeholder, e.g. [ISSUER], [EXECUTIVE_01]
    match_policy: MatchPolicy = MatchPolicy.EXACT
    case_policy: CasePolicy = CasePolicy.LOWER
    priority: int = 100
    reason: str = ""  # Why this identity was added (provenance)
    source_reference: str = ""  # Where this value was sourced from
    reviewer_id: str = ""  # Who approved this entry
    review_date: datetime | None = None
    active: bool = True
    private_notes: str = ""

    @field_validator("entry_id")
    @classmethod
    def entry_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("entry_id must not be empty")
        return v.strip()

    @field_validator("private_value")
    @classmethod
    def value_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("private_value must not be empty")
        return v.strip()


class IdentityAtlas(BaseModel):
    """Versioned identity atlas for a source company.

    Contains all known identifiers, aliases, and semantic fingerprints
    needed to deterministically mask a company's identity from its data.
    """

    atlas_id: str
    schema_version: str = "1.0.0"
    company_id: str  # SRC_001 (never the real company name)
    pseudonym_policy_version: str = "1.0.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    entries: list[IdentityEntry] = []

    # Metadata
    private_classification: str = "private"
    source_manifest_ids: list[str] = []
    source_manifest_hashes: list[str] = []
    atlas_config_hash: str = ""

    @field_validator("entries")
    @classmethod
    def no_duplicate_entry_ids(cls, v: list[IdentityEntry]) -> list[IdentityEntry]:
        ids = [e.entry_id for e in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate entry_id values in atlas")
        return v

    def get_entries_by_category(self, category: AtlasCategory) -> list[IdentityEntry]:
        return [e for e in self.entries if e.category == category and e.active]

    def get_entries_by_sub_type(self, sub_type: IdentitySubType) -> list[IdentityEntry]:
        return [e for e in self.entries if e.sub_type == sub_type and e.active]

    def get_all_active(self) -> list[IdentityEntry]:
        return [e for e in self.entries if e.active]

    def config_hash(self) -> str:
        """Compute a deterministic hash of the atlas configuration."""
        import hashlib

        import orjson

        sorted_entries = sorted(self.entries, key=lambda e: e.entry_id)
        data = {
            "atlas_id": self.atlas_id,
            "schema_version": self.schema_version,
            "company_id": self.company_id,
            "pseudonym_policy_version": self.pseudonym_policy_version,
            "entries": [
                {
                    "entry_id": e.entry_id,
                    "category": e.category.value,
                    "sub_type": e.sub_type.value,
                    "normalized_value": e.normalized_value,
                    "assigned_pseudonym": e.assigned_pseudonym,
                    "match_policy": e.match_policy.value,
                    "priority": e.priority,
                    "active": e.active,
                }
                for e in sorted_entries
            ],
        }
        raw = orjson.dumps(data, option=orjson.OPT_SORT_KEYS)
        return hashlib.sha256(raw).hexdigest()
