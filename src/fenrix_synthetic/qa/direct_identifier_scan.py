"""Direct identifier scanner for V3 release boundary.

Scans files, paths, and ZIP entries for forbidden patterns including:
- CIK numbers
- SEC commission file numbers
- EIN identifiers
- CUSIP, ISIN, LEI
- SEC URLs and EDGAR paths
- XBRL namespace declarations
- Workiva/Wdesk metadata
- Source company names (from config)
- Source tickers (from config)
- Executive names (from config)

Returns structured ScanResult with hits and pass/fail status.
"""

from __future__ import annotations

import dataclasses
import re
import zipfile
from pathlib import Path
from typing import Any

# ── Pattern definitions ───────────────────────────────────────────────────

_PATTERNS: dict[str, tuple[str, str]] = {
    # SEC identifiers
    "CIK": (r"\bCIK\b|CIK_", "CIK keyword"),
    "entity_central_index_key": (
        r"EntityCentralIndexKey|Entity\s*Central\s*Index\s*Key",
        "EntityCentralIndexKey attribute",
    ),
    "commission_file_no": (
        r"Commission\s*File\s*(?:No|Number|No\.)",
        "Commission file number",
    ),
    "irs_employer_id": (
        r"IRS\s*Employer\s*Identification|Employer\s*Identification\s*(?:No|Number|No\.)",
        "IRS Employer Identification Number",
    ),
    "ein_keyword": (r"\bEIN\b", "EIN keyword"),
    "accession_number": (
        r"(?:[Aa]ccession|[Aa]ccession\s*[Nn]umber|[Aa]ccession\s*No\.)",
        "Accession number reference",
    ),
    # Financial identifiers
    "cusip": (r"\bCUSIP\b", "CUSIP identifier"),
    "isin": (r"\bISIN\b", "ISIN identifier"),
    "lei": (r"\bLEI\b[^a-z]", "LEI identifier"),
    # Exchange identifiers
    "nasdaq": (r"\bNASDAQ\b", "NASDAQ exchange"),
    "nyse": (r"\bNYSE\b", "NYSE exchange"),
    # SEC URLs and domains
    "sec_gov": (r"sec\.gov", "SEC.gov domain"),
    "data_sec_gov": (r"data\.sec\.gov", "SEC data API"),
    "xbrl_sec_gov": (r"xbrl\.sec\.gov", "XBRL SEC domain"),
    # XBRL taxonomies
    "us_gaap": (r"us-gaap[:\s]", "US GAAP taxonomy"),
    "dei_namespace": (r"dei:\s|dei_[A-Z]", "DEI namespace"),
    "ix_namespace": (r"ix:\s|ix_[A-Z]", "Inline XBRL namespace"),
    "ixt_namespace": (r"ixt:\s", "iXT namespace"),
    "xbrli_namespace": (r"xbrli:|xbrli_", "XBRL instance namespace"),
    "xbrldi_namespace": (r"xbrldi:|xbrldi_", "XBRL dimensions namespace"),
    "inline_xbrl": (r"inlineXBRL|inline\s*XBRL", "Inline XBRL reference"),
    "xbrl_document": (r"XBRL\s*Document", "XBRL Document reference"),
    # Vendor metadata
    "workiva": (r"\bWorkiva\b", "Workiva metadata"),
    "wdesk": (r"\bWdesk\b", "Wdesk metadata"),
    "sec_file": (r"SEC\s*[Ff]ile", "SEC file reference"),
    "central_index_key": (r"Central\s*Index\s*Key", "Central Index Key"),
}


def _build_dynamic_patterns(
    company_names: list[str] | None = None,
    tickers: list[str] | None = None,
    executive_names: list[str] | None = None,
    ciks: list[str] | None = None,
) -> dict[str, tuple[str, str]]:
    """Build additional patterns from config data.

    Only creates patterns for values that are long enough to be meaningful
    (>= 3 chars) and not too short to cause false positives.
    """
    dynamic: dict[str, tuple[str, str]] = {}
    for name in company_names or []:
        if len(name) >= 4:
            escaped = re.escape(name)
            dynamic[f"company_name:{name[:20]}"] = (escaped, "Source company name")
    for ticker in tickers or []:
        if len(ticker) >= 2 and len(ticker) <= 6:
            dynamic[f"ticker:{ticker}"] = (rf"\b{re.escape(ticker)}\b", "Source ticker")
    for name in executive_names or []:
        if len(name) >= 5:
            escaped = re.escape(name)
            dynamic[f"executive:{name[:20]}"] = (escaped, "Executive name")
    for cik in ciks or []:
        if len(cik) >= 7:
            dynamic[f"cik:{cik[:10]}"] = (re.escape(cik), "CIK number")
    return dynamic


@dataclasses.dataclass(frozen=True)
class ScanHit:
    """A single detected identifier hit."""

    path: str
    line: int | None
    pattern_id: str
    severity: str  # "blocking" or "warning"
    matched_text_preview: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "pattern_id": self.pattern_id,
            "severity": self.severity,
            "matched_text_preview": self.matched_text_preview,
        }


