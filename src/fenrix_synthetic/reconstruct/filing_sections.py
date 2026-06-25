"""Filing section extraction from private source materials.

Provides conservative detection of filing sections (10-K, 10-Q, 8-K, DEF14A)
from extracted text using pattern matching.
"""

from __future__ import annotations

import re

SECTION_BUSINESS = "annual_report_business"
SECTION_RISK_FACTORS = "annual_report_risk_factors"
SECTION_MDA = "annual_report_mda"
SECTION_FINANCIAL_SUMMARY = "annual_report_financial_summary"
SECTION_QUARTERLY = "quarterly_update_summary"
SECTION_MATERIAL_EVENTS = "material_events_summary"
SECTION_GOVERNANCE = "governance_proxy_summary"
SECTION_COVERAGE = "filing_coverage"

ALL_SECTION_TYPES: list[str] = [
    SECTION_BUSINESS,
    SECTION_RISK_FACTORS,
    SECTION_MDA,
    SECTION_FINANCIAL_SUMMARY,
    SECTION_QUARTERLY,
    SECTION_MATERIAL_EVENTS,
    SECTION_GOVERNANCE,
    SECTION_COVERAGE,
]


def detect_section_type(text: str) -> str:
    """Detect the section type from text content."""
    text_lower = text.lower()[:500]
    if re.search(r"item\s*1\.?\s*(business|description of business)", text_lower):
        return SECTION_BUSINESS
    if re.search(r"item\s*1a\.?\s*risk", text_lower):
        return SECTION_RISK_FACTORS
    if re.search(r"item\s*7\.?\s*(management|md.a)", text_lower):
        return SECTION_MDA
    if re.search(r"item\s*8\.?\s*financial", text_lower):
        return SECTION_FINANCIAL_SUMMARY
    if re.search(r"item\s*2\.?\s*(management|quarterly)", text_lower):
        return SECTION_QUARTERLY
    if re.search(r"item\s*[89]\.?\s*(other|material)", text_lower):
        return SECTION_MATERIAL_EVENTS
    if re.search(r"item\s*1[0123]\.?\s*|proxy", text_lower):
        return SECTION_GOVERNANCE
    return SECTION_BUSINESS


def extract_business_section(text: str) -> str:
    """Extract the Business section from filing text."""
    return _extract_between(text, [r"item\s*1\.?\s*(business|description)"], [r"item\s*1a\.?"])


def extract_risk_factors_section(text: str) -> str:
    """Extract the Risk Factors section from filing text."""
    return _extract_between(text, [r"item\s*1a\.?\s*risk"], [r"item\s*(2|7)\.?"])


def extract_mda_section(text: str) -> str:
    """Extract the MD&A section from filing text."""
    return _extract_between(text, [r"item\s*7\.?\s*(management|md.a)"], [r"item\s*8\.?"])


def extract_financial_summary_section(text: str) -> str:
    """Extract the Financial Summary section from filing text."""
    return _extract_between(text, [r"item\s*8\.?\s*financial"], [r"item\s*9\.?"])


def extract_quarterly_section(text: str) -> str:
    """Extract the Quarterly Update section (10-Q) from filing text."""
    return _extract_between(text, [r"item\s*2\.?\s*(management|quarterly)"], [r"item\s*(3|4)\.?"])


def extract_material_events_section(text: str) -> str:
    """Extract the Material Events section (8-K) from filing text."""
    return _extract_between(text, [r"item\s*[89]\.?"], [r"signatures?"])


def extract_governance_section(text: str) -> str:
    """Extract the Governance/Proxy section from filing text."""
    return _extract_between(text, [r"item\s*1[0123]\.?", r"proxy statement"], [r"signatures?"])


def _extract_between(text: str, start_pats: list[str], end_pats: list[str]) -> str:
    """Extract text between start and end markers."""
    start = len(text)
    for pat in start_pats:
        m = re.search(pat, text, re.IGNORECASE)
        if m and m.start() < start:
            start = m.start()
    if start >= len(text):
        return ""
    end = len(text)
    for pat in end_pats:
        m = re.search(pat, text[start + 1 :], re.IGNORECASE)
        if m:
            cand = start + 1 + m.start()
            if cand < end:
                end = cand
    return text[start:end].strip()
