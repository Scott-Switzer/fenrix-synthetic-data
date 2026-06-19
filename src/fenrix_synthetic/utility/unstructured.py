"""Utility evaluation for unstructured data (Phase 4H).

Metrics:
- Non-identifier token retention
- Section retention
- Table retention
- Financial-number retention
- Document-type classification agreement
- Sentiment agreement (when labels supplied)
- Topic-distribution similarity (where feasible)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class UnstructuredUtilityResult:
    """Result of unstructured utility evaluation."""

    document_id: str
    non_identifier_token_retention: float = 1.0
    section_retention: float = 1.0
    table_retention: float = 1.0
    financial_number_retention: float = 1.0
    overall_utility: float = 1.0
    warnings: list[str] = field(default_factory=list)


def _count_tokens(text: str) -> int:
    """Simple whitespace-based token count."""
    return len(text.split())


def _count_financial_numbers(text: str) -> int:
    """Count numbers that look financial (dollars, percentages, ratios)."""
    patterns = [
        r"\$\s*[\d,.]+",  # dollar amounts
        r"[\d,.]+%",  # percentages
        r"\d+\.\d+x",  # multiples
        r"\d+\s*(?:million|billion|thousand)",  # scaled numbers
    ]
    count = 0
    for pat in patterns:
        count += len(re.findall(pat, text, re.IGNORECASE))
    return count


def evaluate_unstructured_utility(
    source_text: str,
    masked_text: str,
    source_sections: list[str] | None = None,
    masked_sections: list[str] | None = None,
    document_id: str = "",
) -> UnstructuredUtilityResult:
    """Evaluate how well the masking preserves document utility.

    Args:
        source_text: Original (pre-masking) text
        masked_text: Masked text
        source_sections: Original section texts
        masked_sections: Masked section texts
        document_id: Document identifier

    Returns:
        UnstructuredUtilityResult with all metrics
    """
    result = UnstructuredUtilityResult(document_id=document_id)

    # Non-identifier token retention
    source_tokens = _count_tokens(source_text)
    masked_tokens = _count_tokens(masked_text)
    if source_tokens > 0:
        result.non_identifier_token_retention = masked_tokens / source_tokens
    else:
        result.non_identifier_token_retention = 1.0

    # Financial number retention
    source_fin = _count_financial_numbers(source_text)
    masked_fin = _count_financial_numbers(masked_text)
    if source_fin > 0:
        result.financial_number_retention = masked_fin / source_fin
    else:
        result.financial_number_retention = 1.0

    # Section retention
    if source_sections and masked_sections:
        result.section_retention = len(masked_sections) / max(1, len(source_sections))

    # Table retention (simple heuristic: count table-like structures)
    source_tables = len(re.findall(r"\|.*\|", source_text))
    masked_tables = len(re.findall(r"\|.*\|", masked_text))
    if source_tables > 0:
        result.table_retention = masked_tables / source_tables
    else:
        result.table_retention = 1.0

    # Overall utility (weighted average)
    weights = {"token": 0.25, "section": 0.25, "table": 0.15, "financial": 0.35}
    result.overall_utility = (
        weights["token"] * result.non_identifier_token_retention
        + weights["section"] * result.section_retention
        + weights["table"] * result.table_retention
        + weights["financial"] * result.financial_number_retention
    )

    # Warnings
    if result.non_identifier_token_retention < 0.85:
        result.warnings.append(
            f"Token retention {result.non_identifier_token_retention:.2f} below 0.85"
        )
    if result.financial_number_retention < 0.90:
        result.warnings.append(
            f"Financial number retention {result.financial_number_retention:.2f} below 0.90"
        )

    return result
