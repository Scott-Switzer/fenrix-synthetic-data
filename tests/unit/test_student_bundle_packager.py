"""Tests for student bundle packager.

Validates allowlist-based ZIP packaging and forbidden path enforcement.
Forbidden paths outside allowlisted areas are silently excluded;
forbidden content inside allowlisted areas is rejected.
"""

from __future__ import annotations

import json
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


class TestPackagerExcludesAppleDoubleAndTempArtifacts:
    """Phase 8F remediation: AppleDouble / macOS metadata / temp-work exclusion."""

    def test_excludes_appledouble_entries(self, tmp_path: Path) -> None:
        """Files with ._ prefix are silently excluded from the bundle."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/anonymized/C001/sec/item_7.md": "# Item 7",
                "._public/anonymized/C001/sec/item_7.md": "APPLE_DOUBLE",
            },
        )
        result = validate_bundle_tree(tmp_path)
        # AppleDouble files outside allowlisted areas are silently excluded.
        # Validation passes because they aren't in allowlisted areas.
        assert result.passed
        assert "._public/anonymized/C001/sec/item_7.md" not in result.allowed_entries

    def test_excludes_appledouble_entries_top_level(self, tmp_path: Path) -> None:
        """Top-level ._* entries are also excluded."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "._some_metadata": "APPLE_DOUBLE",
            },
        )
        result = validate_bundle_tree(tmp_path)
        # Top-level ._* outside allowlisted area → silently excluded
        assert result.passed
        assert "._some_metadata" not in result.allowed_entries

    def test_excludes_macosx_metadata_directory(self, tmp_path: Path) -> None:
        """__MACOSX directory entries are forbidden and excluded."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "__MACOSX/public/anonymized/C001/.filing.md": "MACOS",
            },
        )
        result = validate_bundle_tree(tmp_path)
        # __MACOSX outside allowlisted area → silently excluded
        assert result.passed
        assert "__MACOSX/public/anonymized/C001/.filing.md" not in result.allowed_entries

    def test_excludes_inner_work_directories(self, tmp_path: Path) -> None:
        """.inner_work/ and ._inner_work/ entries are forbidden and excluded."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "._inner_work/COMPANY_001/stage_registry.json": "{}",
            },
        )
        result = validate_bundle_tree(tmp_path)
        # inner_work outside allowlisted area → silently excluded
        assert result.passed
        assert "._inner_work/COMPANY_001/stage_registry.json" not in result.allowed_entries

    def test_excludes_ds_store(self, tmp_path: Path) -> None:
        """.DS_Store files are forbidden and excluded."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/anonymized/.DS_Store": "DSSTORE",
            },
        )
        result = validate_bundle_tree(tmp_path)
        # .DS_Store in allowlisted area → rejected
        assert not result.passed
        assert any(".DS_Store" in e for e in result.rejected_entries)

    def test_production_bundle_package_prevalidation_ignores_temp_artifacts(
        self, tmp_path: Path
    ) -> None:
        """All temp artifacts (.DS_Store, ._*, __MACOSX, .inner_work) are excluded from ZIP."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/anonymized/C001/.DS_Store": "DS",
                ".AppleDouble/parent": "AD",
                ".inner_work/temp.json": "{}",
                "._inner_work/COMPANY_001/stage_registry.json": "{}",
            },
        )
        result = validate_bundle_tree(tmp_path)
        # Temp artifacts outside allowlisted areas → silently excluded
        # .DS_Store inside allowlisted area → rejected
        assert not result.passed  # .DS_Store in allowlisted area rejects
        rejected = result.rejected_entries
        assert any(".DS_Store" in e for e in rejected), rejected
        # Other temp artifacts are excluded from allowed entries
        for bad in (".AppleDouble", ".inner_work", "._inner_work"):
            assert not any(bad in e for e in result.allowed_entries), f"{bad} found in allowed"

    def test_clean_bundle_still_passes(self, tmp_path: Path) -> None:
        """A bundle with only valid files still passes with no rejections."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/anonymized/C001/sec/item_7.md": "# Item 7",
                "public/anonymized/C001/profile/profile.md": "# Profile",
                "qa/llm_blind_guess_summary.json": "{}",
                "qa/utility_preservation_summary.json": "{}",
                "checksums.sha256": "abc",
                "run_summary.json": "{}",
                "QUICKSTART.md": "# Quick",
                "RUN_SUMMARY.md": "# Run",
                "DATA_DICTIONARY.md": "# Dict",
                "RELEASE_MANIFEST.json": "{}",
                "RELEASE_MANIFEST.md": "# Man",
                "artifact_inventory.csv": "path,bytes,kind\n",
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert result.passed, f"No rejections expected, got: {result.rejected_entries}"


class TestStageRegistryRedactsPrivateAuditFilenames:
    """Phase 8F remediation: public stage_registry must not leak private file names."""

    #: Private audit filenames that must be excluded from the public ZIP.
    PRIVATE_AUDIT_FILENAMES: tuple[str, ...] = (
        "peer_archetype_audit",
        "numeric_transform_audit",
        "trajectory_morph_audit",
        "llm_blind_guess_private",
        "utility_preservation_private",
        "news_reconstruction_private",
    )

    def test_public_stage_registry_has_no_private_audit_filenames(
        self, tmp_path: Path
    ) -> None:
        """stage_registry files with private audit filenames in their PATH
        are rejected. Contents are redacted before writing - this test
        validates the file-level exclusion."""
        # A filename CONTAINING a forbidden substring inside qa/ → rejected
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "qa/peer_archetype_audit.json": "{}",  # filename IS the forbidden pattern
            },
        )
        result = validate_bundle_tree(tmp_path)
        assert not result.passed
        assert any("peer_archetype_audit" in e for e in result.rejected_entries)

    def test_forbidden_substrings_in_qa_rejected(self, tmp_path: Path) -> None:
        """Any qa/ file whose PATH contains a forbidden substring is rejected."""
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "qa/llm_blind_guess_summary.json": '{"reviewed":true}',
                "qa/peer_archetype_audit.json": "{}",  # filename IS the forbidden pattern
            },
        )
        result = validate_bundle_tree(tmp_path)
        # The file is in allowlisted qa/ area but its name has a forbidden substring
        assert not result.passed
        assert any("peer_archetype_audit" in e for e in result.rejected_entries)

    def test_banned_text_scan_does_not_hit_clean_stage_registry(self) -> None:
        """A redacted stage_registry passes the forbidden-pattern scan."""
        from fenrix_synthetic.professor.multi_orchestrator import (
            ProfessorBundleMultiCompanyOrchestrator,
        )

        raw = {
            "stages": {
                "PEER_ARCHETYPE": {
                    "outputs": ["peer_archetype_audit.json", "profile.md"],
                    "status": "PASS",
                },
                "NUMERIC_TRANSFORM": {
                    "outputs": ["numeric_transform_audit.json", "metrics.csv"],
                    "status": "PASS",
                },
                "LLM_BLIND_GUESS": {
                    "outputs": ["llm_blind_guess_private.json", "summary.md"],
                    "status": "PASS",
                },
            }
        }
        redacted = ProfessorBundleMultiCompanyOrchestrator._redact_private_filenames(raw)
        serialized = json.dumps(redacted)
        for forbidden in self.PRIVATE_AUDIT_FILENAMES:
            assert forbidden not in serialized, (
                f"{forbidden} leaked in redacted stage_registry"
            )
