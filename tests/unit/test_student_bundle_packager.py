"""Tests for student bundle packager.

Validates allowlist-based ZIP packaging and forbidden path enforcement.
Forbidden paths outside allowlisted areas are silently excluded;
forbidden content inside allowlisted areas is rejected.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fenrix_synthetic.package.student_bundle import (
    package_student_bundle,
    validate_bundle_tree,
)


def _make_dir_structure(base: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        fp = base / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")


class TestBundleValidation:
    """Test bundle tree validation."""

    def test_allowlist_includes_public_files(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/anonymized/C001/sec/item_7.md": "# Item 7",
                "checksums.sha256": "abc",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert result.passed, f"Should pass: {result.rejected_entries}"
        assert "public/README.md" in result.allowed_entries

    def test_private_directory_skipped_silently(self, tmp_path: Path) -> None:
        """private/ outside allowlisted area → silently excluded, not a failure."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "private/identity_map.json": "{}",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert result.passed, f"Should pass, private/ silently excluded: {result.rejected_entries}"
        assert "public/README.md" in result.allowed_entries
        assert "private/identity_map.json" not in result.allowed_entries

    def test_raw_directory_skipped_silently(self, tmp_path: Path) -> None:
        """raw/ outside allowlisted area → silently excluded."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "raw/filing.html": "<html>",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert result.passed, f"Should pass: {result.rejected_entries}"
        assert "raw/filing.html" not in result.allowed_entries

    def test_env_file_skipped_silently(self, tmp_path: Path) -> None:
        """.env outside allowlisted area → silently excluded."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                ".env": "SECRET=value",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert result.passed, f"Should pass: {result.rejected_entries}"
        assert ".env" not in result.allowed_entries

    def test_html_in_public_is_rejected(self, tmp_path: Path) -> None:
        """.html inside public/ → rejected as validation failure."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/anonymized/C001/filing.html": "<html>",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert not result.passed
        assert any(".html" in e for e in result.rejected_entries)

    def test_xml_in_public_is_rejected(self, tmp_path: Path) -> None:
        """.xml inside public/ → rejected as validation failure."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/data.xml": "<?xml?>",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert not result.passed
        assert any(".xml" in e for e in result.rejected_entries)

    def test_key_file_outside_allowlist_skipped(self, tmp_path: Path) -> None:
        """.key outside allowlisted area → silently excluded."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "secret.key": "key",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert result.passed, f"Should pass: {result.rejected_entries}"
        assert "secret.key" not in result.allowed_entries

    def test_identity_file_outside_allowlist_skipped(self, tmp_path: Path) -> None:
        """identity_map.json outside allowlisted area → silently excluded."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "identity_map.json": "{}",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert result.passed, f"Should pass: {result.rejected_entries}"
        assert "identity_map.json" not in result.allowed_entries


class TestPackageStudentBundle:
    """Test ZIP packaging."""

    def test_packages_clean_bundle(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/CLASSROOM_GUIDE.md": "# Guide",
                "public/anonymized/C001/sec/item_7.md": "# Item 7",
                "qa/stage_registry.json": "{}",
                "checksums.sha256": "abc123",
                "run_summary.json": '{"ok": true}',
                "artifact_inventory.csv": "path,bytes,class\n",
            },
        )
        zip_path, pre, post = package_student_bundle(tmp_path)
        assert zip_path.exists()
        assert pre.passed
        assert post.passed

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "public/README.md" in names
            assert "public/anonymized/C001/sec/item_7.md" in names

    def test_skips_private_directory_cleanly(self, tmp_path: Path) -> None:
        """private/ outside allowlisted area → silently excluded, package succeeds."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "private/secret.txt": "secret",
            },
        )
        # Should NOT raise — private/ is silently excluded
        zip_path, pre, post = package_student_bundle(tmp_path)
        assert pre.passed
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "private/secret.txt" not in names
            assert "public/README.md" in names

    def test_blocks_package_with_html_in_public(self, tmp_path: Path) -> None:
        """.html in public/ → pre-validation fails, packaging blocked."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/filing.html": "<html>",
            },
        )
        with pytest.raises(RuntimeError, match="pre-validation failed"):
            package_student_bundle(tmp_path, validate_before=True)

    def test_includes_allowlisted_public_files(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/anonymized/C001/sec/item_7.md": "# Item 7",
                "public/anonymized/C001/metrics/returns.json": "{}",
                "qa/stage_registry.json": "{}",
            },
        )
        zip_path, _, post = package_student_bundle(tmp_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "public/README.md" in names
            assert "public/anonymized/C001/sec/item_7.md" in names
            assert "public/anonymized/C001/metrics/returns.json" in names
            assert "qa/stage_registry.json" in names

    def test_excludes_unlisted_top_level_file(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "some_random_file.txt": "not in allowlist",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert not result.passed
        assert "some_random_file.txt" in result.rejected_entries

    def test_validation_result_to_dict(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
            },
        )
        result = validate_bundle_tree(tmp_path)
        d = result.to_dict()
        assert d["passed"]
        assert d["rejected_count"] == 0
        assert "validation_hash" in d
