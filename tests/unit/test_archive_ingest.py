"""Tests for archive ingestion module."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from fenrix_synthetic.sources.archive_ingest import (
    IngestionEntry,
    _is_safe_path,
    _reject_absolute_or_traversal,
    _ticker_from_path,
    ingest_source_archive,
)

# ── Fixtures ───────────────────────────────────────────────────────────


def _make_test_zip(entries: dict[str, bytes]) -> bytes:
    """Create an in-memory ZIP from a dict of filename -> content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.fixture
def sample_archive(tmp_path: Path) -> Path:
    """Create a sample ZIP archive with mock filings."""
    entries = {
        "AMZN/2024/10-K_0001018724-24-000129.txt": b"Mock 10-K filing for AMZN 2024\n",
        "AMZN/2024/10-Q_0001018724-24-000130.txt": b"Mock 10-Q filing for AMZN 2024\n",
        "AMZN/2023/10-K_0001018724-23-000100.txt": b"Mock 10-K filing for AMZN 2023\n",
        "GOOGL/2024/10-K_0001652044-24-000021.txt": b"Mock 10-K filing for GOOGL 2024\n",
        "README.txt": b"This is a readme file\n",
        "metadata.csv": b"year,ticker,form\n2024,AMZN,10-K\n",
    }
    path = tmp_path / "test_archive.zip"
    path.write_bytes(_make_test_zip(entries))
    return path


# ── Zip-slip protection ────────────────────────────────────────────────


def test_reject_absolute_path() -> None:
    assert _reject_absolute_or_traversal("/etc/passwd") is None
    assert _reject_absolute_or_traversal("\\Windows\\System32") is None


def test_reject_traversal_path() -> None:
    assert _reject_absolute_or_traversal("../../etc/passwd") is None
    assert _reject_absolute_or_traversal("foo/../../../etc/passwd") is None
    # Conservative: any '..' in path is rejected for zip-slip safety
    assert _reject_absolute_or_traversal("foo/bar/../baz") is None


def test_accept_normal_path() -> None:
    assert _reject_absolute_or_traversal("AMZN/2024/10-K.txt") == "AMZN/2024/10-K.txt"
    assert _reject_absolute_or_traversal("foo/bar/baz.html") == "foo/bar/baz.html"


def test_is_safe_path_blocks_zip_slip(tmp_path: Path) -> None:
    dest = tmp_path / "extract"
    dest.mkdir()
    # Normal path
    assert _is_safe_path(dest, "AMZN/2024/10-K.txt") is True
    # Zip-slip path
    assert _is_safe_path(dest, "../outside.txt") is False
    assert _is_safe_path(dest, "AMZN/../../outside.txt") is False


# ── IngestionEntry ─────────────────────────────────────────────────────


def test_ingestion_entry_detects_10k() -> None:
    entry = IngestionEntry("AMZN/2024/10-K_0001234567-24-000001.txt", "abc123", 1024)
    assert entry.guessed_filing_type == "10-K"
    assert entry.extension_category == "text"
    assert entry.guessed_filing_year == 2024


def test_ingestion_entry_detects_10q() -> None:
    entry = IngestionEntry("PEP/2023/10-Q_filing.txt", "def456", 512)
    assert entry.guessed_filing_type == "10-Q"
    assert entry.guessed_filing_year == 2023


def test_ingestion_entry_unknown_type() -> None:
    entry = IngestionEntry("misc/README.txt", "ghi789", 256)
    assert entry.guessed_filing_type == "unknown"
    assert entry.extension_category == "text"


def test_ingestion_entry_html_extension() -> None:
    entry = IngestionEntry("BLK/2022/10-K.html", "jkl012", 2048)
    assert entry.extension_category == "html"


def test_ingestion_entry_accession_detection() -> None:
    entry = IngestionEntry("AMZN/2024/10-K_0001018724-24-000129.txt", "mno345", 1024)
    assert entry.accession_string == "0001018724-24-000129"
    assert entry.has_private_identifier is True


# ── Ticker extraction ──────────────────────────────────────────────────


def test_ticker_from_path_known() -> None:
    assert _ticker_from_path("AMZN/2024/10-K.txt") == "AMZN"
    assert _ticker_from_path("GOOGL/2023/10-Q.txt") == "GOOGL"


