"""Archive ingestion module for pre-downloaded SEC filing archives.

Provides safe extraction with zip-slip protection, file-type detection,
filing-type heuristics, inventory generation, and private source archive management.

Entry point: ``ingest_source_archive()`` for one-shot ingestion.

Output tree (private only):
    private/source_archive/<run_tag>/
        raw/<relative-path>           — original files
        inventory/
            source_archive_inventory.json
            filing_coverage_by_company.json
            filing_coverage_by_year.json
        qa/archive_ingest_report.json

Never writes to public/.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────

FILING_TYPE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "10-K": [re.compile(r"10-K", re.I), re.compile(r"10k", re.I)],
    "10-Q": [re.compile(r"10-Q", re.I), re.compile(r"10q", re.I)],
    "8-K": [re.compile(r"8-K", re.I), re.compile(r"8k", re.I)],
    "20-F": [re.compile(r"20-F", re.I), re.compile(r"20f", re.I)],
    "DEF 14A": [re.compile(r"DEF\s*14A", re.I), re.compile(r"proxy", re.I)],
}

EXTENSION_CATEGORIES: dict[str, str] = {
    ".html": "html",
    ".htm": "html",
    ".txt": "text",
    ".xml": "xml",
    ".xbrl": "xbrl",
    ".json": "json",
    ".csv": "csv",
    ".pdf": "pdf",
}

YEAR_PATTERN = re.compile(r"(?:^|[^0-9])((?:19|20)\d{2})(?:[^0-9]|$)")
ACCESSION_PATTERN = re.compile(r"(\d{10}-\d{2}-\d{6})")
CIK_PATTERN = re.compile(r"(\d{10})")
CIK_SHORT_PATTERN = re.compile(r"CIK[_\s]*(\d{1,10})", re.I)

# Known company tickers from the source mapping (private)
_KNOWN_TICKERS = frozenset({"CL", "PEP", "TJX", "PM", "AMZN", "HBAN", "BLK", "GOOGL"})


# ── Data structures ────────────────────────────────────────────────────


class IngestionEntry:
    """Record for one file extracted from an archive."""

    __slots__ = (
        "relative_path",
        "file_hash",
        "size_bytes",
        "extension",
        "extension_category",
        "guessed_filing_type",
        "guessed_filing_year",
        "guessed_date_str",
        "accession_string",
        "cik_string",
        "has_private_identifier",
        "parsing_status",
    )

    def __init__(self, relative_path: str, file_hash: str, size_bytes: int) -> None:
        self.relative_path = relative_path
        self.file_hash = file_hash
        self.size_bytes = size_bytes

        # Detected fields
        ext = Path(relative_path).suffix.lower()
        self.extension = ext
        self.extension_category = EXTENSION_CATEGORIES.get(ext, "unknown")

        self.guessed_filing_type = _guess_filing_type(relative_path)
        self.guessed_filing_year = _guess_year(relative_path)
        self.guessed_date_str = ""

        self.accession_string = _extract_accession(relative_path)
        self.cik_string = _extract_cik(relative_path)
        self.has_private_identifier = _has_identifier_hint(relative_path)

        self.parsing_status = "inventoried"

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "file_hash": self.file_hash,
            "size_bytes": self.size_bytes,
            "extension": self.extension,
            "extension_category": self.extension_category,
            "guessed_filing_type": self.guessed_filing_type,
            "guessed_filing_year": self.guessed_filing_year,
            "accession_string": self.accession_string,
            "has_private_identifier_hint": self.has_private_identifier,
            "parsing_status": self.parsing_status,
        }


# ── Heuristic detectors ────────────────────────────────────────────────


def _guess_filing_type(path_str: str) -> str:
    """Guess SEC filing type from path/filename heuristics."""
    for ftype, patterns in FILING_TYPE_PATTERNS.items():
        for pat in patterns:
            if pat.search(path_str):
                return ftype
    # Fallback: check for accession number pattern which indicates SEC filing
    if ACCESSION_PATTERN.search(path_str):
        return "sec_filing_unknown_type"
    return "unknown"


def _guess_year(path_str: str) -> int | None:
    """Guess filing year from path."""
    for m in YEAR_PATTERN.finditer(path_str):
        year = int(m.group(1))
        if 1990 <= year <= 2030:
            return year
    return None


def _extract_accession(path_str: str) -> str | None:
    """Extract accession number from path if present."""
    m = ACCESSION_PATTERN.search(path_str)
    return m.group(1) if m else None


def _extract_cik(path_str: str) -> str | None:
    """Extract CIK-like identifier from path."""
    m = CIK_SHORT_PATTERN.search(path_str)
    if m:
        return m.group(1).zfill(10)
    m = CIK_PATTERN.search(path_str)
    if m and 7 <= len(m.group(1)) <= 10:
        return m.group(1).zfill(10)
    return None


def _has_identifier_hint(path_str: str) -> bool:
    """Check if path contains private identifier hints (accession, CIK patterns)."""
    if ACCESSION_PATTERN.search(path_str):
        return True
    if CIK_PATTERN.search(path_str) or CIK_SHORT_PATTERN.search(path_str):
        return True
    return False


def _ticker_from_path(path_str: str) -> str | None:
    """Extract ticker from path (e.g. Scott/AMZN/... → AMZN)."""
    parts = Path(path_str).parts
    for part in parts:
        if part.upper() in _KNOWN_TICKERS:
            return part.upper()
        if re.fullmatch(r"[A-Z]{1,5}", part.upper()):
            return part.upper()
    return None


# ── Zip-slip protection ────────────────────────────────────────────────


def _is_safe_path(destination: Path, member_path: str) -> bool:
    """Check that a ZIP member path does not escape the destination."""
    try:
        resolved = (destination / member_path).resolve()
        dest_resolved = destination.resolve()
        return resolved.is_relative_to(dest_resolved)
    except (ValueError, OSError):
        return False


def _reject_absolute_or_traversal(member_path: str) -> str | None:
    """Reject absolute paths or paths with '..' traversal. Returns cleaned path or None."""
    # Reject absolute paths
    if member_path.startswith("/") or member_path.startswith("\\"):
        return None
    # Reject traversal
    normalized = Path(member_path).as_posix()
    if ".." in normalized.split("/"):
        return None
    return normalized


# ── Main ingestion function ────────────────────────────────────────────


def ingest_source_archive(
    zip_path: Path,
    output_private_dir: Path,
    run_tag: str = "default",
    *,
    compute_full_hash: bool = True,
) -> dict[str, Any]:
    """Ingest a source ZIP archive into private storage.

    Steps:
    1. Compute archive SHA256.
    2. Validate ZIP integrity.
    3. Inventory all entries (with zip-slip protection).
    4. Extract safe entries to private/raw.
    5. Generate inventory and coverage reports.
    6. Generate ingestion QA report.

    Args:
        zip_path: Path to the ZIP archive.
        output_private_dir: Root private output directory.
        run_tag: Tag for this ingestion run (e.g. "scott_1").
        compute_full_hash: If True, compute full SHA256 of each extracted file.

    Returns:
        Ingestion report dict.
    """
    zip_path = Path(zip_path).resolve()
    if not zip_path.exists():
        raise FileNotFoundError(f"Archive not found: {zip_path}")

    # Compute archive hash
    archive_hash = _sha256_file(zip_path)
    archive_size = zip_path.stat().st_size

    # Output directories
    raw_dir = output_private_dir / run_tag / "raw"
    inv_dir = output_private_dir / run_tag / "inventory"
    qa_dir = output_private_dir / run_tag / "qa"

    entries: list[IngestionEntry] = []
    rejected_entries: list[dict[str, Any]] = []
    safe_extracted = 0
    rejected_count = 0

    # Open ZIP and process
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Test integrity
        bad_file = zf.testzip()
        if bad_file:
            raise zipfile.BadZipFile(f"Corrupt entry in archive: {bad_file}")

        for info in zf.infolist():
            if info.is_dir():
                continue

            member_path = info.filename

            # 1. Reject absolute paths and traversal
            cleaned = _reject_absolute_or_traversal(member_path)
            if cleaned is None:
                rejected_entries.append(
                    {
                        "relative_path": member_path,
                        "reason": "absolute_or_traversal",
                        "size_bytes": info.file_size,
                    }
                )
                rejected_count += 1
                continue

            # 2. Check zip-slip
            if not _is_safe_path(raw_dir, cleaned):
                rejected_entries.append(
                    {
                        "relative_path": cleaned,
                        "reason": "zip_slip",
                        "size_bytes": info.file_size,
                    }
                )
                rejected_count += 1
                continue

            # Read content
            content = zf.read(info.filename)
            file_hash = hashlib.sha256(content).hexdigest()

            # Create entry
            entry = IngestionEntry(
                relative_path=cleaned,
                file_hash=file_hash,
                size_bytes=info.file_size,
            )
            entries.append(entry)

            # Extract to raw private storage
            target = raw_dir / cleaned
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            safe_extracted += 1

    # ── Build inventory ────────────────────────────────────────────────
    inventory = [e.to_dict() for e in entries]
    inv_dir.mkdir(parents=True, exist_ok=True)

    inv_path = inv_dir / "source_archive_inventory.json"
    _write_json(inv_path, inventory)

    # ── Coverage by company/type/year ──────────────────────────────────
    by_company: dict[str, dict[str, Any]] = {}
    by_year: dict[int, int] = {}
    by_extension: dict[str, int] = {}
    by_filing_type: dict[str, int] = {}

    for e in entries:
        # By company (ticker from path)
        ticker = _ticker_from_path(e.relative_path) or "unknown"
        if ticker not in by_company:
            by_company[ticker] = {"total": 0, "by_form": {}, "by_year": {}}
        by_company[ticker]["total"] += 1

        ftype = e.guessed_filing_type or "unknown"
        if ftype not in by_company[ticker]["by_form"]:
            by_company[ticker]["by_form"][ftype] = 0
        by_company[ticker]["by_form"][ftype] += 1

        if e.guessed_filing_year:
            yr = e.guessed_filing_year
            by_year[yr] = by_year.get(yr, 0) + 1
            by_company[ticker]["by_year"][yr] = by_company[ticker]["by_year"].get(yr, 0) + 1

        by_extension[e.extension_category] = by_extension.get(e.extension_category, 0) + 1
        by_filing_type[ftype] = by_filing_type.get(ftype, 0) + 1

    coverage_company = inv_dir / "filing_coverage_by_company.json"
    _write_json(coverage_company, by_company)

    coverage_year = inv_dir / "filing_coverage_by_year.json"
    _write_json(coverage_year, by_year)

    # ── QA report ──────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "archive_path": str(zip_path),
        "archive_sha256": archive_hash,
        "archive_size_bytes": archive_size,
        "ingested_at": datetime.now(UTC).isoformat(),
        "run_tag": run_tag,
        "total_entries": len(entries),
        "safe_extracted": safe_extracted,
        "rejected_entries": rejected_count,
        "rejected_detail": rejected_entries[:50],
        "counts_by_extension_category": by_extension,
        "counts_by_filing_type": by_filing_type,
        "counts_by_year": by_year,
        "company_folders_detected": sorted(by_company.keys()),
        "identifier_flag_count": sum(1 for e in entries if e.has_private_identifier),
        "inventory_path": str(inv_path),
    }

    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_path = qa_dir / "archive_ingest_report.json"
    _write_json(qa_path, report)

    return report


# ── Helpers ────────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: Any) -> None:
    """Write JSON atomically."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


