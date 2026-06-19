"""Deterministic SHA-256 hashing utilities."""

import hashlib
from pathlib import Path
from typing import Any

import orjson


def _sha256(data: bytes) -> str:
    """Compute SHA-256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


def hash_bytes(data: bytes) -> str:
    """Compute SHA-256 hash of bytes."""
    return _sha256(data)


def hash_string(data: str) -> str:
    """Compute SHA-256 hash of string (UTF-8 encoded)."""
    return _sha256(data.encode("utf-8"))


def hash_file(path: Path) -> str:
    """Compute SHA-256 hash of file contents."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def hash_object(obj: Any) -> str:
    """Compute deterministic SHA-256 hash of a JSON-serializable object.

    Uses orjson with sorted keys for canonical serialization.
    """
    # Use orjson with OPT_SORT_KEYS for deterministic output
    serialized = orjson.dumps(obj, option=orjson.OPT_SORT_KEYS)
    return _sha256(serialized)


def hash_dict(d: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of a dictionary."""
    return hash_object(d)