def test_ticker_from_path_unknown() -> None:
    assert _ticker_from_path("unknown_folder/file.txt") is None


# ── Full ingestion ─────────────────────────────────────────────────────


def test_ingest_source_archive_basic(tmp_path: Path, sample_archive: Path) -> None:
    output_dir = tmp_path / "private"
    report = ingest_source_archive(
        zip_path=sample_archive,
        output_private_dir=output_dir,
        run_tag="test_run",
    )

    assert report["total_entries"] == 6
    assert report["safe_extracted"] == 6
    assert report["rejected_entries"] == 0
    assert report["run_tag"] == "test_run"
    assert len(report["archive_sha256"]) == 64

    # Check raw files extracted
    raw_dir = output_dir / "test_run" / "raw"
    assert (raw_dir / "AMZN" / "2024" / "10-K_0001018724-24-000129.txt").exists()
    assert (raw_dir / "GOOGL" / "2024" / "10-K_0001652044-24-000021.txt").exists()

    # Check inventory written
    inv_path = output_dir / "test_run" / "inventory" / "source_archive_inventory.json"
    assert inv_path.exists()

    # Check coverage reports
    cov_company = output_dir / "test_run" / "inventory" / "filing_coverage_by_company.json"
    assert cov_company.exists()
    cov_year = output_dir / "test_run" / "inventory" / "filing_coverage_by_year.json"
    assert cov_year.exists()

    # Check QA report
    qa_path = output_dir / "test_run" / "qa" / "archive_ingest_report.json"
    assert qa_path.exists()


def test_ingest_source_archive_rejects_traversal(tmp_path: Path) -> None:
    entries = {
        "AMZN/2024/10-K.txt": b"safe file\n",
        "../../outside.txt": b"malicious file\n",
        "foo/../../../etc/passwd": b"another malicious file\n",
    }
    archive_path = tmp_path / "bad_archive.zip"
    archive_path.write_bytes(_make_test_zip(entries))

    output_dir = tmp_path / "private"
    report = ingest_source_archive(
        zip_path=archive_path,
        output_private_dir=output_dir,
        run_tag="test_run",
    )

    assert report["safe_extracted"] == 1
    assert report["rejected_entries"] == 2
    assert len(report["rejected_detail"]) == 2

    raw_dir = output_dir / "test_run" / "raw"
    assert (raw_dir / "AMZN" / "2024" / "10-K.txt").exists()
    assert not (tmp_path / "outside.txt").exists()


def test_ingest_source_archive_counts_by_type(tmp_path: Path, sample_archive: Path) -> None:
    output_dir = tmp_path / "private"
    report = ingest_source_archive(
        zip_path=sample_archive,
        output_private_dir=output_dir,
        run_tag="test_run",
    )

    assert report["counts_by_filing_type"]["10-K"] == 3
    assert report["counts_by_filing_type"]["10-Q"] == 1
    assert report["counts_by_extension_category"]["text"] == 5
    assert report["counts_by_extension_category"]["csv"] == 1


def test_ingest_source_archive_counts_by_year(tmp_path: Path, sample_archive: Path) -> None:
    output_dir = tmp_path / "private"
    report = ingest_source_archive(
        zip_path=sample_archive,
        output_private_dir=output_dir,
        run_tag="test_run",
    )

    assert report["counts_by_year"][2024] == 3
    assert report["counts_by_year"][2023] == 1


def test_ingest_source_archive_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ingest_source_archive(
            zip_path=tmp_path / "nonexistent.zip",
            output_private_dir=tmp_path / "private",
            run_tag="test",
        )


def test_ingest_source_archive_deterministic_hashes(tmp_path: Path, sample_archive: Path) -> None:
    output_dir1 = tmp_path / "private1"
    output_dir2 = tmp_path / "private2"

    report1 = ingest_source_archive(
        zip_path=sample_archive,
        output_private_dir=output_dir1,
        run_tag="run1",
    )
    report2 = ingest_source_archive(
        zip_path=sample_archive,
        output_private_dir=output_dir2,
        run_tag="run2",
    )

    assert report1["archive_sha256"] == report2["archive_sha256"]

    # Check individual file hashes match
    raw1 = output_dir1 / "run1" / "raw" / "README.txt"
    raw2 = output_dir2 / "run2" / "raw" / "README.txt"
    assert raw1.read_bytes() == raw2.read_bytes()
