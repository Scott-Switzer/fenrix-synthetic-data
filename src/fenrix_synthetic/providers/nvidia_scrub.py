"""Pre-NVIDIA deterministic scrub and direct-residual precheck.

Runs *before* text is sent to NVIDIA so the attack pass never sees
raw CIK, ticker, domain, or product identifiers.  Reuses the same
regex-pattern builders as the main masking pipeline so there is no
grammar drift between the two.

Architecture
------------
1. ``PreNVIDIAScrubber.scrub(text)`` → ``(scrubbed_text, scan_report)``
   Applies deterministic replacements for every alias registered in
   the ``EntityRegistry`` whose entity type is ``CIK``, ``TICKER``,
   ``COMPANY_DOMAIN``, ``COMPANY_EMAIL_DOMAIN``, ``PRODUCT``, or
   ``BRAND``.  Structural identifiers (CIK, ticker, domain) are
   replaced with ``[REDACTED_XXX]`` tags; names are replaced with
   their assigned pseudonym.

2. ``PreNVIDIAScrubber.precheck(text)`` → ``PrecheckReport``
   Scans *scrubbed* text for any remaining direct identifiers
   using ``ExactResidualScanner``.  If *blocking_hits > 0* the
   precheck fails and NVIDIA should NOT be called.

3. ``PreNVIDIAScrubber.scrub_and_precheck(text)`` → ``(str, PrecheckReport)``
   Convenience that runs scrub then precheck in one call.

Gate rules
----------
* ``PASS``       — zero blocking residuals after scrub.
* ``BLOCKED_PRECHECK`` — one or more blocking residuals remain.
  NVIDIA is skipped; the artifact is marked FAIL.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..attacks.exact_match import (
    ExactResidualScanner,
    ScanResult,
)
from ..identity import EntityRegistry
from ..identity.schemas import (
    Alias,
    EntityType,
)
from ..masking.deterministic import (
    get_patterns_for_alias,
)

logger = logging.getLogger(__name__)

# ── Direct-identifier entity types (must be scrubbed before NVIDIA) ────

_DIRECT_ENTITY_TYPES: frozenset[str] = frozenset(
    {
        EntityType.CIK.value,
        EntityType.TICKER.value,
        EntityType.COMPANY_DOMAIN.value,
        EntityType.COMPANY_EMAIL_DOMAIN.value,
    }
)

_NAME_ENTITY_TYPES: frozenset[str] = frozenset(
    {
        EntityType.COMPANY.value,
        EntityType.FORMER_COMPANY_NAME.value,
        EntityType.PRODUCT.value,
        EntityType.BRAND.value,
        EntityType.SUBSIDIARY.value,
        EntityType.HEADQUARTERS.value,
    }
)

_REDACT_TAGS: dict[str, str] = {
    EntityType.CIK.value: "[CIK_REDACTED]",
    EntityType.TICKER.value: "[TICKER_REDACTED]",
    EntityType.COMPANY_DOMAIN.value: "[DOMAIN_REDACTED]",
    EntityType.COMPANY_EMAIL_DOMAIN.value: "[DOMAIN_REDACTED]",
}

# ── Extra patterns not covered by alias-based matching ─────────────────

# CIK: "central index key" phrasing
_CIK_PHRASE_RE = re.compile(r"\bcentral\s+index\s+key\b", re.IGNORECASE)

# Ticker: "ticker symbol XYZ", "trading symbol XYZ"
_TICKER_SYMBOL_RE = re.compile(
    r"\b(?:ticker|trading)\s+symbol\s*(?::|is)?\s*([A-Z]{1,5})\b", re.IGNORECASE
)

# Ticker: "Nasdaq: NVDA" / "NYSE: NVDA" / "NYSE Arca: NVDA"
_EXCHANGE_TICKER_RE = re.compile(
    r"\b(?:NYSE|NASDAQ|NYSE\s*Arca)\s*:\s*([A-Z]{1,5})\b", re.IGNORECASE
)  # SEC file numbers (333-XXXXXX, 001-XXXXX, etc.)
_SEC_FILE_NUMBER_RE = re.compile(r"\b(?:333|001|002|003|005|811)-\d{5,8}\b")

# Employer ID (XX-XXXXXXX)
_EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")

# Source URLs containing CIK
_CIK_URL_RE = re.compile(r"https?://[^\s]*?(?:cik|CIK)[=/_]?\d{6,10}[^\s]*", re.IGNORECASE)

# SEC archive paths
_SEC_ARCHIVE_PATH_RE = re.compile(r"/Archives/edgar/data/\d{6,10}/", re.IGNORECASE)

# EntityCentralIndexKey XBRL attribute
_ENTITY_CIK_ATTR_RE = re.compile(r'EntityCentralIndexKey[^>]*?"?(\d+)"?', re.IGNORECASE)


@dataclass
class PrecheckReport:
    """Result of a pre-NVIDIA direct-residual scan."""

    passed: bool = True
    status: str = "PRECHECK_PASS"
    total_hits: int = 0
    blocking_hits: int = 0
    hit_types: list[str] = field(default_factory=list)
    hit_summary: str = ""

    @classmethod
    def from_scan_result(cls, result: ScanResult) -> PrecheckReport:
        hit_types = sorted(set(result.hits_by_type.keys()))
        hit_summary_parts: list[str] = []
        for htype in hit_types:
            count = len(result.hits_by_type.get(htype, []))
            hit_summary_parts.append(f"{htype}={count}")
        return cls(
            passed=not result.is_blocked,
            status="PRECHECK_PASS" if not result.is_blocked else "BLOCKED_PRECHECK",
            total_hits=result.total_hits,
            blocking_hits=result.blocking_hits,
            hit_types=hit_types,
            hit_summary=", ".join(hit_summary_parts),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "status": self.status,
            "total_hits": self.total_hits,
            "blocking_hits": self.blocking_hits,
            "hit_types": self.hit_types,
            "hit_summary": self.hit_summary,
        }


# ── Scrubber ────────────────────────────────────────────────────────────


class PreNVIDIAScrubber:
    """Deterministic pre-NVIDIA scrub and precheck.

    Parameters
    ----------
    registry:
        ``EntityRegistry`` populated with all company identity data
        (ticker, CIK, legal names, domains, products, etc.).
        Pass ``None`` to create an empty scrubber that only applies
        hardcoded regex patterns.
    """

    def __init__(self, registry: EntityRegistry | None = None) -> None:
        self._registry = registry
        self._scanner = ExactResidualScanner()
        self._patterns: list[tuple[str, str, str]] = []  # (type, pattern, replacement)
        self._known_values: dict[str, list[str]] = self._build_known_values()
        self._build_patterns()

    # ── Public API ──────────────────────────────────────────────────

    def scrub(self, text: str) -> str:
        """Apply all deterministic replacements and return scrubbed text."""
        result = text
        for _ptype, pattern_str, replacement in self._patterns:
            try:
                result = re.sub(pattern_str, replacement, result, flags=re.IGNORECASE)
            except re.error:
                logger.debug("Invalid scrub pattern: %s", pattern_str)
        return result

    def scan(self, text: str) -> ScanResult:
        """Scan text for remaining direct identifiers."""
        return self._scanner.scan_text(text, self._known_values)

    def precheck(self, text: str) -> PrecheckReport:
        """Scan scrubbed text and return a precheck report."""
        scan = self.scan(text)
        return PrecheckReport.from_scan_result(scan)

    def scrub_and_precheck(self, text: str) -> tuple[str, PrecheckReport]:
        """Scrub text, then scan for remaining direct identifiers."""
        scrubbed = self.scrub(text)
        report = self.precheck(scrubbed)
        return scrubbed, report

    # ── Internal ────────────────────────────────────────────────────

    def _build_known_values(self) -> dict[str, list[str]]:
        """Build the value dict expected by ExactResidualScanner."""
        values: dict[str, list[str]] = {
            "cik": [],
            "ticker": [],
            "company": [],
            "former_company_name": [],
            "company_domain": [],
            "company_email_domain": [],
            "product": [],
            "brand": [],
            "subsidiary": [],
            "headquarters": [],
            "sec_accession_number": [],
        }

        if not self._registry:
            return values

        for entity in self._registry.all_entities():
            etype = entity.entity_type.value
            if etype in values:
                values[etype].append(entity.canonical_private_value)

        for alias in self._registry.all_aliases():
            etype = alias.entity_type.value
            if etype in values:
                values[etype].append(alias.private_alias_value)

        return values

    def _build_patterns(self) -> None:
        """Build all scrub patterns: alias-based + hardcoded extras."""
        patterns: list[tuple[str, str, str]] = []

        # ── Alias-based patterns from the registry ──────────────────
        if self._registry:
            for alias in self._registry.all_aliases():
                if alias.entity_type.value not in (_DIRECT_ENTITY_TYPES | _NAME_ENTITY_TYPES):
                    continue

                replacement = self._replacement_for(alias)

                try:
                    alias_patterns = get_patterns_for_alias(alias, self._registry)
                except Exception:
                    logger.debug("Failed to build patterns for alias %s", alias.alias_id)
                    continue

                for ptype, pattern_str, _replacement, _priority, _flags in alias_patterns:
                    patterns.append((ptype, pattern_str, replacement))

        # ── Hardcoded structural patterns (always applied) ──────────

        # CIK phrases
        patterns.append(("cik_phrase", _CIK_PHRASE_RE.pattern, "[CIK_REDACTED]"))

        # Ticker symbol phrases
        patterns.append(
            (
                "ticker_symbol_phrase",
                r"\b(?:ticker|trading)\s+symbol\s*(?::|is)?\s*[A-Z]{1,5}\b",
                "[TICKER_SYMBOL_REDACTED]",
            )
        )

        # Exchange-prefixed tickers
        patterns.append(
            (
                "exchange_ticker",
                r"\b(?:NYSE|NASDAQ|NYSE\s*Arca)\s*:\s*[A-Z]{1,5}\b",
                "[EXCHANGE_TICKER_REDACTED]",
            )
        )

        # SEC file numbers
        patterns.append(("sec_file_number", _SEC_FILE_NUMBER_RE.pattern, "[FILE_NUMBER_REDACTED]"))

        # EIN
        patterns.append(("ein", _EIN_RE.pattern, "[EIN_REDACTED]"))

        # CIK in URLs
        patterns.append(("cik_url", _CIK_URL_RE.pattern, "[CIK_URL_REDACTED]"))

        # SEC accession numbers (18-digit or dashed)
        patterns.append(
            (
                "accession",
                r"\b\d{10}-\d{2}-\d{6}\b|\b\d{18}\b",
                "[ACCESSION_REDACTED]",
            )
        )

        # SEC archive paths
        patterns.append(
            ("sec_archive_path", _SEC_ARCHIVE_PATH_RE.pattern, "[SEC_ARCHIVE_PATH_REDACTED]")
        )

        # EntityCentralIndexKey XBRL attribute
        patterns.append(
            (
                "entity_cik_attr",
                _ENTITY_CIK_ATTR_RE.pattern,
                'EntityCentralIndexKey="[CIK_REDACTED]"',
            )
        )

        self._patterns = patterns

    def _replacement_for(self, alias: Alias) -> str:
        """Choose the right replacement for an alias's entity type."""
        etype = alias.entity_type.value

        # Structural identifiers → bracket tags
        if etype in _REDACT_TAGS:
            return _REDACT_TAGS[etype]

        # Named entities → assigned pseudonym
        if self._registry:
            entity = self._registry.get_entity(alias.canonical_entity_id)
            if entity and entity.assigned_pseudonym:
                return entity.assigned_pseudonym

        return "[REDACTED]"
