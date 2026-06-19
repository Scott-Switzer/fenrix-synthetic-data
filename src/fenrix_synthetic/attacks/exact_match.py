from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScanHit:
    value: str
    location: str
    start: int = 0
    context: str = ""


@dataclass
class ScanResult:
    total_hits: int = 0
    blocking_hits: int = 0
    allowed_hits: int = 0
    hits_by_type: dict[str, list[ScanHit]] = field(default_factory=dict)
    is_blocked: bool = False
    hit_values: list[str] = field(default_factory=list)

    def add_hit(self, hit_type: str, hit: ScanHit, is_blocking: bool = True) -> None:
        self.hits_by_type.setdefault(hit_type, []).append(hit)
        self.total_hits += 1
        self.hit_values.append(hit.value)
        if is_blocking:
            self.blocking_hits += 1
            self.is_blocked = True
        else:
            self.allowed_hits += 1


class ExactResidualScanner:
    def scan_text(
        self,
        text: str,
        values: dict[str, list[str]],
    ) -> ScanResult:
        result = ScanResult()
        for scan_type, value_list in values.items():
            for value in value_list:
                blocking = self._is_blocking(scan_type, value)
                matches = self._find_matches(text, value, scan_type)
                for match in matches:
                    result.add_hit(
                        scan_type,
                        match,
                        is_blocking=blocking,
                    )
        return result

    def scan_metadata(
        self,
        metadata: dict[str, Any],
        values: dict[str, list[str]],
    ) -> ScanResult:
        text = self._metadata_to_text(metadata)
        return self.scan_text(text, values)

    def scan_document(
        self,
        masked_text: str,
        masked_metadata: dict[str, Any],
        registry_values: dict[str, list[str]],
    ) -> ScanResult:
        text_result = self.scan_text(masked_text, registry_values)
        meta_result = self.scan_metadata(masked_metadata, registry_values)

        combined = ScanResult()
        combined.total_hits = text_result.total_hits + meta_result.total_hits
        combined.blocking_hits = text_result.blocking_hits + meta_result.blocking_hits
        combined.allowed_hits = text_result.allowed_hits + meta_result.allowed_hits
        combined.is_blocked = combined.blocking_hits > 0
        combined.hits_by_type = dict(text_result.hits_by_type)
        for k, v in meta_result.hits_by_type.items():
            combined.hits_by_type.setdefault(k, []).extend(v)
        combined.hit_values = text_result.hit_values + meta_result.hit_values
        return combined

    def _find_matches(self, text: str, value: str, scan_type: str) -> list[ScanHit]:
        hits: list[ScanHit] = []
        lower_text = text.lower()
        lower_value = value.lower().strip()
        if not lower_value:
            return hits

        patterns = self._patterns_for_value(value, scan_type)
        for _pattern_name, pattern in patterns:
            try:
                for match in re.finditer(pattern, text):
                    start = match.start()
                    ctx_start = max(0, start - 20)
                    ctx_end = min(len(text), start + len(match.group()) + 20)
                    context = text[ctx_start:ctx_end].replace("\n", " ")
                    hits.append(
                        ScanHit(
                            value=match.group(),
                            location=scan_type,
                            start=start,
                            context=context,
                        )
                    )
            except re.error:
                if lower_value in lower_text:
                    idx = lower_text.find(lower_value)
                    while idx != -1:
                        ctx_start = max(0, idx - 20)
                        ctx_end = min(len(text), idx + len(value) + 20)
                        context = text[ctx_start:ctx_end].replace("\n", " ")
                        hits.append(
                            ScanHit(
                                value=text[idx : idx + len(value)],
                                location=scan_type,
                                start=idx,
                                context=context,
                            )
                        )
                        idx = lower_text.find(lower_value, idx + 1)
        return hits

    def _patterns_for_value(self, value: str, scan_type: str) -> list[tuple[str, str]]:
        escaped = re.escape(value)
        patterns: list[tuple[str, str]] = []
        if scan_type == "cik":
            patterns.append(("cik_padded", build_cik_padded_pattern(value)))
        elif scan_type == "sec_accession_number":
            patterns.append(("accession_url", build_accession_url_pattern(value)))
            patterns.append(("literal", escaped))
        elif scan_type in ("company_domain", "company_email_domain"):
            patterns.append(("url", build_domain_url_pattern(value)))
            patterns.append(("email", build_email_pattern(value)))
            patterns.append(("literal", f"\\b{escaped}\\b"))
        else:
            patterns.append(("literal", escaped))
        if scan_type in ("company", "former_company_name"):
            patterns.append(("possessive", f"(?:{escaped})'s\\b"))
        if scan_type == "ticker":
            patterns.append(("ticker_exchange", build_ticker_exchange_pattern(value)))
            patterns.append(("ticker_parenthesized", build_ticker_parenthesized_pattern(value)))
        return patterns

    def _is_blocking(self, scan_type: str, value: str) -> bool:
        blocking_types = {
            "company",
            "former_company_name",
            "ticker",
            "cik",
            "sec_accession_number",
            "sec_primary_document",
            "company_domain",
            "company_email_domain",
            "canary",
        }
        return scan_type in blocking_types

    @staticmethod
    def _metadata_to_text(metadata: dict[str, Any]) -> str:
        parts: list[str] = []
        for key, val in metadata.items():
            parts.append(f"{key}: {val}")
        return "\n".join(parts)


def build_ticker_exchange_pattern(ticker: str) -> str:
    escaped = re.escape(ticker.upper())
    return f"(?:NYSE|NASDAQ|NYSE\\s*Arca)\\s*:\\s*{escaped}\\b"


def build_ticker_parenthesized_pattern(ticker: str) -> str:
    return f"\\({re.escape(ticker.upper())}\\)"


def build_cik_padded_pattern(cik: str) -> str:
    clean = cik.lstrip("0")
    return f"CIK\\s*#?\\s*0*{re.escape(clean)}\\b|\\b0*{re.escape(clean)}\\b"


def build_accession_url_pattern(accession: str) -> str:
    no_dashes = accession.replace("-", "")
    return f"{re.escape(no_dashes)}|{re.escape(accession)}"


def build_domain_url_pattern(domain: str) -> str:
    escaped = re.escape(domain)
    return f"https?://(?:www\\.)?{escaped}[^\\s]*|{escaped}"


def build_email_pattern(domain: str) -> str:
    return f"[\\w.+-]+@{re.escape(domain)}"
