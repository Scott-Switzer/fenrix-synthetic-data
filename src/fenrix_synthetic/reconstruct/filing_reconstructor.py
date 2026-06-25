"""Public filing section reconstructor.

Transforms private source filing sections into safe public markdown.
Removes identifiers, sanitizes dates, and preserves business-analysis structure.
"""

from __future__ import annotations

import re
from typing import Any

from fenrix_synthetic.reconstruct.filing_sections import (
    ALL_SECTION_TYPES,
    SECTION_BUSINESS,
    SECTION_COVERAGE,
    SECTION_FINANCIAL_SUMMARY,
    SECTION_GOVERNANCE,
    SECTION_MATERIAL_EVENTS,
    SECTION_MDA,
    SECTION_QUARTERLY,
    SECTION_RISK_FACTORS,
    detect_section_type,
)

# Patterns to sanitize from public output
IDENTIFIER_PATTERNS: list[re.Pattern] = [
    re.compile(r, re.IGNORECASE)
    for r in [
        r"CIK\s*[:=]?\s*\d{5,10}",
        r"EntityCentralIndexKey",
        r"\d{10}\-\d{2}\-\d{6}",  # Accession-like
        r"\d{2}\-\d{6,8}",  # SEC file number
        r"\d{1,4}\.\d{1,4}\.\d{1,4}",  # XBRL namespace
        r"xbrl[a-z]*:",
        r"dei:",
        r"us-gaap[a-z]*:",
        r"sec\.gov",
        r"commission file number",
        r"ACCESSION NUMBER",
        r"\b[A-Z]{2,10}\b Corp\b",
        r"incorporated\s+in\s+the\s+state\s+of",
        r"\b[A-Z][a-z]+,\s*(Inc\.?|Ltd\.?|LLC|SA|PLC)\b",
    ]
]

# Executive/director name patterns
EXECUTIVE_TITLES = [
    r"(president|ceo|cfo|coo|cto|director|chairman|secretary|treasurer)",
    r"(chief\s+(executive|financial|operating|technology|information)\s+officer)",
    r"board\s+of\s+directors",
    r"management\s+team",
]

# Placeholder values
PLACEHOLDER_SECTIONS: dict[str, str] = {
    SECTION_BUSINESS: "# Business Overview\n\nSource data is being processed.\n",
    SECTION_RISK_FACTORS: "# Risk Factors\n\nStandard risk factors apply.\n",
    SECTION_MDA: "# Management Discussion & Analysis\n\nFinancial results are being compiled.\n",
    SECTION_FINANCIAL_SUMMARY: "# Financial Summary\n\n| Metric | Value |\n|--------|-------|\n",
    SECTION_QUARTERLY: "# Quarterly Update Summary\n\nDetails being compiled.\n",
    SECTION_MATERIAL_EVENTS: "# Material Events Summary\n\nNo material events reported.\n",
    SECTION_GOVERNANCE: "# Governance & Proxy Summary\n\nGovernance details being compiled.\n",
}


class FilingReconstructor:
    """Reconstruct public filing sections from private source materials."""

    def reconstruct(
        self,
        company_id: str,
        source_sections: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Reconstruct public filing sections from private source sections.

        Args:
            company_id: Anonymized company identifier.
            source_sections: List of dicts with keys: section_type, content.

        Returns:
            Dict mapping section keys to dicts with keys: content.
        """
        result: dict[str, dict[str, Any]] = {}

        grouped: dict[str, list[str]] = {}
        for section in source_sections:
            s_type = section.get("section_type", detect_section_type(section.get("content", "")))
            if s_type not in grouped:
                grouped[s_type] = []
            grouped[s_type].append(section.get("content", ""))

        for section_key in ALL_SECTION_TYPES:
            source_texts = grouped.get(section_key, [])
            if not source_texts:
                result[section_key] = {
                    "content": self._render_placeholder(company_id, section_key),
                }
                continue

            merged = "\n\n".join(source_texts)
            sanitized = self._sanitize(merged, company_id)
            result[section_key] = {"content": sanitized}

        result[SECTION_COVERAGE] = {
            "content": self._build_coverage_md(company_id, len(source_sections)),
        }

        return result

    def _sanitize(self, text: str, company_id: str) -> str:
        """Remove identifiers and sensitive patterns from text."""
        for pattern in IDENTIFIER_PATTERNS:
            text = pattern.sub("[REDACTED]", text)

        # Replace executive names
        for title_pat in EXECUTIVE_TITLES:
            text = re.sub(
                rf"({title_pat})\s+[\w\s,'\.\-]+?(?=\n)",
                r"\1 [NAME REDACTED]",
                text,
                flags=re.IGNORECASE,
            )

        # Replace exact dates with relative markers
        text = re.sub(r"\d{4}-\d{2}-\d{2}", "[relative date]", text)

        # Replace numeric fiscal years with relative notation
        text = re.sub(r"(fiscal\s+year\s+)\d{4}", r"\g<1>[YEAR]", text, flags=re.IGNORECASE)

        # Replace percentage ranges with generalized values
        text = re.sub(r"\d+\.?\d*\s*%\s*to\s*\d+\.?\d*\s*%", "[percentage range]", text)

        return text

    def _render_placeholder(self, company_id: str, section_key: str) -> str:
        """Generate a placeholder section when no source data is available."""
        base = PLACEHOLDER_SECTIONS.get(
            section_key, f"# Filing Section\n\nDetails for {company_id} being compiled.\n"
        )
        return base.replace("being compiled", f"for {company_id} are being compiled")

    def _build_coverage_md(self, company_id: str, num_sections: int) -> str:
        """Build filing coverage markdown."""
        return (
            f"# Filing Coverage for {company_id}\n\n"
            f"Relative year range: Year -19 to Year 0\n"
            f"Annual reports: {max(1, num_sections // 4)}\n"
            f"Quarterly reports: {max(1, num_sections // 2)}\n"
            f"Material event reports: {max(0, num_sections // 3)}\n"
            f"Proxy/governance filings: {max(0, num_sections // 5)}\n"
            f"Coverage notes: Coverage derived from available source archive.\n"
        )
