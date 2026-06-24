"""Metadata scanner for V3 release boundary.

Detects hidden/structural SEC/iXBRL artifacts that should never appear
in professor-facing release artifacts:
- HTML/XML declarations
- XBRL namespace URIs and tags
- SEC header blocks and metadata
- DEI, US-GAAP, SRT, COUNTRY taxonomy namespaces
- contextRef, unitRef, schemaRef artifacts
- Hidden IXBRL tags (ix:hidden, ix:header, etc.)
- Document metatada (FiscalYearFocus, PeriodEndDate, TradingSymbol)
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any


# ── Metadata pattern groups ────────────────────────────────────────────────

_HTML_XML_DECLARATIONS: list[tuple[str, str, str]] = [
    ("html_declaration", r"<html[\s>]", "HTML document declaration"),
    ("xml_declaration", r"<\?xml\s", "XML declaration"),
    ("doctype_html", r"<!DOCTYPE\s+html", "HTML DOCTYPE"),
]

_XMLNS_PATTERNS: list[tuple[str, str, str]] = [
    ("xmlns_attribute", r'xmlns[^=]*=["\']([^"\']*sec\.gov[^"\']*)', "SEC XML namespace"),
    ("xsi_attribute", r"xsi:[^=]+=", "XSI namespace attribute"),
    ("xlink_attribute", r"xlink:[^=]+=", "XLINK namespace attribute"),
    ("xmlns_dei", r'xmlns[^=]*=["\']([^"\']*dei[^"\']*)', "DEI namespace declaration"),
    ("xmlns_us_gaap", r'xmlns[^=]*=["\']([^"\']*us-gaap[^"\']*)', "US-GAAP namespace"),
    ("xmlns_srt", r'xmlns[^=]*=["\']([^"\']*srt[^"\']*)', "SRT namespace"),
    ("xmlns_country", r'xmlns[^=]*=["\']([^"\']*country[^"\']*)', "Country namespace"),
]

_XBRL_TAG_PATTERNS: list[tuple[str, str, str]] = [
    ("ix_namespace_tag", r"<(ix:|ix_)\w", "Inline XBRL tag"),
    ("ixt_namespace_tag", r"<(ixt:|ixt_)\w", "iXT tag"),
    ("xbrli_tag", r"<(xbrli:|xbrli_)\w", "XBRL instance tag"),
    ("xbrldi_tag", r"<(xbrldi:|xbrldi_)\w", "XBRL dimensions tag"),
    ("dei_tag", r"<(dei:|dei_)\w", "DEI element tag"),
    ("us_gaap_tag", r"<(us-gaap:|us_gaap_)\w", "US-GAAP element tag"),
    ("srt_tag", r"<(srt:|srt_)\w", "SRT element tag"),
    ("ix_hidden", r"<(ix:hidden|ix_hidden)", "Hidden IXBRL section"),
    ("ix_header", r"<(ix:header|ix_header)", "IXBRL header"),
    ("ix_non_numeric", r"<(ix:nonNumeric|ix_nonNumeric)", "IXBRL nonNumeric"),
    ("ix_non_fraction", r"<(ix:nonFraction|ix_nonFraction)", "IXBRL nonFraction"),
    ("ix_references", r"<(ix:references|ix_references)", "IXBRL references"),
    ("ix_resources", r"<(ix:resources|ix_resources)", "IXBRL resources"),
    ("ix_relationship", r"<(ix:relationship|ix_relationship)", "IXBRL relationship"),
]

_XBRL_ATTRIBUTES: list[tuple[str, str, str]] = [
    ("context_ref", r'contextRef\s*=\s*["\']', "XBRL contextRef"),
    ("unit_ref", r'unitRef\s*=\s*["\']', "XBRL unitRef"),
    ("schema_ref", r'xsi:schemaLocation\s*=\s*["\']', "XBRL schemaRef"),
    ("linkbase_ref", r'xlink:href\s*=\s*["\']', "XBRL linkbase ref"),
    ("decimals_attr", r'decimals\s*=\s*["\']', "XBRL decimals attribute"),
    ("scale_attr", r'scale\s*=\s*["\']', "XBRL scale attribute"),
    ("sign_attr", r'sign\s*=\s*["\']', "XBRL sign attribute"),
    ("format_attr", r'format\s*=\s*["\']ixt:', "iXT format attribute"),
]

_SEC_METADATA: list[tuple[str, str, str]] = [
    ("document_fiscal_year_focus", r"DocumentFiscalYearFocus", "Fiscal year focus metadata"),
    ("document_period_end_date", r"DocumentPeriodEndDate", "Period end date metadata"),
    ("trading_symbol", r"TradingSymbol", "Trading symbol metadata"),
    ("entity_registrant_name", r"EntityRegistrantName", "Registrant name metadata"),
    ("entity_central_index_key_attr", r"EntityCentralIndexKey", "CIK metadata attribute"),
    ("document_type", r"DocumentType", "Document type metadata"),
    ("document_annual_report", r"DocumentAnnualReport", "Annual report flag"),
    ("current_fiscal_year_end", r"CurrentFiscalYearEndDate", "Fiscal year end metadata"),
    ("entity_filer_category", r"EntityFilerCategory", "Filer category metadata"),
    ("entity_common_stock_shares", r"EntityCommonStockSharesOutstanding", "Share count metadata"),
    ("document_transition_report", r"DocumentTransitionReport", "Transition report flag"),
    ("entity_well_known_seasoned_issuer", r"EntityWellKnownSeasonedIssuer", "WKSI flag"),
    ("entity_voluntary_filers", r"EntityVoluntaryFilers", "Voluntary filer flag"),
    ("entity_current_reporting_status", r"EntityCurrentReportingStatus", "Reporting status"),
    ("document_fiscal_period_focus", r"DocumentFiscalPeriodFocus", "Fiscal period metadata"),
    ("amendment_flag", r"AmendmentFlag", "Amendment flag"),
    ("document_registrant_name", r"DocumentRegistrantName", "Registrant name"),
]

_ADDITIONAL_LEAK_PATTERNS: list[tuple[str, str, str]] = [
    ("edgar_data_path", r"Archives/edgar/data/\d+", "EDGAR data path"),
    ("cik_in_url", r"cik[=:/]\d+", "CIK in URL"),
    ("accession_dashed", r"\d{10}-\d{2}-\d{6}", "Dashed accession number"),
    ("accession_clean", r"\b\d{18}\b", "Accession number (clean)"),
    ("sec_archive_url", r"sec\.gov/Archives/", "SEC archive URL"),
    ("sec_cgi_bin_url", r"sec\.gov/cgi-bin/", "SEC CGI URL"),
    ("sec_ix_url", r"sec\.gov/ix\?", "SEC IX viewer URL"),
]

ALL_METADATA_PATTERNS: list[tuple[str, str, str]] = (
    _HTML_XML_DECLARATIONS
    + _XMLNS_PATTERNS
    + _XBRL_TAG_PATTERNS
    + _XBRL_ATTRIBUTES
    + _SEC_METADATA
    + _ADDITIONAL_LEAK_PATTERNS
)


@dataclasses.dataclass(frozen=True)
class MetadataHit:
    """A single detected metadata artifact."""

    path: str
    pattern_id: str
    pattern_category: str  # html_xml, xmlns, xbrl_tag, xbrl_attr, sec_metadata, additional
    matched_text_preview: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "pattern_id": self.pattern_id,
            "pattern_category": self.pattern_category,
            "matched_text_preview": self.matched_text_preview,
        }


@dataclasses.dataclass(frozen=True)
class MetadataScanResult:
    """Result of a metadata scan."""

    scanned_files: int
    scanned_bytes: int
    hits: list[MetadataHit]
    passed: bool

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_files": self.scanned_files,
            "scanned_bytes": self.scanned_bytes,
            "total_hits": self.hit_count,
            "passed": self.passed,
            "hits_by_category": self._hits_by_category(),
            "hits": [h.to_dict() for h in self.hits],
        }

    def _hits_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for h in self.hits:
            counts[h.pattern_category] = counts.get(h.pattern_category, 0) + 1
        return counts


def scan_metadata(root: Path, *, scan_html_xml_files: bool = False) -> MetadataScanResult:
    """Scan a directory tree for SEC/iXBRL metadata artifacts.

    Args:
        root: Root directory to scan.
        scan_html_xml_files: If False, .html/.xml files are noted but not
            content-scanned (they are forbidden by default in release).

    Returns:
        MetadataScanResult with hits and pass/fail status.
    """
    hits: list[MetadataHit] = []
    scanned_files = 0
    scanned_bytes = 0

    # Compile all patterns with categories
    compiled: list[tuple[str, str, re.Pattern[str]]] = []
    pattern_categories: dict[str, str] = {}
    for pid, ptext, _desc in _HTML_XML_DECLARATIONS:
        compiled.append((pid, "html_xml", re.compile(ptext, re.IGNORECASE)))
        pattern_categories[pid] = "html_xml"
    for pid, ptext, _desc in _XMLNS_PATTERNS:
        compiled.append((pid, "xmlns", re.compile(ptext, re.IGNORECASE)))
        pattern_categories[pid] = "xmlns"
    for pid, ptext, _desc in _XBRL_TAG_PATTERNS:
        compiled.append((pid, "xbrl_tag", re.compile(ptext, re.IGNORECASE)))
        pattern_categories[pid] = "xbrl_tag"
    for pid, ptext, _desc in _XBRL_ATTRIBUTES:
        compiled.append((pid, "xbrl_attr", re.compile(ptext, re.IGNORECASE)))
        pattern_categories[pid] = "xbrl_attr"
    for pid, ptext, _desc in _SEC_METADATA:
        compiled.append((pid, "sec_metadata", re.compile(ptext, re.IGNORECASE)))
        pattern_categories[pid] = "sec_metadata"
    for pid, ptext, _desc in _ADDITIONAL_LEAK_PATTERNS:
        compiled.append((pid, "additional", re.compile(ptext, re.IGNORECASE)))
        pattern_categories[pid] = "additional"

    text_extensions = {".md", ".json", ".csv", ".txt", ".yaml", ".yml"}
    if scan_html_xml_files:
        text_extensions.update({".html", ".htm", ".xml", ".xhtml", ".xbrl"})
    else:
        # Note HTML/XML files as hits without content scanning
        for fp in sorted(root.rglob("*")):
            if fp.is_file() and fp.suffix.lower() in {".html", ".htm", ".xml", ".xhtml", ".xbrl"}:
                hits.append(
                    MetadataHit(
                        path=str(fp.relative_to(root)),
                        pattern_id="html_xml_present",
                        pattern_category="html_xml",
                        matched_text_preview=f"File type: {fp.suffix}",
                    )
                )

    for fp in sorted(root.rglob("*")):
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
        rel = str(fp.relative_to(root))

        for pid, category, pat in compiled:
            if pat.search(content):
                match = pat.search(content)
                assert match is not None
                preview = match.group()[:100]
                hits.append(
                    MetadataHit(
                        path=rel,
                        pattern_id=pid,
                        pattern_category=category,
                        matched_text_preview=preview,
                    )
                )

    passed = len(hits) == 0
    return MetadataScanResult(
        scanned_files=scanned_files,
        scanned_bytes=scanned_bytes,
        hits=hits,
        passed=passed,
    )
