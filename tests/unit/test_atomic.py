"""Unit tests for atomic write utilities."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fenrix_synthetic.storage.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_parquet,
)


class TestAtomicWriteBytes:
    """Test atomic_write_bytes."""

    def test_write_bytes(self, temp_dir: Path):
        target = temp_dir / "test.bin"
        content = b"Hello, World!"
        atomic_write_bytes(target, content)
        assert target.exists()
        assert target.read_bytes() == content

    def test_write_creates_parent_dirs(self, temp_dir: Path):
        target = temp_dir / "subdir" / "test.bin"
        atomic_write_bytes(target, b"test")
        assert target.exists()
        assert target.read_bytes() == b"test"

    def test_overwrite_existing(self, temp_dir: Path):
        target = temp_dir / "test.bin"
        atomic_write_bytes(target, b"first")
        atomic_write_bytes(target, b"second")
        assert target.read_bytes() == b"second"

    def test_atomic_on_failure(self, temp_dir: Path, monkeypatch):
        """Test that partial writes are cleaned up on failure."""
        target = temp_dir / "test.bin"

        def failing_replace(src, dst):
            raise OSError("Simulated failure")

        monkeypatch.setattr("os.replace", failing_replace)

        with pytest.raises(OSError):
            atomic_write_bytes(target, b"test")

        # Original file should not exist (or be unchanged if it existed)
        assert not target.exists()


class TestAtomicWriteJson:
    """Test atomic_write_json."""

    def test_write_json(self, temp_dir: Path):
        target = temp_dir / "test.json"
        data = {"key": "value", "number": 42}
        atomic_write_json(target, data)
        assert target.exists()
        import orjson

        assert orjson.loads(target.read_bytes()) == data

    def test_write_json_deterministic(self, temp_dir: Path):
        target = temp_dir / "test.json"
        data = {"b": 2, "a": 1}
        atomic_write_json(target, data)
        content = target.read_bytes()
        # Should be sorted keys (a before b)
        assert b'"a":' in content
        assert b'"b":' in content
        assert content.find(b'"a"') < content.find(b'"b"')

    def test_write_json_nested(self, temp_dir: Path):
        target = temp_dir / "test.json"
        data = {"outer": {"inner": [1, 2, 3]}}
        atomic_write_json(target, data)
        import orjson

        assert orjson.loads(target.read_bytes()) == data

    def test_write_json_indent(self, temp_dir: Path):
        target = temp_dir / "test.json"
        data = {"a": 1}
        atomic_write_json(target, data, indent=2)
        content = target.read_text()
        assert "\n" in content  # Pretty printed


class TestAtomicWriteJsonl:
    """Test atomic_write_jsonl."""

    def test_write_jsonl(self, temp_dir: Path):
        target = temp_dir / "test.jsonl"
        records = [{"id": 1, "value": "a"}, {"id": 2, "value": "b"}]
        atomic_write_jsonl(target, records)
        assert target.exists()
        lines = target.read_text().strip().split("\n")
        assert len(lines) == 2
        import orjson

        assert orjson.loads(lines[0]) == {"id": 1, "value": "a"}
        assert orjson.loads(lines[1]) == {"id": 2, "value": "b"}

    def test_write_empty_list(self, temp_dir: Path):
        target = temp_dir / "test.jsonl"
        atomic_write_jsonl(target, [])
        assert target.exists()
        assert target.read_text() == "\n"

    def test_write_deterministic(self, temp_dir: Path):
        target = temp_dir / "test.jsonl"
        records = [{"b": 2, "a": 1}]
        atomic_write_jsonl(target, records)
        content = target.read_text()
        # Keys should be sorted
        assert '"a":1' in content
        assert content.index('"a"') < content.index('"b"')


class TestAtomicWriteParquet:
    """Test atomic_write_parquet."""

    def test_write_parquet(self, temp_dir: Path):
        target = temp_dir / "test.parquet"
        table = pa.table({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})
        atomic_write_parquet(target, table)
        assert target.exists()
        result = pq.read_table(target)
        assert result.equals(table)

    def test_write_parquet_creates_dirs(self, temp_dir: Path):
        target = temp_dir / "subdir" / "test.parquet"
        table = pa.table({"col": [1]})
        atomic_write_parquet(target, table)
        assert target.exists()

    def test_overwrite_parquet(self, temp_dir: Path):
        target = temp_dir / "test.parquet"
        table1 = pa.table({"col": [1]})
        table2 = pa.table({"col": [2]})
        atomic_write_parquet(target, table1)
        atomic_write_parquet(target, table2)
        result = pq.read_table(target)
        assert result.equals(table2)
