from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import MaskingAudit


@dataclass
class DiscoveredEntity:
    text: str
    start: int
    end: int
    discovery_type: str
    confidence: float = 0.5
    context: str = ""


@dataclass
class DiscoveryResult:
    total_found: int = 0
    entities: list[DiscoveredEntity] = field(default_factory=list)
    masked_count: int = 0
    unmasked_count: int = 0
    coverage_pct: float = 0.0
    unmasked_high_confidence: list[DiscoveredEntity] = field(default_factory=list)


_PSEUDONYM_RE = re.compile(r"\b[A-Z][a-z]+ \d{3}\b")
_SECTION_MARKER_RE = re.compile(r"^(?:Item|Part)\s+\d+[A-Z]?\.?\s*", re.IGNORECASE | re.MULTILINE)
_KNOWN_SAFE_PHRASES: set[str] = {
    "THE",
    "COMPANY",
    "MANAGEMENT",
    "SHAREHOLDERS",
    "BOARD",
    "DIRECTORS",
    "FINANCIAL",
    "STATEMENTS",
    "NOTES",
    "EXHIBITS",
    "INDEX",
    "TABLE",
    "CONTENTS",
    "OFFICERS",
    "EMPLOYEES",
    "CUSTOMERS",
    "REVENUE",
    "INCOME",
    "ASSETS",
    "LIABILITIES",
    "EQUITY",
    "CASH",
    "OPERATIONS",
    "BUSINESS",
    "SEGMENTS",
    "PRODUCTS",
    "SERVICES",
    "MARKET",
    "RISK",
    "LEGAL",
    "PROPERTY",
    "TAX",
    "BENEFITS",
    "STOCK",
    "DEBT",
    "INTEREST",
    "ACCOUNTING",
    "AUDIT",
    "COMMITTEE",
    "POLICIES",
    "CONTROLS",
    "PROCEDURES",
    "RESULTS",
    "CONDITION",
    "REQUIREMENTS",
    "REGULATIONS",
    "GOVERNMENT",
    "MATERIAL",
    "ADVERSE",
    "EVENTS",
    "DEFAULT",
    "COMPETITION",
    "TECHNOLOGY",
    "RESEARCH",
    "DEVELOPMENT",
    "INTELLECTUAL",
    "TRADEMARKS",
    "PATENTS",
    "LICENSES",
    "FRANCHISES",
    "SUBSIDIARIES",
    "AFFILIATES",
    "JOINT",
    "VENTURES",
    "PARTNERSHIPS",
    "ACQUISITIONS",
    "DIVESTITURES",
    "RESTRUCTURING",
    "IMPAIRMENT",
    "GOODWILL",
    "INTANGIBLE",
    "CAPITAL",
    "EXPENDITURES",
    "OBLIGATIONS",
    "COMMITMENTS",
    "CONTINGENCIES",
    "SETTLEMENTS",
    "INSURANCE",
    "ENVIRONMENTAL",
    "LABOR",
    "SUPPLY",
    "CHAIN",
    "DISTRIBUTION",
    "MARKETING",
    "ADVERTISING",
    "PROMOTION",
    "SEASONAL",
    "QUARTERLY",
    "ANNUAL",
    "CONSOLIDATED",
    "REPORT",
    "PERIOD",
    "FISCAL",
    "CALENDAR",
    "GENERAL",
    "ADMINISTRATIVE",
    "SELLING",
    "COST",
    "EXPENSES",
    "PROFIT",
    "MARGIN",
    "RETURN",
    "INVESTMENT",
    "HOLDINGS",
    "GROUP",
    "LIMITED",
    "CORPORATION",
    "INCORPORATED",
    "COMPANIES",
    "ENTERPRISES",
    "SYSTEMS",
    "SOLUTIONS",
    "NETWORKS",
    "INFRASTRUCTURE",
    "PLATFORM",
    "PORTFOLIO",
    "STRATEGY",
    "GROWTH",
    "VALUE",
    "SHARE",
    "INVESTORS",
}


