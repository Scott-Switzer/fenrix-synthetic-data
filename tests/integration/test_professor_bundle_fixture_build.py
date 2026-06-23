"""Integration test: full professor bundle fixture build.

Runs the complete 18-stage pipeline with mock providers and verifies
all output artifacts, QA reports, ZIP export, and gate decision.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from fenrix_synthetic.cli import cli
from fenrix_synthetic.professor.orchestrator import (
    ProfessorBundleConfig,
    ProfessorBundleOrchestrator,
)


@pytest.fixture
def bundle_output(tmp_path: Path) -> Path:
    """Build a professor bundle fixture and return the output root."""
    config = ProfessorBundleConfig(
        company_id="COMPANY_001",
        output_root=tmp_path / "professor_bundle",
        strict=False,
        fast_fixtures=True,
        allow_provider_skip=False,
        release_date="2026-06-22",
    )
    orchestrator = ProfessorBundleOrchestrator(config)
    result = orchestrator.run()
    # Fixture mode should NOT be professor_ready but SHOULD be strict_fixture_ready
    assert result["professor_ready"] is False
    assert result["strict_fixture_ready"] is True
    assert result["build_mode"] == "fixture"
    return config.output_root


class TestProfessorBundleFixtureBuild:
    def test_bundle_produces_zip(self, bundle_output: Path) -> None:
        zip_path = bundle_output / "exports" / "anonymized_bundle.zip"
        assert zip_path.exists()
        assert zip_path.stat().st_size > 0

    def test_zip_excludes_private_paths(self, bundle_output: Path) -> None:
        zip_path = bundle_output / "exports" / "anonymized_bundle.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        for name in names:
            assert not name.startswith("private/"), f"ZIP contains private path: {name}"
            assert not name.startswith("originals/"), f"ZIP contains originals: {name}"
            assert not name.startswith("maps/"), f"ZIP contains maps: {name}"
            assert ".env" not in name, f"ZIP contains .env: {name}"

    def test_zip_contains_public_artifacts(self, bundle_output: Path) -> None:
        zip_path = bundle_output / "exports" / "anonymized_bundle.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        assert "public/README.md" in names
        assert "public/CLASSROOM_GUIDE.md" in names
        assert "public/EXERCISES.md" in names
        assert "public/RUBRIC.md" in names

    def test_zip_contains_qa_reports(self, bundle_output: Path) -> None:
        zip_path = bundle_output / "exports" / "anonymized_bundle.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        assert "qa/stage_registry.json" in names
        assert "qa/entity_audit_report.json" in names
        assert "qa/classroom_gate_report.json" in names

    def test_all_qa_reports_exist(self, bundle_output: Path) -> None:
        qa_dir = bundle_output / "qa"
        required = [
            "stage_registry.json",
            "entity_audit_report.json",
            "metrics_quality_report.json",
            "metrics_privacy_report.json",
            "metrics_schema_report.json",
            "rag_index_report.json",
            "adversarial_qa_report.json",
            "classroom_gate_report.json",
        ]
        for name in required:
            assert (qa_dir / name).exists(), f"Missing QA report: {name}"

    def test_all_classroom_docs_exist(self, bundle_output: Path) -> None:
        public_dir = bundle_output / "public"
        required = [
            "README.md",
            "CLASSROOM_GUIDE.md",
            "PROFESSOR_AUDIT_GUIDE.md",
            "EXERCISES.md",
            "ANSWER_KEY_STUB.md",
            "RUBRIC.md",
        ]
        for name in required:
            assert (public_dir / name).exists(), f"Missing classroom doc: {name}"

    def test_company_dir_has_learning_guide_and_crosslinks(self, bundle_output: Path) -> None:
        company_dir = bundle_output / "public" / "anonymized" / "COMPANY_001"
        assert (company_dir / "LEARNING_GUIDE.md").exists()
        assert (company_dir / "crosslinks.json").exists()

    def test_company_dir_has_sec_and_metrics(self, bundle_output: Path) -> None:
        company_dir = bundle_output / "public" / "anonymized" / "COMPANY_001"
        assert (company_dir / "sec").exists()
        assert (company_dir / "metrics").exists()
        assert (company_dir / "news").exists()

    def test_gate_report_passes(self, bundle_output: Path) -> None:
        gate_path = bundle_output / "qa" / "classroom_gate_report.json"
        gate = json.loads(gate_path.read_text())
        assert gate["decision"] == "PASS"
        # Fixture mode: strict_fixture_ready=True, professor_ready=False
        assert gate["strict_fixture_ready"] is True
        assert gate["professor_ready"] is False
        assert gate["build_mode"] == "fixture"
        assert gate["blocking_failures"] == []

    def test_stage_registry_has_all_stages(self, bundle_output: Path) -> None:
        reg_path = bundle_output / "qa" / "stage_registry.json"
        reg = json.loads(reg_path.read_text())
        assert reg["all_stages_present"] is True
        assert reg["all_stages_pass"] is True
        assert reg["strict_fixture_ready"] is True
        assert reg["professor_ready"] is False
        assert reg["build_mode"] == "fixture"
        assert reg["beta_status"] == "STRICT_FIXTURE_READY"

    def test_checksums_file_exists(self, bundle_output: Path) -> None:
        assert (bundle_output / "checksums.sha256").exists()

    def test_artifact_inventory_exists(self, bundle_output: Path) -> None:
        assert (bundle_output / "artifact_inventory.csv").exists()

    def test_run_summary_exists(self, bundle_output: Path) -> None:
        summary_path = bundle_output / "run_summary.json"
        summary = json.loads(summary_path.read_text())
        assert summary["strict_fixture_ready"] is True
        assert summary["professor_ready"] is False
        assert summary["beta_status"] == "STRICT_FIXTURE_READY"
        assert summary["build_mode"] == "fixture"
        assert summary["strict_fixture_ready"] is True

    def test_private_evidence_dir_exists(self, bundle_output: Path) -> None:
        evidence_path = bundle_output / "private" / "evidence" / "evidence_graph.json"
        assert evidence_path.exists()

    def test_no_identity_leaks_in_public_artifacts(self, bundle_output: Path) -> None:
        """Scan all public files for canary identity patterns."""
        public_dir = bundle_output / "public"
        forbidden = [
            "Canary Holdings Corporation",
            "CHC",
            "0000999999",
            "Eleanor Testperson",
            "canary-test.invalid",
        ]
        for fp in public_dir.rglob("*"):
            if not fp.is_file():
                continue
            content = fp.read_text(encoding="utf-8", errors="replace")
            for pattern in forbidden:
                assert pattern not in content, (
                    f"Identity leak: '{pattern}' found in {fp.relative_to(bundle_output)}"
                )

    def test_metrics_are_not_fixed_template(self, bundle_output: Path) -> None:
        """Verify metrics row count is not a fixed template (e.g., 2514)."""
        metrics_dir = bundle_output / "public" / "anonymized" / "COMPANY_001" / "metrics"
        returns_path = metrics_dir / "returns.json"
        returns = json.loads(returns_path.read_text())
        assert len(returns) != 2514  # Not the fixed template size
        assert len(returns) > 100  # Reasonable size

    def test_cli_build_professor_bundle_command(self, tmp_path: Path) -> None:
        """Test the CLI command works end-to-end."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "build-professor-bundle",
                "--config",
                "configs/professor_bundle.fixture.yaml",
                "--output-root",
                str(tmp_path / "cli_bundle"),
                "--fast-fixtures",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Strict fixture ready: True" in result.output
        assert "Professor ready: False" in result.output
        assert "Release safe: False" in result.output
        assert (tmp_path / "cli_bundle" / "exports" / "anonymized_bundle.zip").exists()
