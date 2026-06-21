"""Atlas candidate harvesting for text masking hardening (Phase 5A, Part 11).

Harvests candidate identity values from documents that should enter
a manual review queue. No value is automatically accepted.

Sources:
- Deterministic extraction (regex patterns)
- Document metadata (headers, footers, exhibits)
- Digital identifiers (URLs, domains, emails, phone numbers)
- Organization names, people, subsidiaries, products
- Legal identifiers, counterparties, acquisitions, proceedings
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HarvestedCandidate:
    """A candidate identity value harvested from a document.

    Must be manually reviewed before acceptance.
    """

    value: str
    category: str
    source: str  # e.g., "regex:url", "metadata:header", "pattern:email"
    start: int = 0
    end: int = 0
    context: str = ""
    confidence: float = 0.5
    is_auto_accepted: bool = False  # Never auto-accept


@dataclass
class HarvestResult:
    """Result of harvesting operation."""

    total_harvested: int = 0
    candidates: list[HarvestedCandidate] = field(default_factory=list)
    by_category: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class AtlasHarvester:
    """Harvest candidate identity values from documents.

    All harvested values must enter a manual review queue.
    No harvested value is automatically accepted.
    """

    def __init__(self) -> None:
        self._warnings: list[str] = []

    def harvest(self, text: str, metadata: dict[str, Any] | None = None) -> HarvestResult:
        """Harvest all candidate identity values from text and metadata."""
        result = HarvestResult()
        candidates: list[HarvestedCandidate] = []

        # Harvest from text patterns
        candidates.extend(self._harvest_urls(text))
        candidates.extend(self._harvest_emails(text))
        candidates.extend(self._harvest_phones(text))
        candidates.extend(self._harvest_domains(text))
        candidates.extend(self._harvest_organization_names(text))
        candidates.extend(self._harvest_people(text))
        candidates.extend(self._harvest_tickers(text))
        candidates.extend(self._harvest_ciks(text))
        candidates.extend(self._harvest_legal_identifiers(text))

        # Harvest from metadata
        if metadata:
            candidates.extend(self._harvest_metadata(text, metadata))

        # Deduplicate by value (case-insensitive)
        seen: set[str] = set()
        for c in candidates:
            key = c.value.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            result.candidates.append(c)
            result.by_category[c.category] = result.by_category.get(c.category, 0) + 1

        result.total_harvested = len(result.candidates)
        result.warnings = self._warnings
        return result

    def _harvest_urls(self, text: str) -> list[HarvestedCandidate]:
        """Harvest URLs from text."""
        candidates: list[HarvestedCandidate] = []
        pattern = re.compile(r"https?://(?:www\.)?[^\s<>\"']+")
        for match in pattern.finditer(text):
            url = match.group()
            ctx_start = max(0, match.start() - 30)
            ctx_end = min(len(text), match.end() + 30)
            context = text[ctx_start:ctx_end].replace("\n", " ")
            candidates.append(
                HarvestedCandidate(
                    value=url,
                    category="url",
                    source="regex:url",
                    start=match.start(),
                    end=match.end(),
                    context=context,
                    confidence=0.95,
                )
            )

            # Also harvest the domain from the URL
            domain_match = re.search(r"https?://(?:www\.)?([^/\s]+)", url)
            if domain_match:
                domain = domain_match.group(1)
                candidates.append(
                    HarvestedCandidate(
                        value=domain,
                        category="domain",
                        source="extracted:url_domain",
                        start=match.start(),
                        end=match.end(),
                        context=context[:60],
                        confidence=0.9,
                    )
                )

        return candidates

    def _harvest_emails(self, text: str) -> list[HarvestedCandidate]:
        """Harvest email addresses from text."""
        candidates: list[HarvestedCandidate] = []
        pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
        for match in pattern.finditer(text):
            email = match.group()
            ctx_start = max(0, match.start() - 30)
            ctx_end = min(len(text), match.end() + 30)
            context = text[ctx_start:ctx_end].replace("\n", " ")
            candidates.append(
                HarvestedCandidate(
                    value=email,
                    category="email",
                    source="regex:email",
                    start=match.start(),
                    end=match.end(),
                    context=context,
                    confidence=0.95,
                )
            )

            # Harvest email domain
            domain = email.split("@")[1]
            if domain:
                candidates.append(
                    HarvestedCandidate(
                        value=domain,
                        category="email_domain",
                        source="extracted:email_domain",
                        start=match.start(),
                        end=match.end(),
                        context=context[:60],
                        confidence=0.9,
                    )
                )

        return candidates

    def _harvest_phones(self, text: str) -> list[HarvestedCandidate]:
        """Harvest phone numbers from text."""
        candidates: list[HarvestedCandidate] = []
        # US phone numbers
        patterns = [
            re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
            re.compile(r"\+\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
        ]
        for pattern in patterns:
            for match in pattern.finditer(text):
                phone = match.group().strip()
                if len(phone) < 7:
                    continue
                ctx_start = max(0, match.start() - 20)
                ctx_end = min(len(text), match.end() + 20)
                context = text[ctx_start:ctx_end].replace("\n", " ")
                candidates.append(
                    HarvestedCandidate(
                        value=phone,
                        category="phone",
                        source="regex:phone",
                        start=match.start(),
                        end=match.end(),
                        context=context,
                        confidence=0.85,
                    )
                )
        return candidates

    def _harvest_domains(self, text: str) -> list[HarvestedCandidate]:
        """Harvest domain names (standalone, not in URLs) from text."""
        candidates: list[HarvestedCandidate] = []
        pattern = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|edu|gov|io|co|ai|app)\b")
        for match in pattern.finditer(text):
            domain = match.group()
            # Skip if likely part of a URL
            if domain.lower().startswith("http"):
                continue
            ctx_start = max(0, match.start() - 20)
            ctx_end = min(len(text), match.end() + 20)
            context = text[ctx_start:ctx_end].replace("\n", " ")
            candidates.append(
                HarvestedCandidate(
                    value=domain,
                    category="domain",
                    source="regex:standalone_domain",
                    start=match.start(),
                    end=match.end(),
                    context=context,
                    confidence=0.7,
                )
            )
        return candidates

    def _harvest_organization_names(self, text: str) -> list[HarvestedCandidate]:
        """Harvest potential organization names using heuristics."""
        candidates: list[HarvestedCandidate] = []
        patterns = [
            # "Inc.", "Corp.", "LLC", "Ltd." suffixed
            re.compile(
                r"\b[A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+){0,4}\s+(?:Inc\.?|Corp\.?|LLC|Ltd\.?|PLC|LP|GmbH)\b"
            ),
            # "Company", "Corporation", "Incorporated" suffixed
            re.compile(
                r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,4}\s+(?:Company|Corporation|Incorporated)\b"
            ),
            # "and Company", "& Co." patterns
            re.compile(r"\b[A-Z][A-Za-z]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z]+)\b"),
        ]
        for pattern in patterns:
            for match in pattern.finditer(text):
                name = match.group()
                if len(name) < 5:
                    continue
                ctx_start = max(0, match.start() - 20)
                ctx_end = min(len(text), match.end() + 20)
                context = text[ctx_start:ctx_end].replace("\n", " ")
                candidates.append(
                    HarvestedCandidate(
                        value=name,
                        category="organization",
                        source="regex:org_name",
                        start=match.start(),
                        end=match.end(),
                        context=context,
                        confidence=0.6,
                    )
                )
        return candidates

    def _harvest_people(self, text: str) -> list[HarvestedCandidate]:
        """Harvest potential person names from text."""
        candidates: list[HarvestedCandidate] = []
        # Title-prefixed names
        title_pattern = re.compile(
            r"\b(?:Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.|Sen\.|Rep\.|Gov\.)"
            r"\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"
        )
        for match in title_pattern.finditer(text):
            name = match.group()
            ctx_start = max(0, match.start() - 20)
            ctx_end = min(len(text), match.end() + 20)
            context = text[ctx_start:ctx_end].replace("\n", " ")
            candidates.append(
                HarvestedCandidate(
                    value=name,
                    category="person",
                    source="regex:title_name",
                    start=match.start(),
                    end=match.end(),
                    context=context,
                    confidence=0.7,
                )
            )

        # Executive pattern: "CEO John Smith", "President Jane Doe"
        exec_pattern = re.compile(
            r"\b(?:CEO|CFO|COO|CTO|President|Chairman|Executive\s+Vice\s+President|Managing\s+Director)"
            r"\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"
        )
        for match in exec_pattern.finditer(text):
            name = match.group()
            ctx_start = max(0, match.start() - 20)
            ctx_end = min(len(text), match.end() + 20)
            context = text[ctx_start:ctx_end].replace("\n", " ")
            candidates.append(
                HarvestedCandidate(
                    value=name,
                    category="person",
                    source="regex:executive",
                    start=match.start(),
                    end=match.end(),
                    context=context,
                    confidence=0.8,
                )
            )
        return candidates

    def _harvest_tickers(self, text: str) -> list[HarvestedCandidate]:
        """Harvest ticker symbols from text."""
        candidates: list[HarvestedCandidate] = []
        pattern = re.compile(r"\(([A-Z]{1,5})\)")
        stock_exchanges = {"NYSE", "NASDAQ", "AMEX", "ARCA"}
        common_words = {"THE", "AND", "FOR", "ARE", "NOT", "BUT", "HAS", "HAD", "ITS"}

        for match in pattern.finditer(text):
            ticker = match.group(1)
            if ticker in stock_exchanges or ticker in common_words:
                continue
            ctx_start = max(0, match.start() - 20)
            ctx_end = min(len(text), match.end() + 20)
            context = text[ctx_start:ctx_end].replace("\n", " ")
            candidates.append(
                HarvestedCandidate(
                    value=ticker,
                    category="ticker",
                    source="regex:ticker_parenthesized",
                    start=match.start(),
                    end=match.end(),
                    context=context,
                    confidence=0.6,
                )
            )
        return candidates

    def _harvest_ciks(self, text: str) -> list[HarvestedCandidate]:
        """Harvest CIK numbers from text."""
        candidates: list[HarvestedCandidate] = []
        pattern = re.compile(r"CIK\s*#?\s*(\d{6,10})\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            cik = match.group()
            ctx_start = max(0, match.start() - 20)
            ctx_end = min(len(text), match.end() + 20)
            context = text[ctx_start:ctx_end].replace("\n", " ")
            candidates.append(
                HarvestedCandidate(
                    value=cik,
                    category="cik",
                    source="regex:cik",
                    start=match.start(),
                    end=match.end(),
                    context=context,
                    confidence=0.9,
                )
            )
        return candidates

    def _harvest_legal_identifiers(self, text: str) -> list[HarvestedCandidate]:
        """Harvest legal identifiers and proceeding names."""
        candidates: list[HarvestedCandidate] = []
        patterns = [
            # Case names: "Smith v. Jones", "In re Company"
            re.compile(r"[A-Z][a-z]+\s+v\.?\s+[A-Z][a-z]+"),
            re.compile(r"In\s+re\s+[A-Z][A-Za-z\s]+"),
            # Docket numbers
            re.compile(r"No\.\s*\d{2}-\d{4,6}"),
            re.compile(r"Case\s+No\.?\s*\d+"),
            # Acquisition/merger mentions
            re.compile(r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+acquisition\s+of\s+[A-Z]"),
            re.compile(r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+merged\s+with\s+[A-Z]"),
        ]
        for pattern in patterns:
            for match in pattern.finditer(text):
                value = match.group()
                if len(value) < 5:
                    continue
                ctx_start = max(0, match.start() - 20)
                ctx_end = min(len(text), match.end() + 20)
                context = text[ctx_start:ctx_end].replace("\n", " ")
                candidates.append(
                    HarvestedCandidate(
                        value=value,
                        category="legal_identifier",
                        source="regex:legal",
                        start=match.start(),
                        end=match.end(),
                        context=context,
                        confidence=0.65,
                    )
                )
        return candidates

    def _harvest_metadata(self, text: str, metadata: dict[str, Any]) -> list[HarvestedCandidate]:
        """Harvest candidates from document metadata."""
        candidates: list[HarvestedCandidate] = []

        # Check for common metadata fields
        metadata_sources = {
            "company_name": "company name",
            "issuer_name": "company name",
            "registrant_name": "company name",
            "address": "address",
            "phone": "phone",
            "website": "domain/url",
            "contact": "person",
        }

        for key, category in metadata_sources.items():
            val = metadata.get(key)
            if isinstance(val, str) and val.strip():
                candidates.append(
                    HarvestedCandidate(
                        value=val.strip(),
                        category=category,
                        source=f"metadata:{key}",
                        confidence=0.85,
                    )
                )

        # Accession numbers and filing metadata
        for key in ("accession_number", "cik", "ticker"):
            val = metadata.get(key)
            if isinstance(val, str) and val.strip():
                candidates.append(
                    HarvestedCandidate(
                        value=val.strip(),
                        category=key,
                        source=f"metadata:{key}",
                        confidence=0.9,
                    )
                )

        return candidates
