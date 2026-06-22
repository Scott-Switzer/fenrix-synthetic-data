"""Tests for the professor-bundle stage registry."""

from __future__ import annotations

from fenrix_synthetic.professor.stages import (
    ALL_MANDATORY_STAGES,
    STAGES_REQUIRING_EVIDENCE,
    ProfessorStage,
    StageRegistry,
    StageStatus,
    StageStatusRecord,
)


def _all_pass_registry() -> StageRegistry:
    """Build a registry where all stages PASS with evidence."""
    reg = StageRegistry()
    for stage in ALL_MANDATORY_STAGES:
        reg.register(
            StageStatusRecord(
                stage=stage,
                status=StageStatus.PASS,
                evidence_count=10 if stage in STAGES_REQUIRING_EVIDENCE else 0,
            )
        )
    return reg


class TestStageRegistryRequiredStages:
    def test_all_18_stages_defined(self) -> None:
        assert len(ALL_MANDATORY_STAGES) == 19  # 18 + ZIP_EXPORT

    def test_all_stages_pass_makes_professor_ready(self) -> None:
        reg = _all_pass_registry()
        assert reg.professor_ready is True
        assert reg.beta_status == "PROFESSOR_READY"

    def test_missing_stage_blocks_professor_ready(self) -> None:
        reg = _all_pass_registry()
        del reg._records[ProfessorStage.ADVERSARIAL_QA]
        assert reg.professor_ready is False
        assert reg.all_stages_present is False

    def test_failed_stage_blocks_professor_ready(self) -> None:
        reg = _all_pass_registry()
        reg.register(
            StageStatusRecord(
                stage=ProfessorStage.DEIDENTIFY,
                status=StageStatus.FAIL,
                evidence_count=0,
                failures=["test failure"],
            )
        )
        assert reg.professor_ready is False
        assert reg.all_stages_pass is False

    def test_provider_not_run_blocks_professor_ready(self) -> None:
        reg = _all_pass_registry()
        reg.register(
            StageStatusRecord(
                stage=ProfessorStage.ENTITY_DETECT_GLINER,
                status=StageStatus.PROVIDER_NOT_RUN,
                evidence_count=0,
            )
        )
        assert reg.professor_ready is False
        assert reg.has_provider_not_run is True

    def test_zero_evidence_blocks_professor_ready(self) -> None:
        reg = _all_pass_registry()
        reg.register(
            StageStatusRecord(
                stage=ProfessorStage.SOURCE_INGESTION,
                status=StageStatus.PASS,
                evidence_count=0,
            )
        )
        assert reg.professor_ready is False
        assert reg.has_evidence_gaps is True

    def test_beta_status_not_professor_ready_when_provider_skipped(self) -> None:
        reg = _all_pass_registry()
        reg.register(
            StageStatusRecord(
                stage=ProfessorStage.ADVERSARIAL_QA,
                status=StageStatus.PROVIDER_NOT_RUN,
                evidence_count=0,
            )
        )
        assert reg.beta_status == "NOT_PROFESSOR_READY"

    def test_registry_serializes_to_dict(self) -> None:
        reg = _all_pass_registry()
        data = reg.to_dict()
        assert "professor_ready" in data
        assert "beta_status" in data
        assert "stages" in data
        assert len(data["stages"]) == 19
