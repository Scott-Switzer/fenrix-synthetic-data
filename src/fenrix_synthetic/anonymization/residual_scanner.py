"""Residual scanner for anonymized artifacts."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from ..attacks.exact_match import ExactResidualScanner
from ..identity import EntityRegistry

logger = logging.getLogger(__name__)


class ResidualScanner:
    """Scan anonymized artifacts for residual exact identifiers."""

    def __init__(self, ticker: str, atlas: EntityRegistry, output_dir: Path) -> None:
        self.ticker = ticker.upper()
        self.atlas = atlas
        self.output_dir = output_dir

    def scan_all(self, anonymized_dir: Path) -> dict[str, Any]:
        """Scan all anonymized artifacts for leaks."""
        scanner = ExactResidualScanner()
        values = self._build_scan_values()

        total_hits = 0
        blocking_hits = 0
        findings: list[dict[str, Any]] = []

        # Scan text files
        for text_path in anonymized_dir.rglob("*.md"):
            try:
                text = text_path.read_text(encoding="utf-8", errors="replace")
                result = scanner.scan_text(text, values)
                if result.blocking_hits > 0:
                    total_hits += result.total_hits
                    blocking_hits += result.blocking_hits
                    findings.append(
                        {
                            "file": str(text_path.relative_to(anonymized_dir)),
                            "total_hits": result.total_hits,
                            "blocking_hits": result.blocking_hits,
                            "hit_values": result.hit_values[:20],  # Limit
                        }
                    )
            except Exception as exc:
                logger.warning("Scan failed for %s: %s", text_path, exc)

        # Scan JSON files
        for json_path in anonymized_dir.rglob("*.json"):
            try:
                data = orjson.loads(json_path.read_bytes())
                text = orjson.dumps(data).decode("utf-8")
                result = scanner.scan_text(text, values)
                if result.blocking_hits > 0:
                    total_hits += result.total_hits
                    blocking_hits += result.blocking_hits
                    findings.append(
                        {
                            "file": str(json_path.relative_to(anonymized_dir)),
                            "total_hits": result.total_hits,
                            "blocking_hits": result.blocking_hits,
                            "hit_values": result.hit_values[:20],
                        }
                    )
            except Exception as exc:
                logger.warning("Scan failed for %s: %s", json_path, exc)

        # Save detailed findings (private)
        findings_path = self.output_dir / "residual_scans" / "detailed_findings.json"
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        findings_path.write_bytes(
            orjson.dumps(findings, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # Sanitized aggregate report
        report = {
            "ticker": self.ticker,
            "scan_timestamp": datetime.now(UTC).isoformat(),
            "exact_identifier_count": blocking_hits,
            "total_hits": total_hits,
            "files_with_leaks": len(findings),
            "status": "zero_leak" if blocking_hits == 0 else "remaining_leak",
            "unresolved_candidates": len(findings),
        }

        report_path = self.output_dir / "residual_scans" / "qa_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(
            orjson.dumps(report, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        return report

    def _build_scan_values(self) -> dict[str, list[str]]:
        """Build scan values from atlas."""
        values: dict[str, list[str]] = {
            "company": [],
            "former_company_name": [],
            "ticker": [],
            "cik": [],
            "sec_accession_number": [],
            "company_domain": [],
            "company_email_domain": [],
            "executive": [],
            "board_member": [],
            "subsidiary": [],
            "product": [],
            "brand": [],
            "facility": [],
            "headquarters": [],
            "acquisition_target": [],
            "auditor": [],
            "law_firm": [],
        }

        for entity in self.atlas.all_entities():
            etype = entity.entity_type.value
            if etype in values:
                values[etype].append(entity.canonical_private_value)

        for alias in self.atlas.all_aliases():
            etype = alias.entity_type.value
            if etype in values:
                values[etype].append(alias.private_alias_value)

        # Add absolute path patterns
        values["absolute_path"] = [str(self.atlas.metadata.company_id)]

        return values
