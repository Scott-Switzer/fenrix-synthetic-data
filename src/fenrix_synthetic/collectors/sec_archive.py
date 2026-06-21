"""SEC archive importer for pre-downloaded filing archives.

Supports .zip, .tar.gz, and pre-extracted directories.
Never modifies the source archive.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tarfile
import zipfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..storage.atomic import atomic_write_bytes, atomic_write_json
from .base import CollectionStatus, CollectorResult

logger = logging.getLogger(__name__)


class SECArchiveMode(StrEnum):
    ARCHIVE_ONLY = "archive-only"
    ARCHIVE_PREFERRED = "archive-preferred"
    NETWORK_ONLY = "network-only"


# Patterns for detecting ticker, form, year from paths and filenames
_TICKER_PATTERNS = [
    re.compile(r"(?:^|[/\\])([A-Z]{1,5})(?:[/\\]|$)", re.IGNORECASE),
    re.compile(r"ticker[=_-]?([A-Z]{1,5})", re.IGNORECASE),
    re.compile(r"([A-Z]{1,5})[-_]", re.IGNORECASE),
]

_FORM_PATTERNS = [
    re.compile(r"(10-K|10-Q|8-K|20-F|6-K|S-1|S-3|8-K/A|10-K/A|10-Q/A)", re.IGNORECASE),
    re.compile(r"form[=_-]?(10-K|10-Q|8-K)", re.IGNORECASE),
]

_YEAR_PATTERNS = [
    re.compile(r"(?:^|[^0-9])((?:19|20)\d{2})(?:[^0-9]|$)"),
    re.compile(r"(?:^|[^0-9])(0[0-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])((?:19|20)\d{2})(?:[^0-9]|$)"),
]

_ACCESSION_PATTERNS = [
    re.compile(r"(\d{10}-\d{2}-\d{6})"),
    re.compile(r"(\d{18})"),
]

_PRIMARY_DOC_PATTERNS = re.compile(r"\.(?:html?|htm|txt|xml|xbrl|pdf)$", re.IGNORECASE)

_KNOWN_FORMS = frozenset({"10-K", "10-Q", "8-K", "20-F", "6-K", "S-1", "S-3"})


class ArchiveInventoryEntry:
    """Lightweight inventory record for one file in an archive."""

    __slots__ = (
        "relative_path",
        "content_hash",
        "size_bytes",
        "ticker",
        "form",
        "fiscal_year",
        "accession_number",
        "primary_document",
        "raw_path",
    )

    def __init__(
        self,
        relative_path: str,
        content_hash: str,
        size_bytes: int,
        ticker: str | None = None,
        form: str | None = None,
        fiscal_year: int | None = None,
        accession_number: str | None = None,
        primary_document: str | None = None,
        raw_path: Path | None = None,
    ) -> None:
        self.relative_path = relative_path
        self.content_hash = content_hash
        self.size_bytes = size_bytes
        self.ticker = ticker
        self.form = form
        self.fiscal_year = fiscal_year
        self.accession_number = accession_number
        self.primary_document = primary_document
        self.raw_path = raw_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "ticker": self.ticker,
            "form": self.form,
            "fiscal_year": self.fiscal_year,
            "accession_number": self.accession_number,
            "primary_document": self.primary_document,
        }

    def is_filing_html(self) -> bool:
        """True if this entry looks like an SEC filing HTML document."""
        if self.form and self.accession_number and self.primary_document:
            return True
        if self.relative_path.lower().endswith((".html", ".htm")):
            return True
        return False


def _detect_ticker(path_str: str) -> str | None:
    """Heuristically extract a ticker from a path or filename."""
    for pattern in _TICKER_PATTERNS:
        m = pattern.search(path_str)
        if m:
            candidate = m.group(1).upper()
            if 1 <= len(candidate) <= 5 and candidate.isalpha():
                return candidate
    return None


def _detect_form(path_str: str) -> str | None:
    """Heuristically extract an SEC form type from a path."""
    for pattern in _FORM_PATTERNS:
        m = pattern.search(path_str)
        if m:
            candidate = m.group(1).upper().rstrip("/A")
            if candidate in _KNOWN_FORMS:
                return candidate
    return None


def _detect_fiscal_year(path_str: str) -> int | None:
    """Heuristically extract a fiscal year from a path."""
    for pattern in _YEAR_PATTERNS:
        for m in pattern.finditer(path_str):
            year = int(m.group(1) if len(m.groups()) == 1 else m.group(3))
            if 1990 <= year <= 2030:
                return year
    return None


def _detect_accession(path_str: str) -> str | None:
    """Heuristically extract an SEC accession number from a path."""
    for pattern in _ACCESSION_PATTERNS:
        m = pattern.search(path_str)
        if m:
            return m.group(1)
    return None


class SECArchiveCollector:
    """Inventory and extract SEC filings from a local archive.

    Never modifies the source archive.
    """

    def __init__(
        self,
        archive_path: Path,
        output_dir: Path,
        ticker: str | None = None,
        forms: list[str] | None = None,
        years: int = 20,
    ) -> None:
        if not archive_path.exists():
            raise FileNotFoundError(f"Archive not found: {archive_path}")
        self.archive_path = archive_path.resolve()
        self.output_dir = output_dir.resolve()
        self.ticker = ticker.upper() if ticker else None
        self.forms = [f.upper() for f in (forms or ["10-K", "10-Q", "8-K"])]
        self.years = years
        self.parser_version = "sec_archive_v1"
        self._inventory: list[ArchiveInventoryEntry] | None = None
        self._inventory_report: dict[str, Any] | None = None

    # ── Inventory ────────────────────────────────────────────────────

    def inventory(self) -> list[ArchiveInventoryEntry]:
        """Build inventory without loading full content into memory.

        Returns cached result on subsequent calls.
        """
        if self._inventory is not None:
            return self._inventory

        entries: list[ArchiveInventoryEntry] = []

        if self.archive_path.is_dir():
            entries = self._inventory_directory(self.archive_path)
        elif self.archive_path.suffix.lower() == ".zip":
            entries = self._inventory_zip(self.archive_path)
        elif (
            self.archive_path.suffix.lower() in (".gz", ".tgz")
            or ".tar." in self.archive_path.name.lower()
        ):
            entries = self._inventory_tar(self.archive_path)
        else:
            logger.warning("Unknown archive format: %s", self.archive_path.suffix)

        self._inventory = entries
        self._inventory_report = self._build_inventory_report(entries)
        return entries

    def _inventory_directory(self, root: Path) -> list[ArchiveInventoryEntry]:
        entries: list[ArchiveInventoryEntry] = []
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            try:
                stat = file_path.stat()
                size = stat.st_size
                # Quick hash via first/last 64KB + size for speed
                content_hash = self._quick_hash(file_path)
                rel = str(file_path.relative_to(root))
                entry = ArchiveInventoryEntry(
                    relative_path=rel,
                    content_hash=content_hash,
                    size_bytes=size,
                    ticker=_detect_ticker(rel),
                    form=_detect_form(rel),
                    fiscal_year=_detect_fiscal_year(rel),
                    accession_number=_detect_accession(rel),
                    primary_document=file_path.name if _PRIMARY_DOC_PATTERNS.search(rel) else None,
                    raw_path=file_path,
                )
                entries.append(entry)
            except OSError:
                continue
        return entries

    def _inventory_zip(self, archive_path: Path) -> list[ArchiveInventoryEntry]:
        entries: list[ArchiveInventoryEntry] = []
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                content_hash = hashlib.sha256(
                    f"{info.CRC}:{info.file_size}:{name}".encode()
                ).hexdigest()
                entries.append(
                    ArchiveInventoryEntry(
                        relative_path=name,
                        content_hash=content_hash,
                        size_bytes=info.file_size,
                        ticker=_detect_ticker(name),
                        form=_detect_form(name),
                        fiscal_year=_detect_fiscal_year(name),
                        accession_number=_detect_accession(name),
                        primary_document=Path(name).name
                        if _PRIMARY_DOC_PATTERNS.search(name)
                        else None,
                        raw_path=None,  # Needs extraction from zip to read
                    )
                )
        return entries

    def _inventory_tar(self, archive_path: Path) -> list[ArchiveInventoryEntry]:
        entries: list[ArchiveInventoryEntry] = []
        mode = (
            "r:gz"
            if archive_path.name.endswith(".gz") or archive_path.name.endswith(".tgz")
            else "r:*"
        )
        with tarfile.open(str(archive_path), mode) as tf:  # type: ignore[call-overload]
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = member.name
                content_hash = hashlib.sha256(f"{member.size}:{name}".encode()).hexdigest()
                entries.append(
                    ArchiveInventoryEntry(
                        relative_path=name,
                        content_hash=content_hash,
                        size_bytes=member.size,
                        ticker=_detect_ticker(name),
                        form=_detect_form(name),
                        fiscal_year=_detect_fiscal_year(name),
                        accession_number=_detect_accession(name),
                        primary_document=Path(name).name
                        if _PRIMARY_DOC_PATTERNS.search(name)
                        else None,
                        raw_path=None,
                    )
                )
        return entries

    def _quick_hash(self, path: Path, chunk_size: int = 65536) -> str:
        """Fast hash using first/last chunk + file size."""
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                head = f.read(chunk_size)
                if size > chunk_size * 2:
                    f.seek(-chunk_size, 2)
                    tail = f.read(chunk_size)
                else:
                    tail = b""
            return hashlib.sha256(head + tail + str(size).encode()).hexdigest()
        except OSError:
            return ""

    def _build_inventory_report(self, entries: list[ArchiveInventoryEntry]) -> dict[str, Any]:
        """Produce aggregate inventory report by ticker, form, year."""
        by_ticker: dict[str, int] = {}
        by_form: dict[str, int] = {}
        by_year: dict[int, int] = {}
        filing_entries = 0
        total_files = len(entries)
        total_bytes = 0

        for e in entries:
            total_bytes += e.size_bytes
            ticker = e.ticker or "unknown"
            by_ticker[ticker] = by_ticker.get(ticker, 0) + 1
            if e.form:
                by_form[e.form] = by_form.get(e.form, 0) + 1
                filing_entries += 1
            if e.fiscal_year:
                by_year[e.fiscal_year] = by_year.get(e.fiscal_year, 0) + 1

        return {
            "archive_path": str(self.archive_path),
            "total_files": total_files,
            "total_bytes": total_bytes,
            "filing_entries": filing_entries,
            "files_by_ticker": by_ticker,
            "files_by_form": by_form,
            "files_by_year": dict(sorted(by_year.items())),
            "archive_type": (
                "directory"
                if self.archive_path.is_dir()
                else "zip"
                if self.archive_path.suffix.lower() == ".zip"
                else "tar"
            ),
        }

    def inventory_report(self) -> dict[str, Any]:
        """Return the inventory report (runs inventory if needed)."""
        if self._inventory_report is None:
            self.inventory()
        return self._inventory_report or {}

    # ── Collection ───────────────────────────────────────────────────

    def collect(self) -> list[CollectorResult]:
        """Extract and normalize filings from the archive.

        Deduplicates by content hash and accession number.
        Filters to the requested ticker, forms, and date range.
        """
        if self._inventory is None:
            self.inventory()
        assert self._inventory is not None

        end_date = datetime.now(UTC)
        start_date = datetime(end_date.year - self.years, end_date.month, end_date.day, tzinfo=UTC)
        start_year = start_date.year

        # Filter and deduplicate
        seen_hashes: set[str] = set()
        seen_accessions: set[str] = set()
        filing_entries: list[ArchiveInventoryEntry] = []

        for e in self._inventory:
            # Ticker filter
            if self.ticker and e.ticker and e.ticker.upper() != self.ticker:
                continue
            # Form filter
            if e.form and e.form.upper() not in self.forms:
                continue
            # Year filter
            if e.fiscal_year and e.fiscal_year < start_year:
                continue
            # Deduplicate by hash
            if e.content_hash in seen_hashes:
                continue
            seen_hashes.add(e.content_hash)
            # Deduplicate by accession
            if e.accession_number and e.accession_number in seen_accessions:
                continue
            if e.accession_number:
                seen_accessions.add(e.accession_number)
            filing_entries.append(e)

        results: list[CollectorResult] = []

        # Save filing inventory
        inv_path = self.output_dir / "sec" / "archive_filing_inventory.json"
        inv_path.parent.mkdir(parents=True, exist_ok=True)
        inv_data = [e.to_dict() for e in filing_entries]
        atomic_write_json(inv_path, inv_data)
        results.append(
            CollectorResult(
                source="sec_archive",
                artifact_type="filing_inventory",
                status=CollectionStatus.SUCCESS if filing_entries else CollectionStatus.UNAVAILABLE,
                requested_range=(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
                observed_range=(None, None),
                row_count=len(filing_entries),
                fetch_timestamp=datetime.now(UTC).isoformat(),
                parser_version=self.parser_version,
                content_type="application/json",
                relative_path=str(inv_path.relative_to(self.output_dir.parent)),
                byte_size=inv_path.stat().st_size,
                sha256=hashlib.sha256(inv_path.read_bytes()).hexdigest(),
                metadata={
                    "forms": self.forms,
                    "archive_path": str(self.archive_path),
                    "dedup_by_hash": len(seen_hashes),
                    "dedup_by_accession": len(seen_accessions),
                },
            )
        )

        # Extract and normalize filings
        processed_count = 0
        failed_count = 0
        for e in filing_entries:
            try:
                content = self._read_entry_content(e)
                if content is None:
                    failed_count += 1
                    continue

                # Save raw
                raw_name = (
                    e.accession_number.replace("-", "")
                    if e.accession_number
                    else Path(e.relative_path).stem
                )
                raw_path = self.output_dir / "sec" / "filings" / f"{raw_name}.html"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(raw_path, content)

                # Normalize text using HtmlFilingExtractor
                from ..extraction.converter import HtmlFilingExtractor

                extractor = HtmlFilingExtractor()
                text_result = extractor.extract(
                    content.decode("utf-8", errors="replace"),
                    metadata={
                        "source": "sec_archive",
                        "archive_path": str(self.archive_path),
                        "archive_relative_path": e.relative_path,
                    },
                )
                text_path = self.output_dir / "sec" / "filings" / f"{raw_name}.md"
                text_path.write_text(text_result["text"], encoding="utf-8")

                processed_count += 1
            except Exception as exc:
                logger.warning("Archive extraction failed for %s: %s", e.relative_path, exc)
                failed_count += 1

        results.append(
            CollectorResult(
                source="sec_archive",
                artifact_type="filing_documents",
                status=CollectionStatus.SUCCESS
                if processed_count > 0
                else CollectionStatus.UNAVAILABLE,
                requested_range=(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
                observed_range=(None, None),
                row_count=processed_count,
                fetch_timestamp=datetime.now(UTC).isoformat(),
                parser_version=self.parser_version,
                content_type="text/html",
                relative_path=str(self.output_dir / "sec" / "filings"),
                byte_size=0,
                sha256="",
                metadata={
                    "processed_count": processed_count,
                    "failed_count": failed_count,
                    "forms": self.forms,
                },
            )
        )

        return results

    def _read_entry_content(self, entry: ArchiveInventoryEntry) -> bytes | None:
        """Read the content of an archive entry."""
        # Direct file access
        if entry.raw_path is not None and entry.raw_path.exists():
            return entry.raw_path.read_bytes()

        # Extract from archive
        if self.archive_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(self.archive_path, "r") as zf:
                try:
                    return zf.read(entry.relative_path)
                except (KeyError, zipfile.BadZipFile):
                    return None
        elif (
            self.archive_path.suffix.lower() in (".gz", ".tgz")
            or ".tar." in self.archive_path.name.lower()
        ):
            mode = (
                "r:gz"
                if self.archive_path.name.endswith(".gz") or self.archive_path.name.endswith(".tgz")
                else "r:*"
            )
            with tarfile.open(str(self.archive_path), mode) as tf:  # type: ignore[call-overload]
                try:
                    extracted = tf.extractfile(entry.relative_path)
                    if extracted is not None:
                        return extracted.read()  # type: ignore[no-any-return]
                except (KeyError, tarfile.TarError):
                    return None

        return None

    # ── Coverage report ──────────────────────────────────────────────

    def coverage_report(self) -> dict[str, Any]:
        """Produce archive-coverage report by ticker, form, and year."""
        if self._inventory is None:
            self.inventory()
        assert self._inventory is not None

        by_ticker_form_year: dict[str, dict[str, dict[int, int]]] = {}
        for e in self._inventory:
            ticker = e.ticker or "unknown"
            form = e.form or "unknown"
            year = e.fiscal_year or 0
            by_ticker_form_year.setdefault(ticker, {}).setdefault(form, {})
            by_ticker_form_year[ticker][form][year] = (
                by_ticker_form_year[ticker][form].get(year, 0) + 1
            )

        # Flatten for readability
        coverage: dict[str, Any] = {
            "archive_path": str(self.archive_path),
            "total_files": len(self._inventory),
            "by_ticker": {},
        }
        for ticker, forms in sorted(by_ticker_form_year.items()):
            ticker_total = sum(sum(years.values()) for years in forms.values())
            coverage["by_ticker"][ticker] = {
                "total": ticker_total,
                "by_form": {},
            }
            for form, years in sorted(forms.items()):
                form_total = sum(years.values())
                coverage["by_ticker"][ticker]["by_form"][form] = {
                    "total": form_total,
                    "by_year": dict(sorted(years.items())),
                }

        return coverage

    @staticmethod
    def supported_archive(path: Path) -> bool:
        """Check whether the path looks like a supported archive format."""
        if path.is_dir():
            return True
        suffix = path.suffix.lower()
        name = path.name.lower()
        return suffix in (".zip", ".gz", ".tgz") or ".tar." in name
