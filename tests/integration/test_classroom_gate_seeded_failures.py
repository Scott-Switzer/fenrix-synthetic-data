"""Integration test: seeded failure cases for the classroom gate.

Tests that each blocking condition triggers a gate failure when seeded
into the bundle.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from fenrix_synthetic.professor.orchestrator import (
    ProfessorBundleConfig,
    ProfessorBundleOrchestrator,
)
from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate


@pytest.fixture
def clean_bundle(tmp_path: Path) -> Path:
    """Build a clean professor bundle and return the output root."""
    config = ProfessorBundleConfig(
        output_root=tmp_path / "bundle",
        fast_fixtures=True,
    )
    orchestrator = ProfessorBundleOrchestrator(config)
    orchestrator.run()
    return config.output_root


class TestClassroomGateSeededFailures:
    def test_clean_bundle_passes_gate(self, clean_bundle: Path) -> None:
        result = evaluate_classroom_gate(
            bundle_root=clean_bundle,
            release_date="2026-06-22",
            strict=False,
        )
        assert result["decision"] == "PASS"

    def test_missing_stage_blocks(self, clean_bundle: Path) -> None:
        # Remove a stage from the registry
        reg_path = clean_bundle / "qa" / "stage_registry.json"
        reg_data = json.loads(reg_path.read_text())
        del reg_data["stages"]["ADVERSARIAL_QA"]
        reg_path.write_text(json.dumps(reg_data))

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22")
        assert result["decision"] == "FAIL"
        assert any("missing_mandatory_stages" in f for f in result["blocking_failures"])

    def test_identity_leak_blocks(self, clean_bundle: Path) -> None:
        # Inject a canary identifier into a public file
        leak_file = clean_bundle / "public" / "LEAK_TEST.md"
        leak_file.write_text("This file contains CHC which is a leak.")

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22")
        assert result["decision"] == "FAIL"
        assert any("identity_leak" in f for f in result["blocking_failures"])

    def test_missing_classroom_doc_blocks(self, clean_bundle: Path) -> None:
        # Remove a required classroom doc
        (clean_bundle / "public" / "RUBRIC.md").unlink()

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22")
        assert result["decision"] == "FAIL"
        assert any("missing_classroom_doc" in f for f in result["blocking_failures"])

    def test_missing_learning_guide_blocks(self, clean_bundle: Path) -> None:
        # Remove LEARNING_GUIDE.md
        (clean_bundle / "public" / "anonymized" / "COMPANY_001" / "LEARNING_GUIDE.md").unlink()

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22")
        assert result["decision"] == "FAIL"
        assert any("missing_learning_guide" in f for f in result["blocking_failures"])

    def test_missing_crosslinks_blocks(self, clean_bundle: Path) -> None:
        # Remove crosslinks.json
        (clean_bundle / "public" / "anonymized" / "COMPANY_001" / "crosslinks.json").unlink()

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22")
        assert result["decision"] == "FAIL"
        assert any("missing_crosslinks" in f for f in result["blocking_failures"])

    def test_missing_qa_report_blocks(self, clean_bundle: Path) -> None:
        # Remove a required QA report
        (clean_bundle / "qa" / "metrics_quality_report.json").unlink()

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22")
        assert result["decision"] == "FAIL"
        assert any("missing_qa_report" in f for f in result["blocking_failures"])

    def test_missing_checksums_blocks(self, clean_bundle: Path) -> None:
        (clean_bundle / "checksums.sha256").unlink()

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22")
        assert result["decision"] == "FAIL"
        assert any("missing_checksums" in f for f in result["blocking_failures"])

    def test_strict_mode_blocks_provider_not_run(self, clean_bundle: Path) -> None:
        # Mark a stage as PROVIDER_NOT_RUN
        reg_path = clean_bundle / "qa" / "stage_registry.json"
        reg_data = json.loads(reg_path.read_text())
        reg_data["stages"]["ENTITY_DETECT_GLINER"]["status"] = "PROVIDER_NOT_RUN"
        # Set build_mode to production for this test
        reg_data["build_mode"] = "production"
        reg_path.write_text(json.dumps(reg_data))

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22", strict=True)
        assert result["decision"] == "FAIL"
        assert any("provider_not_run_in_production_mode" in f for f in result["blocking_failures"])

    def test_production_mode_blocks_mock_provider(self, clean_bundle: Path) -> None:
        """Production mode blocks when a stage uses a mock provider."""
        reg_path = clean_bundle / "qa" / "stage_registry.json"
        reg_data = json.loads(reg_path.read_text())
        reg_data["stages"]["ENTITY_DETECT_GLINER"]["provider_kind"] = "mock"
        reg_data["build_mode"] = "production"
        reg_path.write_text(json.dumps(reg_data))

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22", strict=True)
        assert result["decision"] == "FAIL"
        assert any("mock_provider_in_production_mode" in f for f in result["blocking_failures"])

    def test_non_strict_allows_provider_not_run(self, clean_bundle: Path) -> None:
        """In non-strict mode, PROVIDER_NOT_RUN doesn't block (but professor_ready stays False)."""
        reg_path = clean_bundle / "qa" / "stage_registry.json"
        reg_data = json.loads(reg_path.read_text())
        reg_data["stages"]["ENTITY_DETECT_GLINER"]["status"] = "PROVIDER_NOT_RUN"
        reg_path.write_text(json.dumps(reg_data))

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22", strict=False)
        # Gate may pass but professor_ready must be False
        assert result["professor_ready"] is False
        assert result["beta_status"] == "NOT_PROFESSOR_READY"

    def test_zip_with_private_path_blocks(self, clean_bundle: Path) -> None:
        # Recreate ZIP with a private path injected
        zip_path = clean_bundle / "exports" / "anonymized_bundle.zip"
        with zipfile.ZipFile(zip_path, "a") as zf:
            zf.writestr("private/secret.txt", "secret data")

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22")
        assert result["decision"] == "FAIL"
        assert any("zip_contains_excluded_path" in f for f in result["blocking_failures"])

    def test_empty_evidence_qa_pass_blocks(self, clean_bundle: Path) -> None:
        # Modify adversarial QA to have PASS with confidence=0.0 and no evidence
        qa_path = clean_bundle / "qa" / "adversarial_qa_report.json"
        qa_data = json.loads(qa_path.read_text())
        qa_data["overall_status"] = "PASS"
        qa_data["nvidia_review"] = {
            "confidence": 0.0,
            "evidence_cited": [],
        }
        qa_path.write_text(json.dumps(qa_data))

        result = evaluate_classroom_gate(clean_bundle, "2026-06-22")
        assert result["decision"] == "FAIL"
        assert any("empty_evidence_qa_pass" in f for f in result["blocking_failures"])
