"""Sidecar checksum support.

Adapted from Project Portfolio Engine ingestion/secedgar/checksums.py
(commit aa31d1e, file last modified af49ce0).

Provides streaming SHA-256 computation, .sha256 sidecar writing and
validation, and atomic sidecar replacement.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_STREAM_CHUNK_SIZE = 65536


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file using streaming (64KB chunks)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def write_sidecar(target_path: Path) -> Path:
    """Write a .sha256 sidecar file for ``target_path``.

    Uses atomic temp-file + rename.  Returns the sidecar path.
    """
    import os
    import tempfile

    sha256 = compute_file_hash(target_path)
    checksum_path = target_path.with_suffix(target_path.suffix + ".sha256")
    content = f"{sha256}  {target_path.name}\n".encode()

    fd, tmp_path = tempfile.mkstemp(
        dir=checksum_path.parent,
        suffix=".sha256.tmp",
        prefix=checksum_path.name + ".",
    )
    try:
        os.write(fd, content)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp_path, checksum_path)
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

    return checksum_path


def read_sidecar(checksum_path: Path) -> str | None:
    """Read SHA-256 from a .sha256 sidecar file."""
    if not checksum_path.exists():
        return None
    try:
        line = checksum_path.read_text(encoding="utf-8").strip()
        if not line:
            return None
        return line.split()[0]
    except (OSError, IndexError):
        return None


def validate_sidecar(target_path: Path) -> bool:
    """Verify a file against its .sha256 sidecar.

    Raises ``FileNotFoundError`` if the sidecar is missing.
    """
    checksum_path = target_path.with_suffix(target_path.suffix + ".sha256")
    if not checksum_path.exists():
        raise FileNotFoundError(f"No sidecar file found for {target_path}")
    expected = read_sidecar(checksum_path)
    if expected is None:
        raise ValueError(f"Malformed sidecar file: {checksum_path}")
    actual = compute_file_hash(target_path)
    return actual == expected
