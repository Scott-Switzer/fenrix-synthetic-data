from __future__ import annotations

import hashlib
import re
import unicodedata

from ..identity import EntityRegistry
from ..identity.schemas import Alias, BoundaryPolicy, MatchPolicy, MutationPolicy


class MatchEntry:
    __slots__ = (
        "span_id",
        "document_artifact_id",
        "original_start",
        "original_end",
        "entity_id",
        "alias_id",
        "entity_type",
        "match_policy",
        "priority",
        "matched_text",
        "matched_text_hash",
        "replacement",
    )

    def __init__(
        self,
        span_id: str,
        document_artifact_id: str,
        original_start: int,
        original_end: int,
        entity_id: str,
        alias_id: str,
        entity_type: str,
        match_policy: str,
        priority: int,
        matched_text: str,
        replacement: str,
    ):
        self.span_id = span_id
        self.document_artifact_id = document_artifact_id
        self.original_start = original_start
        self.original_end = original_end
        self.entity_id = entity_id
        self.alias_id = alias_id
        self.entity_type = entity_type
        self.match_policy = match_policy
        self.priority = priority
        self.matched_text = matched_text
        self.matched_text_hash = hashlib.sha256(matched_text.encode()).hexdigest()
        self.replacement = replacement


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"\s+", " ", text)
    return text


def build_possessive_pattern(value: str) -> str:
    escaped = re.escape(value)
    return f"(?:{escaped})'s\\b|(?:{escaped})'|(?:{escaped})s'\\b"


def build_ticker_exchange_pattern(ticker: str) -> str:
    escaped = re.escape(ticker.upper())
    return f"(?:NYSE|NASDAQ|NYSE\\s*Arca)\\s*:\\s*{escaped}\\b"


def build_ticker_parenthesized_pattern(ticker: str) -> str:
    escaped = re.escape(ticker.upper())
    return f"\\({escaped}\\)"


def build_cik_padded_pattern(cik: str) -> str:
    clean = cik.lstrip("0")
    return f"CIK\\s*#?\\s*0*{re.escape(clean)}\\b|\\b0*{re.escape(clean)}\\b"


def build_accession_dashed_pattern(accession: str) -> str:
    parts = accession.split("-")
    if len(parts) == 3:
        return re.escape(accession)
    if len(parts) == 1 and len(accession) == 18:
        dashed = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
        return re.escape(dashed)
    return re.escape(accession)


def build_domain_url_pattern(domain: str) -> str:
    escaped = re.escape(domain)
    return f"https?://(?:www\\.)?{escaped}[^\\s]*|{escaped}"


def build_email_pattern(domain: str) -> str:
    escaped = re.escape(domain)
    return f"[\\w.+-]+@{escaped}"


def is_unsafe_short_token(value: str) -> bool:
    clean = value.strip().upper()
    if len(clean) <= 2:
        return True
    common_words = {
        "A",
        "AN",
        "THE",
        "AND",
        "OR",
        "BUT",
        "FOR",
        "NOT",
        "IN",
        "ON",
        "AT",
        "TO",
        "BY",
        "OF",
        "WITH",
        "AS",
        "IS",
        "IT",
        "BE",
        "DO",
        "GO",
        "SO",
        "UP",
        "NO",
        "IF",
        "AM",
        "ME",
        "MY",
        "US",
        "WE",
        "HE",
        "SHE",
    }
    if clean in common_words:
        return True
    return False


def get_patterns_for_alias(
    alias: Alias,
    entity_registry: EntityRegistry,
) -> list[tuple[str, str, str, int, int]]:
    """Build regex patterns for an alias.

    Returns list of (ptype, pattern, replacement, priority, flags).
    Flags includes re.IGNORECASE for CASE_INSENSITIVE policy.
    Word boundaries are added for short or common tokens.
    """
    patterns: list[tuple[str, str, str, int, int]] = []
    value = alias.private_alias_value
    entity = entity_registry.get_entity(alias.canonical_entity_id)
    replacement = entity.assigned_pseudonym if entity else "[REDACTED]"
    priority = alias.priority

    match_policy = alias.match_policy
    flags = re.IGNORECASE if match_policy == MatchPolicy.CASE_INSENSITIVE else 0

    # Word-boundary guard for short/common tokens
    needs_boundary = is_unsafe_short_token(value) or alias.boundary_policy == BoundaryPolicy.WORD

    def _bounded(pattern_str: str) -> str:
        if not needs_boundary or pattern_str.startswith(r"\b"):
            return pattern_str
        # Only add \b where the original value has word-char boundaries.
        # Punctuation at start/end makes \b invalid (e.g. "Inc." ends with non-word).
        prefix = r"\b" if value and (value[0].isalnum() or value[0] == "_") else ""
        suffix = r"\b" if value and (value[-1].isalnum() or value[-1] == "_") else ""
        return f"{prefix}{pattern_str}{suffix}"

    if match_policy in (
        MatchPolicy.LITERAL,
        MatchPolicy.CASE_INSENSITIVE,
        MatchPolicy.POSSESSIVE,
        MatchPolicy.PUNCTUATION_VARIANT,
        MatchPolicy.WHITESPACE_VARIANT,
        MatchPolicy.CANARY,
    ):
        patterns.append(("literal", _bounded(re.escape(value)), replacement, priority, flags))

    if match_policy == MatchPolicy.TICKER_EXACT:
        patterns.append(("ticker", _bounded(re.escape(value.upper())), replacement, priority, flags))
        patterns.append(
            ("ticker_exchange", build_ticker_exchange_pattern(value), replacement, priority + 10, flags)
        )
        patterns.append(
            (
                "ticker_parenthesized",
                build_ticker_parenthesized_pattern(value),
                replacement,
                priority + 5,
                flags,
            )
        )

    if match_policy == MatchPolicy.TICKER_WITH_EXCHANGE:
        patterns.append(
            ("ticker_exchange", build_ticker_exchange_pattern(value), replacement, priority, flags)
        )

    if match_policy == MatchPolicy.CIK_PADDED:
        patterns.append(("cik_padded", build_cik_padded_pattern(value), replacement, priority, flags))

    if match_policy == MatchPolicy.ACCESSION_DASHED:
        patterns.append(("accession", build_accession_dashed_pattern(value), replacement, priority, flags))

    if match_policy == MatchPolicy.DOMAIN_FULL:
        patterns.append(("url", build_domain_url_pattern(value), replacement, priority, flags))
        patterns.append(("domain", _bounded(re.escape(value)), replacement, priority + 5, flags))

    if match_policy == MatchPolicy.DOMAIN_EMAIL:
        patterns.append(("email", build_email_pattern(value), replacement, priority, flags))

    if match_policy == MatchPolicy.URL_FULL:
        patterns.append(("url", build_domain_url_pattern(value), replacement, priority, flags))

    if MutationPolicy.POSSESSIVE in alias.enabled_mutation_policies:
        patterns.append(("possessive", build_possessive_pattern(value), replacement, priority + 1, flags))

    if MutationPolicy.DASH_VARIANT in alias.enabled_mutation_policies:
        if " " in value:
            dash_variant = value.replace(" ", "-")
            patterns.append(("dash_variant", _bounded(re.escape(dash_variant)), replacement, priority + 2, flags))
        if "-" in value:
            space_variant = value.replace("-", " ")
            patterns.append(("space_variant", _bounded(re.escape(space_variant)), replacement, priority + 2, flags))

    return patterns
