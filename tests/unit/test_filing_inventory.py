"""Tests for filing inventory normalization module."""

from __future__ import annotations

from pathlib import Path

from fenrix_synthetic.normalize.filing_inventory import (
    FilingInventory,
    FilingRecord,
    _hash_record_id,
)


def test_hash_record_id_deterministic() -> None:
    h1 = _hash_record_id("AMZN/2024/10-K.txt", "abc123")
    h2 = _hash_record_id("AMZN/2024/10-K.txt", "abc123")
    h3 = _hash_record_id("GOOGL/2024/10-K.txt", "abc123")
    assert h1 == h2
    assert len(h1) == 16
    assert h1 != h3


def test_filing_record_to_dict() -> None:
    rec = FilingRecord(
        record_id="abc123",
        relative_path="AMZN/2024/10-K.txt",
        file_hash="deadbeef",
        size_bytes=1024,
        extension_category="text",
        guessed_filing_type="10-K",
        guessed_filing_year=2024,
        has_identifier_hint=True,
        source_archive_tag="test",
    )
    d = rec.to_dict()
    assert d["record_id"] == "abc123"
    assert d["guessed_filing_type"] == "10-K"
    assert d["guessed_filing_year"] == 2024
    assert d["has_identifier_hint"] is True


def test_inventory_coverage(tmp_path: Path) -> None:
    records = [
        FilingRecord(
            record_id="r1",
            relative_path="AMZN/2024/10-K.txt",
            file_hash="h1",
            size_bytes=100,
            extension_category="text",
            guessed_filing_type="10-K",
            guessed_filing_year=2024,
            has_identifier_hint=False,
            source_archive_tag="test",
        ),
        FilingRecord(
            record_id="r2",
            relative_path="AMZN/2024/10-Q.txt",
            file_hash="h2",
            size_bytes=100,
            extension_category="text",
            guessed_filing_type="10-Q",
            guessed_filing_year=2024,
            has_identifier_hint=False,
            source_archive_tag="test",
        ),
        FilingRecord(
            record_id="r3",
            relative_path="AMZN/2023/10-K.txt",
            file_hash="h3",
            size_bytes=100,
            extension_category="text",
            guessed_filing_type="10-K",
            guessed_filing_year=2023,
            has_identifier_hint=False,
            source_archive_tag="test",
        ),
    ]
    inv = FilingInventory(records, "test")
    cov = inv.coverage()

    assert cov.total_records == 3
    assert cov.by_year[2024] == 2
    assert cov.by_year[2023] == 1
    assert cov.by_type["10-K"] == 2
    assert cov.by_type["10-Q"] == 1
    assert cov.years_span == (2023, 2024)


def test_inventory_records_for_year() -> None:
    records = [
        FilingRecord(
            record_id="r1",
            relative_path="AMZN/2024/10-K.txt",
            file_hash="h1",
            size_bytes=100,
            extension_category="text",
            guessed_filing_type="10-K",
            guessed_filing_year=2024,
            has_identifier_hint=False,
            source_archive_tag="test",
        ),
        FilingRecord(
            record_id="r2",
            relative_path="AMZN/2023/10-K.txt",
            file_hash="h2",
            size_bytes=100,
            extension_category="text",
            guessed_filing_type="10-K",
            guessed_filing_year=2023,
            has_identifier_hint=False,
            source_archive_tag="test",
        ),
    ]
    inv = FilingInventory(records, "test")
    assert len(inv.records_for_year(2024)) == 1
    assert len(inv.records_for_year(2023)) == 1
    assert len(inv.records_for_year(2022)) == 0


def test_inventory_records_with_identifiers() -> None:
    records = [
        FilingRecord(
            record_id="r1",
            relative_path="AMZN/2024/10-K.txt",
            file_hash="h1",
            size_bytes=100,
            extension_category="text",
            guessed_filing_type="10-K",
            guessed_filing_year=2024,
            has_identifier_hint=True,
            source_archive_tag="test",
        ),
        FilingRecord(
            record_id="r2",
            relative_path="AMZN/2024/10-Q.txt",
            file_hash="h2",
            size_bytes=100,
            extension_category="text",
            guessed_filing_type="10-Q",
            guessed_filing_year=2024,
            has_identifier_hint=False,
            source_archive_tag="test",
        ),
    ]
    inv = FilingInventory(records, "test")
    flagged = inv.records_with_identifiers()
    assert len(flagged) == 1
    assert flagged[0].record_id == "r1"


def test_inventory_save_and_load(tmp_path: Path) -> None:
    records = [
        FilingRecord(
            record_id="r1",
            relative_path="AMZN/2024/10-K.txt",
            file_hash="h1",
            size_bytes=100,
            extension_category="text",
            guessed_filing_type="10-K",
            guessed_filing_year=2024,
            has_identifier_hint=False,
            source_archive_tag="test",
        ),
    ]
    inv = FilingInventory(records, "test")
    path = tmp_path / "inventory.json"
    inv.save(path)
    assert path.exists()

    data = inv.to_dict()
    assert data["source_tag"] == "test"
    assert data["total_records"] == 1
    assert data["coverage"]["total_records"] == 1
