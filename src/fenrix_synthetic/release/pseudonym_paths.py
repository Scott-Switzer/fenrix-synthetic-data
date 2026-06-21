"""Deterministic public aliases for export paths, filenames, and identifiers.

The public export must never contain the input ticker anywhere in a path.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PseudonymPathMap:
    """Maps private identifiers to deterministic public pseudonyms."""

    company_pseudonym: str
    ticker_pseudonym: str
    cik_pseudonym: str
    # Maps original accession number -> pseudonym
    accession_pseudonyms: dict[str, str] = field(default_factory=dict)
    # Maps original org path segment -> pseudonym segment
    path_pseudonyms: dict[str, str] = field(default_factory=dict)

    def public_directory(self) -> str:
        """Return the public directory name for this company."""
        return self.company_pseudonym

    def public_filename(self, accession: str) -> str:
        """Return a public filename for the given accession number."""
        pseudo = self.accession_pseudonyms.get(accession)
        if pseudo:
            return f"{pseudo}.md"
        # Fallback: hash the accession to produce a deterministic pseudonym
        return f"filing_{_short_hash(accession)}.md"

    def public_artifact_id(self, artifact_type: str, index: int, accession: str = "") -> str:
        """Return a public artifact ID with no ticker."""
        if accession:
            return f"{self.company_pseudonym}_{artifact_type}_{_short_hash(accession)}"
        return f"{self.company_pseudonym}_{artifact_type}_{index:04d}"

    def public_path(self, private_relative_path: str) -> str:
        """Rewrite a private relative path to a public one.

        Replaces the company ticker pseudonym segment in paths,
        and accession-based filenames with pseudonym filenames.
        """
        path = private_relative_path
        # Replace accession-based filenames
        for orig, pseudo in self.path_pseudonyms.items():
            if orig in path:
                path = path.replace(orig, pseudo)
        return path

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_pseudonym": self.company_pseudonym,
            "ticker_pseudonym": self.ticker_pseudonym,
            "cik_pseudonym": self.cik_pseudonym,
            "accession_count": len(self.accession_pseudonyms),
        }


def build_pseudonym_path_map(ticker: str, cik: str, accessions: list[str]) -> PseudonymPathMap:
    """Build a deterministic pseudonym path map.

    Pseudonyms are deterministic SHA-256 based but reveal no private values.
    """
    company_pseudo = f"COMP_{_short_hash(ticker)}"
    ticker_pseudo = f"TKR_{_short_hash(ticker)}"
    cik_pseudo = f"CIK_{_short_hash(cik)}" if cik else "CIK_UNKNOWN"

    accession_pseudo: dict[str, str] = {}
    path_pseudo: dict[str, str] = {}
    for i, acc in enumerate(accessions):
        pseudo = f"F_{i:04d}_{_short_hash(acc)}"
        accession_pseudo[acc] = pseudo
        # Also map the clean (no-dash) form that filenames use
        clean_acc = acc.replace("-", "")
        accession_pseudo[clean_acc] = pseudo
        # Map both .md and .html extensions
        path_pseudo[f"{clean_acc}.md"] = f"{pseudo}.md"
        path_pseudo[f"{clean_acc}.html"] = f"{pseudo}.html"

    return PseudonymPathMap(
        company_pseudonym=company_pseudo,
        ticker_pseudonym=ticker_pseudo,
        cik_pseudonym=cik_pseudo,
        accession_pseudonyms=accession_pseudo,
        path_pseudonyms=path_pseudo,
    )


def _short_hash(value: str) -> str:
    """Produce a short deterministic hash for pseudonyms."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def build_xbrl_cik_patterns(cik: str) -> list[tuple[str, str]]:
    """Build XBRL-specific CIK masking patterns.

    Returns list of (pattern, replacement) for XBRL context attributes.
    These patterns target CIK values embedded in XML/XBRL attributes
    that flat text regex cannot reach.
    """
    clean = cik.lstrip("0")
    padded = cik.zfill(10)
    pseudo = f"CIK_{_short_hash(cik)}"

    patterns: list[tuple[str, str]] = [
        # EntityCentralIndexKey in XBRL contexts
        (
            f'(EntityCentralIndexKey|entityCentralIndexKey)\\s*=\\s*"?{re.escape(clean)}"?',
            f'\\g<1>="{pseudo}"',
        ),
        (
            f'(EntityCentralIndexKey|entityCentralIndexKey)\\s*=\\s*"?{re.escape(padded)}"?',
            f'\\g<1>="{pseudo}"',
        ),
        # CIK in schemaRef and other URLs
        (f"cik={re.escape(clean)}", f"cik={pseudo}"),
        (f"cik={re.escape(padded)}", f"cik={pseudo}"),
        # CIK in context IDs
        (f"cik:{re.escape(clean)}", f"cik:{pseudo}"),
        (f"cik:{re.escape(padded)}", f"cik:{pseudo}"),
        # Bare CIK in XML text content
        (
            f"(>)\\s*{re.escape(clean)}\\s*(<)",
            f"\\g<1>{pseudo}\\g<2>",
        ),
        (
            f"(>)\\s*{re.escape(padded)}\\s*(<)",
            f"\\g<1>{pseudo}\\g<2>",
        ),
    ]
    return patterns