class ResidualEntityDiscoverer:
    def discover(
        self, text: str, known_pseudonyms: set[str] | None = None
    ) -> list[DiscoveredEntity]:
        pseudonyms = known_pseudonyms or set()
        entities: list[DiscoveredEntity] = []

        entities.extend(self._find_capitalized_phrases(text, pseudonyms))
        entities.extend(self._find_ticker_patterns(text, pseudonyms))
        entities.extend(self._find_urls(text, pseudonyms))
        entities.extend(self._find_emails(text, pseudonyms))
        entities.extend(self._find_executive_patterns(text, pseudonyms))
        entities.extend(self._find_cik_patterns(text, pseudonyms))

        entities.sort(key=lambda e: e.start)
        return entities

    def discover_unmasked(
        self,
        text: str,
        known_pseudonyms: set[str] | None = None,
        masked_spans: list[tuple[int, int]] | None = None,
    ) -> list[DiscoveredEntity]:
        all_discovered = self.discover(text, known_pseudonyms)
        masked_spans = masked_spans or []

        unmasked: list[DiscoveredEntity] = []
        for entity in all_discovered:
            if not self._is_covered_by_spans(entity.start, entity.end, masked_spans):
                unmasked.append(entity)
        return unmasked

    def compute_coverage(
        self,
        discovered: list[DiscoveredEntity],
        accepted_spans: list[tuple[int, int]],
    ) -> DiscoveryResult:
        result = DiscoveryResult()
        for entity in discovered:
            if self._is_covered_by_spans(entity.start, entity.end, accepted_spans):
                result.masked_count += 1
            else:
                result.unmasked_count += 1
                result.entities.append(entity)
                if entity.confidence >= 0.7:
                    result.unmasked_high_confidence.append(entity)

        result.total_found = len(discovered)
        if result.total_found > 0:
            result.coverage_pct = round(result.masked_count / result.total_found * 100, 1)

        return result

    def _is_covered_by_spans(self, start: int, end: int, spans: list[tuple[int, int]]) -> bool:
        for span_start, span_end in spans:
            if start >= span_start and end <= span_end:
                return True
            if start < span_end and end > span_start:
                return True
        return False

    def _find_capitalized_phrases(self, text: str, pseudonyms: set[str]) -> list[DiscoveredEntity]:
        entities: list[DiscoveredEntity] = []
        pattern = re.compile(r"\b(?:[A-Z][a-z]+[']?[a-z]*\s?){2,5}")
        for match in pattern.finditer(text):
            phrase = match.group().strip()
            if self._is_safe_phrase(phrase, pseudonyms):
                continue
            entities.append(
                DiscoveredEntity(
                    text=phrase,
                    start=match.start(),
                    end=match.end(),
                    discovery_type="capitalized_phrase",
                    confidence=0.4,
                )
            )
        return entities

    def _find_ticker_patterns(self, text: str, pseudonyms: set[str]) -> list[DiscoveredEntity]:
        entities: list[DiscoveredEntity] = []
        pattern = re.compile(r"\(([A-Z]{1,5})\)")
        for match in pattern.finditer(text):
            ticker = match.group(1)
            if ticker in {"NYSE", "NASDAQ", "AMEX", "ARCA"}:
                continue
            if self._is_common_word(ticker):
                continue
            entities.append(
                DiscoveredEntity(
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    discovery_type="ticker_pattern",
                    confidence=0.6,
                )
            )
        return entities

    def _find_urls(self, text: str, pseudonyms: set[str]) -> list[DiscoveredEntity]:
        entities: list[DiscoveredEntity] = []
        pattern = re.compile(r"https?://(?:www\.)?[^\s<>\"']+")
        for match in pattern.finditer(text):
            url = match.group()
            entities.append(
                DiscoveredEntity(
                    text=url,
                    start=match.start(),
                    end=match.end(),
                    discovery_type="url",
                    confidence=0.9,
                )
            )
        return entities

    def _find_emails(self, text: str, pseudonyms: set[str]) -> list[DiscoveredEntity]:
        entities: list[DiscoveredEntity] = []
        pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
        for match in pattern.finditer(text):
            entities.append(
                DiscoveredEntity(
                    text=match.group(),
                    start=match.start(),
                    end=match.end(),
                    discovery_type="email",
                    confidence=0.9,
                )
            )
        return entities

    def _find_executive_patterns(self, text: str, pseudonyms: set[str]) -> list[DiscoveredEntity]:
        entities: list[DiscoveredEntity] = []
        titles = r"(?:CEO|CFO|COO|CTO|CIO|CMO|CHRO|President|Chairman|Vice\s+President|Managing\s+Director|Executive\s+(?:Vice\s+)?President|General\s+Counsel|Chief\s+\w+\s+Officer)"
        pattern = re.compile(rf"\b{titles}\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)")
        for match in pattern.finditer(text):
            name = match.group(1)
            if self._is_pseudonym(name, pseudonyms):
                continue
            entities.append(
                DiscoveredEntity(
                    text=match.group(),
                    start=match.start(),
                    end=match.end(),
                    discovery_type="executive_pattern",
                    confidence=0.8,
                )
            )
        return entities

    def _find_cik_patterns(self, text: str, pseudonyms: set[str]) -> list[DiscoveredEntity]:
        entities: list[DiscoveredEntity] = []
        pattern = re.compile(r"CIK\s*#?\s*(\d{6,10})\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            entities.append(
                DiscoveredEntity(
                    text=match.group(),
                    start=match.start(),
                    end=match.end(),
                    discovery_type="cik",
                    confidence=0.9,
                )
            )
        return entities

    def _is_safe_phrase(self, phrase: str, pseudonyms: set[str]) -> bool:
        upper = phrase.upper().strip()
        if self._is_pseudonym(phrase, pseudonyms):
            return True
        if len(phrase) <= 3:
            return True
        if _SECTION_MARKER_RE.match(phrase):
            return True
        words = upper.replace("'", "").split()
        if not words:
            return True
        if words[0] in _KNOWN_SAFE_PHRASES:
            return True
        if all(w in _KNOWN_SAFE_PHRASES for w in words):
            return True
        return False

    def _is_pseudonym(self, text: str, pseudonyms: set[str]) -> bool:
        if text.strip() in pseudonyms:
            return True
        if _PSEUDONYM_RE.match(text):
            return True
        return False

    def _is_common_word(self, word: str) -> bool:
        if len(word) <= 2:
            return True
        common = {
            "THE",
            "AND",
            "FOR",
            "ARE",
            "NOT",
            "BUT",
            "HAS",
            "HAD",
            "ITS",
            "ALL",
            "ANY",
            "CAN",
            "MAY",
            "WAS",
            "WERE",
            "BEEN",
            "BEING",
            "MORE",
            "MOST",
            "SOME",
            "SUCH",
            "THAN",
            "THAT",
            "THIS",
            "WITH",
            "FROM",
            "WHAT",
            "WHEN",
            "WHERE",
            "WHICH",
            "WHILE",
            "WHOLE",
            "DOES",
            "DONE",
            "EACH",
            "ELSE",
            "EVEN",
            "FEW",
            "HOW",
            "JUST",
            "LIKE",
            "LONG",
            "MAKE",
            "MANY",
            "MUCH",
            "NEAR",
            "NEXT",
            "ONLY",
            "OPEN",
            "OVER",
            "OWN",
            "PART",
            "PAST",
            "POST",
            "PULL",
            "PUSH",
            "QUIT",
            "RARE",
            "SAID",
            "SAME",
            "SAVE",
            "SEEN",
            "SHOW",
            "SIDE",
            "SITE",
            "SIZE",
            "SURE",
            "TAKE",
            "TOLD",
            "TURN",
            "USED",
            "VERY",
            "WANT",
            "WELL",
            "WENT",
            "YEAR",
            "YOUNG",
        }
        return word.upper() in common

    def extract_accepted_spans(self, accepted_matches: list) -> list[tuple[int, int]]:
        return [(m.original_start, m.original_end) for m in accepted_matches]

    @staticmethod
    def extract_pseudonyms_from_audit(audit: MaskingAudit) -> set[str]:
        pseudonyms: set[str] = set()
        for span in audit.spans:
            if span.replacement:
                pseudonyms.add(span.replacement)
        return pseudonyms
