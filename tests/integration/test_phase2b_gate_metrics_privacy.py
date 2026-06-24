"""Integration tests: metrics privacy gate wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fenrix_synthetic.professor.stages import (
    BuildMode,
    ProfessorStage,
    ProviderKind,
    StageRegistry,
    StageStatus,
    StageStatusRecord,
)


class TestMetricsPrivacyGate:
    """Gate must enforce metrics privacy report checks."""

    PRIVACY_FIELDS = [
        ("fixed_template", "metrics_fixed_template"),
        ("exact_value_leakage", "metrics_exact_value_leakage"),
        ("identical_distributions", "metrics_identical_distributions"),
        ("suspicious_correlation", "metrics_suspicious_correlation"),
    ]

    def _run_gate_with_privacy(self, tmp_path: Path, privacy_overrides: dict) -> dict:
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)

        _write_minimal_gate_files(qa_dir, public_dir)

        # Write a metrics privacy report with the specified overrides
        privacy_report: dict = {
            "fixed_template": False,
            "identical_distributions": False,
            "exact_value_leakage": False,
            "suspicious_correlation": False,
            "privacy_score": 0.95,
            "warnings": [],
        }
        privacy_report.update(privacy_overrides)
        (qa_dir / "metrics_privacy_report.json").write_text(json.dumps(privacy_report, indent=2))

        registry = StageRegistry(build_mode=BuildMode.FIXTURE)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PASS,
                    evidence_count=1,
                    provider_name="TestProvider",
                    provider_kind=ProviderKind.FIXTURE,
                    is_production_provider=False,
                )
            )

        return evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

    @pytest.mark.parametrize("field,expected_blocker", PRIVACY_FIELDS)
    def test_privacy_flag_blocks(self, tmp_path: Path, field: str, expected_blocker: str) -> None:
        """Each privacy flag set to True must block."""
        gate_result = self._run_gate_with_privacy(tmp_path, {field: True})
        blockers = " ".join(gate_result["blocking_failures"])
        assert expected_blocker in blockers, (
            f"Expected {expected_blocker!r} in blockers for field {field!r}, got: {blockers}"
        )

    def test_low_privacy_score_blocks(self, tmp_path: Path) -> None:
        """Privacy score below 0.50 must block."""
        gate_result = self._run_gate_with_privacy(tmp_path, {"privacy_score": 0.30})
        blockers = " ".join(gate_result["blocking_failures"])
        assert "metrics_privacy_below_threshold" in blockers

    def test_high_privacy_score_passes(self, tmp_path: Path) -> None:
        """Clean privacy report should not block."""
        gate_result = self._run_gate_with_privacy(tmp_path, {"privacy_score": 0.90})
        privacy_blockers = [b for b in gate_result["blocking_failures"] if "metrics" in b.lower()]
        assert len(privacy_blockers) == 0, f"Expected no privacy blockers, got: {privacy_blockers}"

    def test_missing_privacy_report_is_blocker(self, tmp_path: Path) -> None:
        """Missing metrics_privacy_report.json must block."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)

        # Write everything except metrics_privacy_report
        _write_minimal_gate_files(qa_dir, public_dir)
        # Remove the privacy report
        (qa_dir / "metrics_privacy_report.json").unlink()

        registry = StageRegistry(build_mode=BuildMode.FIXTURE)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PASS,
                    evidence_count=1,
                    provider_name="TestProvider",
                    provider_kind=ProviderKind.FIXTURE,
                    is_production_provider=False,
                )
            )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

        blockers = " ".join(gate_result["blocking_failures"])
        assert "metrics_privacy" in blockers, f"Expected metrics_privacy blocker, got: {blockers}"


class TestMetricsQualityGate:
    """Gate must enforce metrics quality report checks."""

    def test_low_quality_score_blocks(self, tmp_path: Path) -> None:
        """Quality score below 0.30 must block."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)

        _write_minimal_gate_files(qa_dir, public_dir)

        quality_report = {"quality_score": 0.15, "schema_valid": False}
        (qa_dir / "metrics_quality_report.json").write_text(json.dumps(quality_report, indent=2))

        registry = StageRegistry(build_mode=BuildMode.FIXTURE)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PASS,
                    evidence_count=1,
                    provider_name="TestProvider",
                    provider_kind=ProviderKind.FIXTURE,
                    is_production_provider=False,
                )
            )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

        blockers = " ".join(gate_result["blocking_failures"])
        assert "metrics_quality_below_threshold" in blockers, (
            f"Expected quality threshold blocker, got: {blockers}"
        )

    def test_high_quality_score_passes(self, tmp_path: Path) -> None:
        """High quality score should not block."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)

        _write_minimal_gate_files(qa_dir, public_dir)

        quality_report = {"quality_score": 0.85, "schema_valid": True}
        (qa_dir / "metrics_quality_report.json").write_text(json.dumps(quality_report, indent=2))

        registry = StageRegistry(build_mode=BuildMode.FIXTURE)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PASS,
                    evidence_count=1,
                    provider_name="TestProvider",
                    provider_kind=ProviderKind.FIXTURE,
                    is_production_provider=False,
                )
            )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

        quality_blockers = [b for b in gate_result["blocking_failures"] if "metrics_quality" in b]
        assert len(quality_blockers) == 0, f"Expected no quality blockers, got: {quality_blockers}"


def _write_minimal_gate_files(qa_dir: Path, public_dir: Path) -> None:
    """Write minimal QA reports and classroom docs for gate to pass."""
    required_qa = [
        "stage_registry.json",
        "entity_audit_report.json",
        "metrics_quality_report.json",
        "metrics_privacy_report.json",
        "metrics_schema_report.json",
        "rag_index_report.json",
        "adversarial_qa_report.json",
        "adversarial_review_report.json",
    ]
    for fname in required_qa:
        (qa_dir / fname).write_text("{}")

    required_docs = [
        "README.md",
        "CLASSROOM_GUIDE.md",
        "PROFESSOR_AUDIT_GUIDE.md",
        "EXERCISES.md",
        "ANSWER_KEY_STUB.md",
        "RUBRIC.md",
    ]
    for fname in required_docs:
        (public_dir / fname).write_text("# placeholder")

    company_dir = public_dir / "anonymized" / "COMPANY_001"
    company_dir.mkdir(parents=True, exist_ok=True)
    (company_dir / "LEARNING_GUIDE.md").write_text("# placeholder")
    (company_dir / "crosslinks.json").write_text("[]")
    bundle_root = qa_dir.parent
    (bundle_root / "checksums.sha256").write_text("")
