"""Regression test: fixture build must not regress."""

from __future__ import annotations

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


class TestPhase2aFixtureRegression:
    """Verify fixture build returns strict_fixture_ready=true, professor_ready=false."""

    @pytest.fixture
    def output_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "fixture_output"

    def _write_required_gate_files(self, output_dir: Path) -> None:
        """Write required QA reports and classroom docs for gate to pass."""
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True, exist_ok=True)
        public_dir.mkdir(parents=True, exist_ok=True)

        required_qa = [
            "stage_registry.json",
            "entity_audit_report.json",
            "metrics_quality_report.json",
            "metrics_privacy_report.json",
            "metrics_schema_report.json",
            "rag_index_report.json",
            "adversarial_qa_report.json",
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

        (output_dir / "checksums.sha256").write_text("")

    def test_fixture_gate_professor_ready_false(self, output_dir: Path) -> None:
        """Fixture build must return professor_ready=false."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir.mkdir(parents=True, exist_ok=True)
        self._write_required_gate_files(output_dir)

        # Build a registry representing a successful fixture build
        registry = StageRegistry(build_mode=BuildMode.FIXTURE)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PASS,
                    evidence_count=1,
                    provider_name="FixtureProvider",
                    provider_kind=ProviderKind.FIXTURE,
                    is_production_provider=False,
                )
            )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=True,
            stage_registry=registry,
        )

        # In fixture mode, PASS decision is expected
        decision = gate_result.get("decision", "")
        assert decision != "FAIL" or not gate_result.get("blocking_failures"), (
            f"Fixture build failed: {gate_result.get('blocking_failures')}"
        )

        # professor_ready must be False in fixture mode
        assert gate_result.get("professor_ready") is False
        # strict_fixture_ready should be True if all stages PASS
        assert gate_result.get("strict_fixture_ready") is True

    def test_fixture_gate_correct_non_production_conditions(self, output_dir: Path) -> None:
        """Fixture build must report non-production conditions."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir.mkdir(parents=True, exist_ok=True)
        self._write_required_gate_files(output_dir)
        registry = StageRegistry(build_mode=BuildMode.FIXTURE)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PASS,
                    evidence_count=1,
                    provider_name="FixtureProvider",
                    provider_kind=ProviderKind.FIXTURE,
                    is_production_provider=False,
                )
            )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=True,
            stage_registry=registry,
        )

        conditions = gate_result.get("non_production_conditions", [])
        assert "build_mode_is_fixture" in conditions
        assert "mock_provider_used" in conditions

    def test_fixture_release_safe_false(self, output_dir: Path) -> None:
        """Fixture build must have release_safe=false."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir.mkdir(parents=True, exist_ok=True)
        self._write_required_gate_files(output_dir)
        registry = StageRegistry(build_mode=BuildMode.FIXTURE)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PASS,
                    evidence_count=1,
                    provider_name="FixtureProvider",
                    provider_kind=ProviderKind.FIXTURE,
                    is_production_provider=False,
                )
            )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=True,
            stage_registry=registry,
        )

        assert gate_result["release_safe"] is False

    def test_fixture_gate_correct_beta_status(self, output_dir: Path) -> None:
        """Fixture build must have STRICT_FIXTURE_READY beta status."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir.mkdir(parents=True, exist_ok=True)
        self._write_required_gate_files(output_dir)
        registry = StageRegistry(build_mode=BuildMode.FIXTURE)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PASS,
                    evidence_count=1,
                    provider_name="FixtureProvider",
                    provider_kind=ProviderKind.FIXTURE,
                    is_production_provider=False,
                )
            )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=True,
            stage_registry=registry,
        )

        assert gate_result["beta_status"] == "STRICT_FIXTURE_READY"
