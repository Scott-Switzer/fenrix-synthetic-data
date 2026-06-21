"""Pseudonym-safe allowlist.

The deterministic masker produces pseudonyms of the form
``{EntityType} {counter:03d}`` (per ``PseudonymPolicy``), and the
classroom-safe numeric writer and news surrogate generators emit
additional bracketed placeholders like ``[PERIOD DATE]`` and
``[PUBLISHER REMOVED]``. The exact-residual scanner MUST NOT count
those system-generated pseudonyms as privacy leaks, because they
prove zero bits about the real source value.

This module is the single source of truth for the scanner's
``is_pseudonym_suppression_eligible`` check. Patterns are AUDITABLE
because:

1. Every pattern is anchored (start ``^`` / end ``$``) so loose
   substrings cannot match real source values;
2. Patterns explicitly reference system-generated tokens (``Aster``,
   ``ex-``, ``[bracket]``, ``synthetic ... surrogate``);
3. The exact list is logged at debug level when a run starts so a
   reviewer can audit which substrings were suppressed.
"""

from __future__ import annotations

import logging
import re
from typing import Final

logger = logging.getLogger(__name__)


# ── Pattern catalogue ────────────────────────────────────────────────


# Counter-suffixed pseudonyms: ``Executive 042``, ``Subsidiary 017``, etc.
# Anchored so substring matches never qualify.
_COUNTER_PSEUDONYM_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:Company|FormerCompanyName|Ticker|Cik|SecAccessionNumber|"
    r"SecPrimaryDocument|CompanyDomain|CompanyEmailDomain|Executive|"
    r"BoardMember|Subsidiary|BusinessSegment|Product|Brand|"
    r"ProprietaryPlatform|Facility|Headquarters|AcquisitionTarget|"
    r"JointVenture|Auditor|LawFirm|Customer|Supplier|Competitor|"
    r"Regulator)\s+\d{1,4}$"
)


# Synthetic / surrogate literal tokens (semantic fingerprint or
# fallback-name references).
_SYNTHETIC_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:synthetic financial (?:disclosure|news) surrogate)$"
)


# Bracketed placeholder outputs from the surrogate + numeric writers.
# Examples: ``[PERIOD DATE]``, ``[PUBLISHER REMOVED]``,
# ``[Executive-*]``, ``[Product-*]``, ``[URL REMOVED]``.
_BRACKETED_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\[(?:PERIOD DATE|PUBLISHER REMOVED|URL REMOVED|"
    r"Executive-\*|Product-\*|Source-\*|Region-\*|Brand-\*|Subsidiary-\*)\]$"
)


# Allowlist regex gate.
_ALLOWLIST = (_COUNTER_PSEUDONYM_RE, _SYNTHETIC_LITERAL_RE, _BRACKETED_PLACEHOLDER_RE)

# An exhaustive plain-text enumeration for the QA payload so a
# reviewer can audit the exact safe pseudonyms the scanner ignored.
_ALLOWLIST_LITERAL_HUMAN_DOC = (
    "System-suppressed pseudonyms include counter-suffixed tokens of "
    "the form '<EntityType> <NNN>' (e.g., Executive 042, Subsidiary "
    "017), the literal phrase 'synthetic financial disclosure "
    "surrogate' / 'synthetic financial news surrogate', and bracketed "
    "placeholders like '[PERIOD DATE]', '[PUBLISHER REMOVED]', "
    "'[Executive-*]', '[Product-*]'."
)


# ── Public API ────────────────────────────────────────────────────────


def is_pseudonym_suppression_eligible(text: str) -> bool:
    """Return True when ``text`` is a system-generated safe pseudonym.

    Suppression is exact and anchored so a generic-looking raw value
    (e.g., "the company" or "Q1 2024") is NEVER suppressed by accident.
    """
    if not text or not isinstance(text, str):
        return False
    candidate = text.strip()
    if not candidate:
        return False
    return any(p.match(candidate) for p in _ALLOWLIST)


def safe_pseudonym_patterns() -> list[str]:
    """Pattern sources for QA logs + audit dashboards (no private values)."""
    return [p.pattern for p in _ALLOWLIST]


def allowlist_human_readable() -> str:
    return _ALLOWLIST_LITERAL_HUMAN_DOC


# Convenience constant the scanner / orchestrator can count without
# re-enumerating the patterns. Used for ``pseudonym_allowlist_size``
# QA reports.
SAFE_PSEUDONYM_ALLOWLIST_SIZE: Final[int] = len(_ALLOWLIST)