@dataclasses.dataclass(frozen=True)
class ScanResult:
    """Result of a direct identifier scan."""

    scanned_files: int
    scanned_bytes: int
    hits: list[ScanHit]
    passed: bool

    @property
    def blocking_hits(self) -> list[ScanHit]:
        return [h for h in self.hits if h.severity == "blocking"]

    @property
    def warning_hits(self) -> list[ScanHit]:
        return [h for h in self.hits if h.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_files": self.scanned_files,
            "scanned_bytes": self.scanned_bytes,
            "total_hits": len(self.hits),
            "blocking_hits": len(self.blocking_hits),
            "warning_hits": len(self.warning_hits),
            "passed": self.passed,
            "hits": [h.to_dict() for h in self.hits],
        }


def scan_path(
    root: Path,
    *,
    company_names: list[str] | None = None,
    tickers: list[str] | None = None,
    executive_names: list[str] | None = None,
    ciks: list[str] | None = None,
    scan_html_xml: bool = True,
) -> ScanResult:
    """Scan a directory tree for direct identifiers.

    Args:
        root: Root directory to scan.
        company_names: Source company names to detect.
        tickers: Source tickers to detect.
        executive_names: Executive names to detect.
        ciks: Known CIK numbers to detect.
        scan_html_xml: Whether to scan HTML/XML files (usually blocked from release).

    Returns:
        ScanResult with hits and pass/fail status.
    """
    all_patterns: dict[str, tuple[str, str]] = dict(_PATTERNS)
    dynamic = _build_dynamic_patterns(company_names, tickers, executive_names, ciks)
    all_patterns.update(dynamic)

    hits: list[ScanHit] = []
    scanned_files = 0
    scanned_bytes = 0
    text_extensions = {".md", ".json", ".csv", ".txt", ".yaml", ".yml", ".ini", ".cfg"}
    if scan_html_xml:
        text_extensions.update({".html", ".htm", ".xml", ".xhtml", ".xbrl"})

    # Pre-compile all patterns once
    compiled_patterns: dict[str, re.Pattern[str]] = {}
    for pid, (ptext, _) in all_patterns.items():
        try:
            compiled_patterns[pid] = re.compile(ptext, re.IGNORECASE)
        except re.error:
            pass

    for fp in sorted(root.rglob("*")):
        rel = str(fp.relative_to(root))

        # Scan file/directory paths for forbidden patterns
        for pattern_id, compiled in compiled_patterns.items():
            if compiled.search(rel):
                severity = _severity_for_pattern(pattern_id)
                hits.append(
                    ScanHit(
                        path=rel,
                        line=None,
                        pattern_id=pattern_id,
                        severity=severity,
                        matched_text_preview=rel[:80],
                    )
                )

        if not fp.is_file():
            continue
        suffix = fp.suffix.lower()
        if suffix not in text_extensions:
            continue

        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        scanned_files += 1
        scanned_bytes += len(content.encode("utf-8", errors="replace"))

        for pattern_id, compiled in compiled_patterns.items():
            for match in compiled.finditer(content):
                line_no = content[: match.start()].count("\n") + 1 if "\n" in content else None
                preview = match.group()[:80]
                severity = _severity_for_pattern(pattern_id)
                hits.append(
                    ScanHit(
                        path=rel,
                        line=line_no,
                        pattern_id=pattern_id,
                        severity=severity,
                        matched_text_preview=preview,
                    )
                )

    passed = len([h for h in hits if h.severity == "blocking"]) == 0
    return ScanResult(
        scanned_files=scanned_files,
        scanned_bytes=scanned_bytes,
        hits=hits,
        passed=passed,
    )


def scan_zip_entries(zip_path: Path) -> list[ScanHit]:
    """Scan ZIP entry names for forbidden patterns."""
    hits: list[ScanHit] = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                for pattern_id, (pattern_text, _desc) in _PATTERNS.items():
                    compiled = re.compile(pattern_text, re.IGNORECASE)
                    if compiled.search(name):
                        hits.append(
                            ScanHit(
                                path=f"zip:{name}",
                                line=None,
                                pattern_id=pattern_id,
                                severity=_severity_for_pattern(pattern_id),
                                matched_text_preview=name[:80],
                            )
                        )
    except (zipfile.BadZipFile, OSError):
        hits.append(
            ScanHit(
                path=str(zip_path),
                line=None,
                pattern_id="bad_zip",
                severity="blocking",
                matched_text_preview="Cannot read ZIP file",
            )
        )
    return hits


def _severity_for_pattern(pattern_id: str) -> str:
    """Determine severity for a pattern ID.

    Most patterns are blocking. Ticker patterns from dynamic matching
    are treated as blocking when they match standalone ticker names.
    """
    # All static patterns are blocking
    if pattern_id in _PATTERNS:
        return "blocking"
    # Dynamic patterns are blocking by default
    return "blocking"
