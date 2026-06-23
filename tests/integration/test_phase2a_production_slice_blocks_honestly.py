"""Integration test: production config blocks honestly."""

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


class TestPhase2aProductionSliceBlocksHonestly:
    """Verify production configuration produces PRODUCTION_BLOCKED gate report."""

    @pytest.fixture
    def production_config_path(self) -> Path:
        """Path to production example config."""
        return (
            Path(__file__).parent.parent.parent
            / "configs"
            / "professor_bundle.production.example.yaml"
        )

    @pytest.fixture
    def output_dir(self, tmp_path: Path) -> Path:
        """Temporary output directory."""
        return tmp_path / "production_output"

    def test_production_config_exists(self, production_config_path: Path) -> None:
        """Production example config must exist."""
        assert production_config_path.exists(), (
            f"Production config not found at {production_config_path}"
        )

    def test_production_gate_blocks_with_real_registry(self, output_dir: Path) -> None:
        """Production gate must produce PRODUCTION_BLOCKED with explicit blocker list."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir.mkdir(parents=True, exist_ok=True)

        # Build a STAGE registry representing a production build where GLiNER failed
        registry = StageRegistry(build_mode=BuildMode.PRODUCTION)

        # Register all 19 stages. Most PASS, but ENTITY_DETECT_GLINER is PROVIDER_NOT_RUN
        for stage in ProfessorStage:
            if stage == ProfessorStage.ENTITY_DETECT_GLINER:
                registry.register(
                    StageStatusRecord(
                        stage=stage,
                        status=StageStatus.PROVIDER_NOT_RUN,
                        failures=["GLiNER provider not available"],
                        provider_name="",
                        provider_kind=ProviderKind.SKIPPED,
                        is_production_provider=False,
                    )
                )
            elif stage in (
                ProfessorStage.METRIC_SYNTHESIS,
                ProfessorStage.METRIC_EVALUATION,
                ProfessorStage.ADVERSARIAL_QA,
            ):
                # Stages that may be PROVIDER_NOT_RUN in realistic production
                registry.register(
                    StageStatusRecord(
                        stage=stage,
                        status=StageStatus.PROVIDER_NOT_RUN,
                        provider_name="",
                        provider_kind=ProviderKind.SKIPPED,
                        is_production_provider=False,
                    )
                )
            else:
                registry.register(
                    StageStatusRecord(
                        stage=stage,
                        status=StageStatus.PASS,
                        evidence_count=1,
                        provider_name="TestProvider",
                        provider_kind=ProviderKind.REAL,
                        is_production_provider=True,
                    )
                )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=True,
            stage_registry=registry,
        )

        assert gate_result["decision"] == "FAIL", (
            f"Expected FAIL, got {gate_result['decision']}: {gate_result.get('blocking_failures')}"
        )
        assert gate_result["beta_status"] == "PRODUCTION_BLOCKED"
        assert len(gate_result["blocking_failures"]) > 0

    def test_production_blockers_listed(self, output_dir: Path) -> None:
        """Blockers must be explicitly listed."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir.mkdir(parents=True, exist_ok=True)
        registry = StageRegistry(build_mode=BuildMode.PRODUCTION)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PROVIDER_NOT_RUN,
                    provider_kind=ProviderKind.SKIPPED,
                    is_production_provider=False,
                )
            )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=True,
            stage_registry=registry,
        )

        for blocker in gate_result["blocking_failures"]:
            assert isinstance(blocker, str)
            assert len(blocker) > 0

    def test_non_strict_production_gate_does_not_block(self, output_dir: Path) -> None:
        """Non-strict production gate should not produce PRODUCTION_BLOCKED."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir.mkdir(parents=True, exist_ok=True)
        registry = StageRegistry(build_mode=BuildMode.PRODUCTION)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PROVIDER_NOT_RUN,
                    provider_kind=ProviderKind.SKIPPED,
                    is_production_provider=False,
                )
            )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

        assert gate_result["beta_status"] != "PRODUCTION_BLOCKED"

    def test_strict_gate_blocks_on_missing_gliner(self, output_dir: Path) -> None:
        """Missing GLiNER must be a blocker in strict mode."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir.mkdir(parents=True, exist_ok=True)
        registry = StageRegistry(build_mode=BuildMode.PRODUCTION)
        for stage in ProfessorStage:
            if stage == ProfessorStage.ENTITY_DETECT_GLINER:
                registry.register(
                    StageStatusRecord(
                        stage=stage,
                        status=StageStatus.PROVIDER_NOT_RUN,
                        provider_kind=ProviderKind.SKIPPED,
                        is_production_provider=False,
                    )
                )
            else:
                registry.register(
                    StageStatusRecord(
                        stage=stage,
                        status=StageStatus.PASS,
                        evidence_count=1,
                        provider_name="TestProvider",
                        provider_kind=ProviderKind.REAL,
                        is_production_provider=True,
                    )
                )

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=True,
            stage_registry=registry,
        )

        blocker_text = " ".join(gate_result["blocking_failures"])
        assert "gliner" in blocker_text.lower() or "entity_detect" in blocker_text.lower()

    def test_release_safe_false_when_blocked(self, output_dir: Path) -> None:
        """release_safe must be False when gate blocks."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir.mkdir(parents=True, exist_ok=True)
        registry = StageRegistry(build_mode=BuildMode.PRODUCTION)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PROVIDER_NOT_RUN,
                    provider_kind=ProviderKind.SKIPPED,
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
        assert gate_result["professor_ready"] is False