# ── Public helpers ─────────────────────────────────────────────────────


def load_inventory(inventory_path: Path) -> list[dict[str, Any]]:
    """Load a previously written inventory JSON."""
    import orjson

    return list(orjson.loads(inventory_path.read_bytes()))


def coverage_summary(report: dict[str, Any]) -> str:
    """Format a human-readable coverage summary from the ingestion report."""
    lines = [
        f"Archive: {report['archive_path']}",
        f"SHA256: {report['archive_sha256'][:16]}...",
        f"Total entries: {report['total_entries']}",
        f"Safe extracted: {report['safe_extracted']}",
        f"Rejected: {report['rejected_entries']}",
        "",
        "By extension category:",
    ]
    for ext, count in sorted(report.get("counts_by_extension_category", {}).items()):
        lines.append(f"  {ext}: {count}")
    lines.append("")
    lines.append("By filing type:")
    for ftype, count in sorted(report.get("counts_by_filing_type", {}).items()):
        lines.append(f"  {ftype}: {count}")
    lines.append("")
    lines.append("By year:")
    for year, count in sorted(report.get("counts_by_year", {}).items()):
        lines.append(f"  {year}: {count}")
    lines.append("")
    lines.append(f"Company folders detected: {report.get('company_folders_detected', [])}")
    return "\n".join(lines)
