"""Atomic file write utilities for JSON, JSONL, Parquet, and binary."""

import os
import tempfile
from pathlib import Path
from typing import Any

import orjson
import pyarrow as pa
import pyarrow.parquet as pq


def _atomic_write(target_path: Path, content: bytes) -> None:
    """Write content to target_path atomically using temp file + rename."""
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Create temp file in same directory for atomic rename
    fd, tmp_path = tempfile.mkstemp(
        dir=target_path.parent, suffix=".tmp", prefix=target_path.name + "."
    )
    try:
        os.write(fd, content)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp_path, target_path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_bytes(target_path: Path, content: bytes) -> None:
    """Write bytes atomically."""
    _atomic_write(target_path, content)


def atomic_write_json(target_path: Path, obj: Any, *, indent: int = 2) -> None:
    """Write JSON atomically with deterministic key ordering.

    Uses orjson with sorted keys for canonical serialization.
    """
    content = orjson.dumps(obj, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
    _atomic_write(target_path, content)


def atomic_write_jsonl(target_path: Path, records: list[Any]) -> None:
    """Write JSONL atomically (one JSON object per line)."""
    lines = [orjson.dumps(r, option=orjson.OPT_SORT_KEYS) for r in records]
    content = b"\n".join(lines) + b"\n"
    _atomic_write(target_path, content)


def atomic_write_parquet(target_path: Path, table: pa.Table) -> None:
    """Write Parquet atomically."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    # Write to temp file first
    fd, tmp_path = tempfile.mkstemp(
        dir=target_path.parent, suffix=".parquet", prefix=target_path.name + "."
    )
    try:
        os.close(fd)
        pq.write_table(table, tmp_path)
        os.replace(tmp_path, target_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
