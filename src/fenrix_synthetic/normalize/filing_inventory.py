"""Filing inventory normalization.

Builds a normalized filing inventory from an archive ingestion report,
providing coverage maps, deduplication, and safe metadata extraction.

All outputs remain private. No source identifiers leak into public paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..sources.archive_ingest import load_inventory


@dataclass
class FilingRecord:
    """Normalized filing record with safe metadata only."""

    record_id: str  # opaque hash-based ID
    relative_path: str
    file_hash: str
    size_bytes: int
    extension_category: str
    guessed_filing_type: str
    guessed_filing_year: int | None
    has_identifier_hint: bool
    source_archive_tag: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "relative_path": self.relative_path,
            "file_hash": self.file_hash,
            "size_bytes": self.size_bytes,
            "extension_category": self.extension_category,
            "guessed_filing_type": self.guessed_filing_type,
            "guessed_filing_year": self.guessed_filing_year,
            "has_identifier_hint": self.has_identifier_hint,
            "source_archive_tag": self.source_archive_tag,
        }


@dataclass
class CoverageMap:
    """Coverage map for filings across years and types."""

    total_records: int = 0
    by_year: dict[int, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    by_extension: dict[str, int] = field(default_factory=dict)
    years_span: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total_records,
            "by_year": self.by_year,
            "by_type": self.by_type,
            "by_extension": self.by_extension,
            "years_span": self.years_span,
        }


class FilingInventory:
    """Normalized filing inventory from archive ingestion."""

    def __init__(self, records: list[FilingRecord], source_tag: str) -> None:
        self.records = records
        self.source_tag = source_tag
        self._coverage: CoverageMap | None = None

    @classmethod
    def from_archive_ingestion(
        cls,
        ingestion_report: dict[str, Any],
        inventory_path: Path,
    ) -> FilingInventory:
        """Build inventory from an archive ingestion report and inventory JSON."""
        raw_entries = load_inventory(inventory_path)
        source_tag = ingestion_report.get("run_tag", "unknown")

        records: list[FilingRecord] = []
        seen_hashes: set[str] = set()

        for entry in raw_entries:
            file_hash = entry.get("file_hash", "")
            if file_hash in seen_hashes:
                continue
            seen_hashes.add(file_hash)

            # Build opaque record ID from hash of path + hash
            record_id = _hash_record_id(entry.get("relative_path", ""), file_hash)

            records.append(
                FilingRecord(
                    record_id=record_id,
                    relative_path=entry.get("relative_path", ""),
                    file_hash=file_hash,
                    size_bytes=entry.get("size_bytes", 0),
                    extension_category=entry.get("extension_category", "unknown"),
                    guessed_filing_type=entry.get("guessed_filing_type", "unknown"),
                    guessed_filing_year=entry.get("guessed_filing_year"),
                    has_identifier_hint=entry.get("has_private_identifier_hint", False),
                    source_archive_tag=source_tag,
                )
            )

        return cls(records, source_tag)

    def coverage(self) -> CoverageMap:
        """Compute coverage statistics."""
        if self._coverage is not None:
            return self._coverage

        cmap = CoverageMap(total_records=len(self.records))
        years: set[int] = set()

        for rec in self.records:
            cmap.by_type[rec.guessed_filing_type] = cmap.by_type.get(rec.guessed_filing_type, 0) + 1
            cmap.by_extension[rec.extension_category] = (
                cmap.by_extension.get(rec.extension_category, 0) + 1
            )
            if rec.guessed_filing_year is not None:
                years.add(rec.guessed_filing_year)
                cmap.by_year[rec.guessed_filing_year] = (
                    cmap.by_year.get(rec.guessed_filing_year, 0) + 1
                )

        if years:
            cmap.years_span = (min(years), max(years))

        self._coverage = cmap
        return cmap

    def records_for_year(self, year: int) -> list[FilingRecord]:
        """Get all records for a specific year."""
        return [r for r in self.records if r.guessed_filing_year == year]

    def records_for_type(self, filing_type: str) -> list[FilingRecord]:
        """Get all records for a specific filing type."""
        return [r for r in self.records if r.guessed_filing_type == filing_type]

    def records_with_identifiers(self) -> list[FilingRecord]:
        """Get records that have identifier hints (private only)."""
        return [r for r in self.records if r.has_identifier_hint]

    def deduplicated_count(self) -> int:
        """Return count of unique records (by hash)."""
        return len(self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_tag": self.source_tag,
            "total_records": len(self.records),
            "records": [r.to_dict() for r in self.records],
            "coverage": self.coverage().to_dict(),
        }

    def save(self, path: Path) -> None:
        """Save inventory to JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")


def _hash_record_id(relative_path: str, file_hash: str) -> str:
    """Create an opaque record ID from path and file hash."""
    import hashlib

    return hashlib.sha256(f"{relative_path}:{file_hash}".encode()).hexdigest()[:16]
