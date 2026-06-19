from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class EntityType(StrEnum):
    COMPANY = "company"
    FORMER_COMPANY_NAME = "former_company_name"
    TICKER = "ticker"
    CIK = "cik"
    SEC_ACCESSION_NUMBER = "sec_accession_number"
    SEC_PRIMARY_DOCUMENT = "sec_primary_document"
    COMPANY_DOMAIN = "company_domain"
    COMPANY_EMAIL_DOMAIN = "company_email_domain"
    EXECUTIVE = "executive"
    BOARD_MEMBER = "board_member"
    SUBSIDIARY = "subsidiary"
    BUSINESS_SEGMENT = "business_segment"
    PRODUCT = "product"
    BRAND = "brand"
    PROPRIETARY_PLATFORM = "proprietary_platform"
    FACILITY = "facility"
    HEADQUARTERS = "headquarters"
    ACQUISITION_TARGET = "acquisition_target"
    JOINT_VENTURE = "joint_venture"
    AUDITOR = "auditor"
    LAW_FIRM = "law_firm"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    COMPETITOR = "competitor"
    REGULATOR = "regulator"


class RegistryStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class MatchPolicy(StrEnum):
    LITERAL = "literal"
    CASE_INSENSITIVE = "case_insensitive"
    TICKER_EXACT = "ticker_exact"
    TICKER_WITH_EXCHANGE = "ticker_with_exchange"
    TICKER_PARENTHESIZED = "ticker_parenthesized"
    CIK_PADDED = "cik_padded"
    CIK_CONTEXTUAL = "cik_contextual"
    ACCESSION_DASHED = "accession_dashed"
    ACCESSION_URL_FORM = "accession_url_form"
    DOMAIN_FULL = "domain_full"
    DOMAIN_EMAIL = "domain_email"
    URL_FULL = "url_full"
    POSSESSIVE = "possessive"
    PUNCTUATION_VARIANT = "punctuation_variant"
    WHITESPACE_VARIANT = "whitespace_variant"
    CANARY = "canary"


class CasePolicy(StrEnum):
    PRESERVE = "preserve"
    CASE_INSENSITIVE = "case_insensitive"
    UPPER = "upper"
    LOWER = "lower"
    TITLE = "title"


class BoundaryPolicy(StrEnum):
    WORD = "word"
    LINE = "line"
    SENTENCE = "sentence"
    NONE = "none"


class MutationPolicy(StrEnum):
    POSSESSIVE = "possessive"
    SMART_APOSTROPHE = "smart_apostrophe"
    DASH_VARIANT = "dash_variant"
    WHITESPACE_NORMALIZE = "whitespace_normalize"
    COMMA_PUNCTUATION = "comma_punctuation"


class RegistryMetadata(BaseModel):
    registry_id: str
    schema_version: str = "1.0.0"
    company_id: str
    source_manifest_ids: list[str] = []
    source_manifest_hashes: list[str] = []
    registry_config_hash: str = ""
    pseudonym_policy_version: str = ""
    private_classification: str = "private"
    status: RegistryStatus = RegistryStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CanonicalEntity(BaseModel):
    entity_id: str
    company_id: str
    entity_type: EntityType
    canonical_private_value: str
    assigned_pseudonym: str = ""
    source_references: list[str] = []
    active: bool = True
    private_notes: str = ""

    @field_validator("entity_id")
    @classmethod
    def entity_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("entity_id must not be empty")
        return v.strip()

    @field_validator("canonical_private_value")
    @classmethod
    def value_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("canonical_private_value must not be empty")
        return v.strip()


class Alias(BaseModel):
    alias_id: str
    canonical_entity_id: str
    private_alias_value: str
    normalized_alias_value: str = ""
    entity_type: EntityType = EntityType.COMPANY
    match_policy: MatchPolicy = MatchPolicy.LITERAL
    case_policy: CasePolicy = CasePolicy.CASE_INSENSITIVE
    boundary_policy: BoundaryPolicy = BoundaryPolicy.WORD
    enabled_mutation_policies: list[MutationPolicy] = []
    priority: int = 100
    source_reference: str = ""
    active: bool = True

    @field_validator("alias_id")
    @classmethod
    def alias_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("alias_id must not be empty")
        return v.strip()

    @field_validator("private_alias_value")
    @classmethod
    def alias_value_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("private_alias_value must not be empty")
        return v.strip()


class IdentityRegistry(BaseModel):
    metadata: RegistryMetadata
    entities: list[CanonicalEntity]
    aliases: list[Alias]

    @field_validator("entities")
    @classmethod
    def no_duplicate_entity_ids(cls, v: list[CanonicalEntity]) -> list[CanonicalEntity]:
        ids = [e.entity_id for e in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate entity_id values")
        return v

    @field_validator("aliases")
    @classmethod
    def no_duplicate_alias_ids(cls, v: list[Alias]) -> list[Alias]:
        ids = [a.alias_id for a in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate alias_id values")
        return v

    def validate_company_id_consistency(self) -> bool:
        return all(e.company_id == self.metadata.company_id for e in self.entities)


class PseudonymPolicy(BaseModel):
    policy_version: str = "1.0.0"
    template: str = "{entity_type} {counter:03d}"
    max_counter: int = 999
    hash_algorithm: str = "sha256"

    def format_pseudonym(self, entity_type: EntityType, counter: int) -> str:
        label = entity_type.value.replace("_", " ").title().replace(" ", "")
        return f"{label} {counter:03d}"
