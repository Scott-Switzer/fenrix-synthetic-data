"""Tests for checksum sidecar operations and storage lineage."""

from pathlib import Path

import pytest

from fenrix_synthetic.storage.checksums import (
    compute_file_hash,
    read_sidecar,
    validate_sidecar,
    write_sidecar,
)


class TestChecksums:
    """Test sidecar checksum operations."""

    def test_compute_file_hash(self, temp_dir: Path):
        f = temp_dir / "test.txt"
        f.write_text("hello")
        h = compute_file_hash(f)
        assert len(h) == 64
        assert isinstance(h, str)

    def test_compute_hash_deterministic(self, temp_dir: Path):
        f = temp_dir / "test.txt"
        f.write_text("hello")
        h1 = compute_file_hash(f)
        h2 = compute_file_hash(f)
        assert h1 == h2

    def test_sidecar_creation(self, temp_dir: Path):
        f = temp_dir / "test.html"
        f.write_text("<html>content</html>")
        sidecar = write_sidecar(f)
        assert sidecar.exists()
        assert sidecar.suffix == ".sha256"
        assert sidecar.name == "test.html.sha256"

    def test_sidecar_content_format(self, temp_dir: Path):
        f = temp_dir / "test.html"
        f.write_text("content")
        sidecar = write_sidecar(f)
        content = sidecar.read_text(encoding="utf-8").strip()
        parts = content.split()
        assert len(parts) == 2
        assert len(parts[0]) == 64  # SHA-256 hex
        assert parts[1] == "test.html"

    def test_sidecar_read(self, temp_dir: Path):
        f = temp_dir / "test.html"
        f.write_text("content")
        sidecar = write_sidecar(f)
        h = read_sidecar(sidecar)
        assert h is not None
        assert len(h) == 64

    def test_sidecar_read_missing(self, temp_dir: Path):
        h = read_sidecar(temp_dir / "nonexistent.html.sha256")
        assert h is None

    def test_sidecar_validation_valid(self, temp_dir: Path):
        f = temp_dir / "test.html"
        f.write_text("content")
        write_sidecar(f)
        assert validate_sidecar(f) is True

    def test_sidecar_validation_missing_raises(self, temp_dir: Path):
        f = temp_dir / "test.html"
        f.write_text("content")
        with pytest.raises(FileNotFoundError):
            validate_sidecar(f)

    def test_sidecar_validation_malformed_raises(self, temp_dir: Path):
        f = temp_dir / "test.html"
        f.write_text("content")
        sidecar = f.with_suffix(f.suffix + ".sha256")
        sidecar.write_text("")
        with pytest.raises(ValueError, match="Malformed"):
            validate_sidecar(f)

    def test_atomic_sidecar_replacement(self, temp_dir: Path):
        """Write sidecar and verify it replaces cleanly on re-write."""
        f = temp_dir / "test.html"
        f.write_text("content v1")
        s1 = write_sidecar(f)
        h1 = read_sidecar(s1)

        f.write_text("content v2")
        s2 = write_sidecar(f)
        h2 = read_sidecar(s2)

        assert h1 != h2
