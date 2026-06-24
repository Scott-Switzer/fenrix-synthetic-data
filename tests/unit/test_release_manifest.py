"""Tests for release manifest.

Validates that privacy flags are enforced as False and
manifest serialization works correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fenrix_synthetic.package.release_manifest import (
    ReleaseManifest,
    create_release_manifest,
)


class TestReleaseManifestCreation:
    """Test manifest creation."""

    def test_creates_valid_manifest(self) -> None:
        manifest = create_release_manifest(
            release_id="SYNTH_001",
            repo_sha="abc123",
            branch="feature/test",
            source_count=1,
        )
        assert manifest.release_id == "SYNTH_001"
        assert manifest.repo_sha == "abc123"
        assert manifest.identity_map_included is False
        assert manifest.raw_source_included is False
        assert manifest.raw_sec_html_included is False
        assert manifest.raw_xbrl_included is False

    def test_manifest_requires_false_identity_map(self) -> None:
        with pytest.raises(ValueError, match="identity_map_included must be False"):
            ReleaseManifest(
                release_id="test",
                identity_map_included=True,
                raw_source_included=False,
                raw_sec_html_included=False,
                raw_xbrl_included=False,
            )

    def test_manifest_requires_false_raw_source(self) -> None:
        with pytest.raises(ValueError, match="raw_source_included must be False"):
            ReleaseManifest(
                release_id="test",
                identity_map_included=False,
                raw_source_included=True,
                raw_sec_html_included=False,
                raw_xbrl_included=False,
            )

    def test_manifest_requires_false_raw_sec_html(self) -> None:
        with pytest.raises(ValueError, match="raw_sec_html_included must be False"):
            ReleaseManifest(
                release_id="test",
                identity_map_included=False,
                raw_source_included=False,
                raw_sec_html_included=True,
                raw_xbrl_included=False,
            )

    def test_manifest_requires_false_raw_xbrl(self) -> None:
        with pytest.raises(ValueError, match="raw_xbrl_included must be False"):
            ReleaseManifest(
                release_id="test",
                identity_map_included=False,
                raw_source_included=False,
                raw_sec_html_included=False,
                raw_xbrl_included=True,
            )


class TestReleaseManifestSerialization:
    """Test manifest serialization."""

    def test_to_dict(self) -> None:
        manifest = create_release_manifest("test", repo_sha="abc")
        d = manifest.to_dict()
        assert d["identity_map_included"] is False

    def test_to_json(self) -> None:
        manifest = create_release_manifest("test", repo_sha="abc")
        j = manifest.to_json()
        assert '"identity_map_included": false' in j or '"identity_map_included":false' in j
        import json
        parsed = json.loads(j)
        assert parsed["identity_map_included"] is False

    def test_to_markdown(self) -> None:
        manifest = create_release_manifest("test", repo_sha="abc")
        md = manifest.to_markdown()
        assert "# Release Manifest: test" in md
        assert "**Identity Map Included:** False" in md
        assert "**Raw Source Included:** False" in md
        assert "**Raw SEC HTML Included:** False" in md
        assert "**Raw XBRL Included:** False" in md

    def test_accepts_default_values(self) -> None:
        manifest = ReleaseManifest(release_id="test")
        assert manifest.identity_map_included is False
        assert manifest.raw_source_included is False

    def test_contains_repo_sha(self) -> None:
        manifest = create_release_manifest(
            "test",
            repo_sha="abcdef1234567890",
            branch="feature/test",
        )
        assert manifest.repo_sha == "abcdef1234567890"
        assert manifest.branch == "feature/test"

    def test_public_company_ids_default(self) -> None:
        manifest = create_release_manifest("test")
        assert manifest.public_company_ids == []
        assert manifest.source_count == 0

    def test_artifact_counts(self) -> None:
        manifest = create_release_manifest(
            "test",
            artifact_counts={"md": 5, "json": 10},
        )
        assert manifest.artifact_counts == {"md": 5, "json": 10}

    def test_qa_reports_listed(self) -> None:
        manifest = create_release_manifest(
            "test",
            qa_reports=["qa/stage_registry.json", "qa/direct_identifier_scan.json"],
        )
        assert "qa/stage_registry.json" in manifest.qa_reports
        assert "qa/direct_identifier_scan.json" in manifest.qa_reports
