"""Integration test: professor bundle strict release boundary.

Tests the strict V3 release gate and allowlist packager integration
in the ProfessorBundleOrchestrator.

All tests use temporary directories and tiny fake files.
No real SEC filings. No network calls.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from fenrix_synthetic.professor.orchestrator import (
    ProfessorBundleConfig,
    ProfessorBundleOrchestrator,
)


class TestStrictReleaseBoundaryIntegration:
    """Integration tests for the strict V3 release boundary in the orchestrator."""

    def test_clean_bundle_passes_strict_gate(self, tmp_path: Path) -> None:
        """A clean minimal professor release tree passes the strict release gate."""
        output_root = tmp_path / "professor_bundle_clean"

        config = ProfessorBundleConfig(
            company_id="COMPANY_001",
            output_root=output_root,
            strict=False,
            fast_fixtures=True,
            allow_provider_skip=False,
            release_date="2026-06-22",
        )
        orchestrator = ProfessorBundleOrchestrator(config)
        result = orchestrator.run()

        # Fixture mode should be strict_fixture_ready
        assert result["strict_fixture_ready"] is True
        assert result["build_mode"] == "fixture"

        # Check that release manifest exists
        assert (output_root / "RELEASE_MANIFEST.json").exists()
        assert (output_root / "RELEASE_MANIFEST.md").exists()

        # Check strict gate reports exist
        qa_dir = output_root / "qa"
        assert (qa_dir / "direct_identifier_scan.json").exists()
        assert (qa_dir / "metadata_scan.json").exists()
        assert (qa_dir / "public_release_gate.json").exists()

        # Verify public_release_gate passed
        gate = json.loads((qa_dir / "public_release_gate.json").read_text())
        assert gate["passed"] is True
        assert gate["fail_reasons"] == []

        # Check ZIP exists
        zip_path = output_root / "exports" / "anonymized_bundle.zip"
        assert zip_path.exists()

        # Check ZIP contains allowlisted files, not private
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        for name in names:
            assert not name.startswith("private/"), f"ZIP contains private: {name}"

    def test_release_manifest_privacy_flags(self, tmp_path: Path) -> None:
        """Release manifest must have all privacy flags set to false."""
        output_root = tmp_path / "professor_bundle_manifest"

        config = ProfessorBundleConfig(
            company_id="COMPANY_001",
            output_root=output_root,
            strict=False,
            fast_fixtures=True,
            allow_provider_skip=False,
            release_date="2026-06-22",
        )
        orchestrator = ProfessorBundleOrchestrator(config)
        orchestrator.run()

        manifest_data = json.loads((output_root / "RELEASE_MANIFEST.json").read_text())
        assert manifest_data["identity_map_included"] is False
        assert manifest_data["raw_source_included"] is False
        assert manifest_data["raw_sec_html_included"] is False
        assert manifest_data["raw_xbrl_included"] is False
        assert manifest_data["strict_release_gate"] is True

    def test_zip_excludes_private_directory(self, tmp_path: Path) -> None:
        """ZIP must not include private/ directory."""
        output_root = tmp_path / "professor_bundle_zip"

        config = ProfessorBundleConfig(
            company_id="COMPANY_001",
            output_root=output_root,
            strict=False,
            fast_fixtures=True,
            allow_provider_skip=False,
            release_date="2026-06-22",
        )
        orchestrator = ProfessorBundleOrchestrator(config)
        orchestrator.run()

        zip_path = output_root / "exports" / "anonymized_bundle.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()

        for name in names:
            assert "private/" not in name, f"ZIP contains private path: {name}"
            assert "raw/" not in name, f"ZIP contains raw path: {name}"
            assert ".env" not in name, f"ZIP contains .env: {name}"
            assert ".html" not in name.lower(), f"ZIP contains .html: {name}"
            assert ".xml" not in name.lower(), f"ZIP contains .xml: {name}"

    def test_zip_contains_required_qa_reports(self, tmp_path: Path) -> None:
        """ZIP must contain expected QA reports and manifest files."""
        output_root = tmp_path / "professor_bundle_reports"

        config = ProfessorBundleConfig(
            company_id="COMPANY_001",
            output_root=output_root,
            strict=False,
            fast_fixtures=True,
            allow_provider_skip=False,
            release_date="2026-06-22",
        )
        orchestrator = ProfessorBundleOrchestrator(config)
        orchestrator.run()

        zip_path = output_root / "exports" / "anonymized_bundle.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())

        assert "RELEASE_MANIFEST.json" in names
        assert "RELEASE_MANIFEST.md" in names
        assert "qa/public_release_gate.json" in names
        assert "qa/direct_identifier_scan.json" in names
        assert "qa/metadata_scan.json" in names
        assert "qa/classroom_gate_report.json" in names

    def test_strict_gate_reports_written(self, tmp_path: Path) -> None:
        """Three strict gate reports must be written to qa/."""
        output_root = tmp_path / "professor_bundle_strict_reports"

        config = ProfessorBundleConfig(
            company_id="COMPANY_001",
            output_root=output_root,
            strict=False,
            fast_fixtures=True,
            allow_provider_skip=False,
            release_date="2026-06-22",
        )
        orchestrator = ProfessorBundleOrchestrator(config)
        orchestrator.run()

        qa_dir = output_root / "qa"

        # Direct identifier scan report
        di_report = json.loads((qa_dir / "direct_identifier_scan.json").read_text())
        assert "scanned_files" in di_report or "hits" in di_report

        # Metadata scan report
        md_report = json.loads((qa_dir / "metadata_scan.json").read_text())
        assert "scanned_files" in md_report or "hits" in md_report

        # Public release gate report
        gate = json.loads((qa_dir / "public_release_gate.json").read_text())
        assert "passed" in gate
        assert "mode" in gate
        assert "checked_at" in gate

    def test_stage_registry_records_release_gate(self, tmp_path: Path) -> None:
        """Stage registry must record RELEASE_GATE stage."""
        output_root = tmp_path / "professor_bundle_registry"

        config = ProfessorBundleConfig(
            company_id="COMPANY_001",
            output_root=output_root,
            strict=False,
            fast_fixtures=True,
            allow_provider_skip=False,
            release_date="2026-06-22",
        )
        orchestrator = ProfessorBundleOrchestrator(config)
        orchestrator.run()

        reg_path = output_root / "qa" / "stage_registry.json"
        reg = json.loads(reg_path.read_text())
        stages = reg.get("stages", {})

        assert "RELEASE_GATE" in stages
        assert stages["RELEASE_GATE"]["status"] == "PASS"

    def test_run_summary_reflects_build_mode(self, tmp_path: Path) -> None:
        """Run summary must reflect fixture build mode."""
        output_root = tmp_path / "professor_bundle_summary"

        config = ProfessorBundleConfig(
            company_id="COMPANY_001",
            output_root=output_root,
            strict=False,
            fast_fixtures=True,
            allow_provider_skip=False,
            release_date="2026-06-22",
        )
        orchestrator = ProfessorBundleOrchestrator(config)
        _result = orchestrator.run()

        summary = json.loads((output_root / "run_summary.json").read_text())
        assert summary["build_mode"] == "fixture"
        assert summary["strict_fixture_ready"] is True
        assert summary["professor_ready"] is False

    def test_all_required_artifacts_produced(self, tmp_path: Path) -> None:
        """All required public artifacts must be produced in the output tree."""
        output_root = tmp_path / "professor_bundle_artifacts"

        config = ProfessorBundleConfig(
            company_id="COMPANY_001",
            output_root=output_root,
            strict=False,
            fast_fixtures=True,
            allow_provider_skip=False,
            release_date="2026-06-22",
        )
        orchestrator = ProfessorBundleOrchestrator(config)
        orchestrator.run()

        # Top-level files
        assert (output_root / "RELEASE_MANIFEST.json").exists()
        assert (output_root / "RELEASE_MANIFEST.md").exists()
        assert (output_root / "run_summary.json").exists()
        assert (output_root / "checksums.sha256").exists()
        assert (output_root / "artifact_inventory.csv").exists()

        # QA reports
        qa_dir = output_root / "qa"
        required_qa = [
            "direct_identifier_scan.json",
            "metadata_scan.json",
            "public_release_gate.json",
            "classroom_gate_report.json",
            "stage_registry.json",
        ]
        for name in required_qa:
            assert (qa_dir / name).exists(), f"Missing QA report: {name}"

        # ZIP
        zip_path = output_root / "exports" / "anonymized_bundle.zip"
        assert zip_path.exists()
        assert zip_path.stat().st_size > 0
