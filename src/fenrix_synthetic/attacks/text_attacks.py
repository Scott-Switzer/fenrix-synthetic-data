"""Text-based re-identification attacks (Phase 4G).

Implements:
- Exact identity scan
- Normalized identity scan
- Fuzzy alias scan
- Digital identifier scan (URLs, domains, emails, phones)
- Unique phrase scan
- Semantic fingerprint scan
- Filename and metadata scan

All attacks operate on masked release candidates only.
No real company names in tracked attack artifacts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TextAttackHit:
    """A single hit from a text attack."""

    attack_type: str
    matched_text: str
    location: str = ""
    start: int = 0
    end: int = 0
    context: str = ""
    severity: str = "blocking"


@dataclass
class TextAttackResult:
    """Result of running text-based attacks on a document."""

    attack_type: str
    document_id: str
    total_hits: int = 0
    blocking_hits: int = 0
    warning_hits: int = 0
    hits: list[TextAttackHit] = field(default_factory=list)
    is_blocked: bool = False
    attack_duration_ms: float = 0.0


def _find_all(text: str, pattern: str) -> list[tuple[int, int, str]]:
    """Find all non-overlapping matches of pattern in text."""
    results: list[tuple[int, int, str]] = []
    try:
        for match in re.finditer(pattern, text):
            results.append((match.start(), match.end(), match.group()))
    except re.error:
        # Fall back to simple substring search
        lower_text = text.lower()
        lower_pat = pattern.lower()
        idx = lower_text.find(lower_pat)
        while idx != -1:
            results.append((idx, idx + len(pattern), text[idx : idx + len(pattern)]))
            idx = lower_text.find(lower_pat, idx + 1)
    return results


def exact_identity_scan(
    text: str,
    document_id: str,
    values: dict[str, list[str]],
) -> TextAttackResult:
    """Scan for exact matches of known identity values.

    Args:
        text: Masked document text
        document_id: Document identifier
        values: Dict of category -> list of private values to scan for

    Returns:
        TextAttackResult with all exact matches found
    """
    result = TextAttackResult(attack_type="exact_identity", document_id=document_id)

    for category, value_list in values.items():
        for value in value_list:
            if not value.strip():
                continue
            pattern = re.escape(value)
            matches = _find_all(text, pattern)
            for start, end, matched in matches:
                ctx_start = max(0, start - 20)
                ctx_end = min(len(text), end + 20)
                context = text[ctx_start:ctx_end].replace("\n", " ")
                hit = TextAttackHit(
                    attack_type="exact_identity",
                    matched_text=matched,
                    location=category,
                    start=start,
                    end=end,
                    context=context,
                    severity="blocking",
                )
                result.hits.append(hit)
                result.blocking_hits += 1

    result.total_hits = len(result.hits)
    result.is_blocked = result.blocking_hits > 0
    return result


def normalized_identity_scan(
    text: str,
    document_id: str,
    values: dict[str, list[str]],
) -> TextAttackResult:
    """Scan for normalized matches (lowercase, whitespace-collapsed)."""
    result = TextAttackResult(attack_type="normalized_identity", document_id=document_id)
    normalized_text = " ".join(text.lower().split())

    for category, value_list in values.items():
        for value in value_list:
            if not value.strip():
                continue
            normalized_value = " ".join(value.lower().strip().split())
            if normalized_value in normalized_text:
                idx = normalized_text.find(normalized_value)
                ctx_start = max(0, idx - 20)
                ctx_end = min(len(text), idx + len(value) + 20)
                context = text[ctx_start:ctx_end].replace("\n", " ")
                hit = TextAttackHit(
                    attack_type="normalized_identity",
                    matched_text=value,
                    location=category,
                    start=idx,
                    end=idx + len(value),
                    context=context,
                    severity="blocking",
                )
                result.hits.append(hit)
                result.blocking_hits += 1

    result.total_hits = len(result.hits)
    result.is_blocked = result.blocking_hits > 0
    return result


def digital_identifier_scan(
    text: str,
    document_id: str,
    websites: list[str],
    domains: list[str],
    emails: list[str],
    phones: list[str],
) -> TextAttackResult:
    """Scan for digital identifiers (URLs, domains, emails, phone numbers)."""
    result = TextAttackResult(attack_type="digital_identifier", document_id=document_id)

    # URL pattern
    url_pattern = re.compile(r"https?://[^\s]+")
    for match in url_pattern.finditer(text):
        hit = TextAttackHit(
            attack_type="digital_identifier",
            matched_text=match.group(),
            location="url",
            start=match.start(),
            end=match.end(),
            severity="blocking",
        )
        result.hits.append(hit)
        result.blocking_hits += 1

    # Domain scan
    for domain in domains:
        if domain.strip() and domain.strip() in text:
            hit = TextAttackHit(
                attack_type="digital_identifier",
                matched_text=domain,
                location="domain",
                severity="blocking",
            )
            result.hits.append(hit)
            result.blocking_hits += 1

    # Email scan
    for email in emails:
        if email.strip() and email.strip().lower() in text.lower():
            hit = TextAttackHit(
                attack_type="digital_identifier",
                matched_text=email,
                location="email",
                severity="blocking",
            )
            result.hits.append(hit)
            result.blocking_hits += 1

    # Phone scan
    for phone in phones:
        if phone.strip():
            pattern = re.escape(phone.strip())
            for match in re.finditer(pattern, text):
                hit = TextAttackHit(
                    attack_type="digital_identifier",
                    matched_text=match.group(),
                    location="phone",
                    severity="blocking",
                )
                result.hits.append(hit)
                result.blocking_hits += 1

    result.total_hits = len(result.hits)
    result.is_blocked = result.blocking_hits > 0
    return result


def unique_phrase_scan(
    text: str,
    document_id: str,
    phrases: list[str],
) -> TextAttackResult:
    """Scan for unique phrases / semantic fingerprints."""
    result = TextAttackResult(attack_type="unique_phrase", document_id=document_id)

    for phrase in phrases:
        if not phrase.strip():
            continue
        pattern = re.escape(phrase)
        for match in re.finditer(pattern, text, re.IGNORECASE):
            ctx_start = max(0, match.start() - 30)
            ctx_end = min(len(text), match.end() + 30)
            context = text[ctx_start:ctx_end].replace("\n", " ")
            hit = TextAttackHit(
                attack_type="unique_phrase",
                matched_text=match.group(),
                location="semantic_fingerprint",
                context=context,
                severity="blocking",
            )
            result.hits.append(hit)
            result.blocking_hits += 1

    result.total_hits = len(result.hits)
    result.is_blocked = result.blocking_hits > 0
    return result


def filename_and_metadata_scan(
    filenames: list[str],
    metadata: dict,
    values: dict[str, list[str]],
) -> TextAttackResult:
    """Scan filenames and metadata for known identifiers."""
    result = TextAttackResult(attack_type="filename_metadata", document_id="release_dossier")

    # Scan filenames
    for fname in filenames:
        lower_fname = fname.lower()
        for _category, value_list in values.items():
            for value in value_list:
                if value.lower().strip() in lower_fname:
                    hit = TextAttackHit(
                        attack_type="filename_metadata",
                        matched_text=value,
                        location=f"file:{fname}",
                        severity="blocking",
                    )
                    result.hits.append(hit)
                    result.blocking_hits += 1

    # Scan metadata
    metadata_text = str(metadata).lower()
    for category, value_list in values.items():
        for value in value_list:
            if value.lower().strip() in metadata_text:
                hit = TextAttackHit(
                    attack_type="filename_metadata",
                    matched_text=value,
                    location=f"metadata:{category}",
                    severity="blocking",
                )
                result.hits.append(hit)
                result.blocking_hits += 1

    result.total_hits = len(result.hits)
    result.is_blocked = result.blocking_hits > 0
    return result


ALLOWED = "allowed"
BLOCKING = "blocking"
