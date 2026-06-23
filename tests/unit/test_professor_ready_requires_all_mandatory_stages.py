"""Tests for the professor-ready requirement: all mandatory stages must pass."""

from __future__ import annotations

from fenrix_synthetic.professor.stages import (
    ALL_MANDATORY_STAGES,
    STAGES_REQUIRING_EVIDENCE,
    BuildMode,
    ProfessorStage,
    ProviderKind,
    StageRegistry,
    StageStatus,
    StageStatusRecord,
)


def _make_registry(
    skip: set[ProfessorStage] | None = None,
    fail: set[ProfessorStage] | None = None,
    not_run: set[ProfessorStage] | None = None,
    zero_evidence: set[ProfessorStage] | None = None,
) -> StageRegistry:
    """Build a registry with configurable stage states."""
    reg = StageRegistry(build_mode=BuildMode.PRODUCTION)
    skip = skip or set()
    fail = fail or set()
    not_run = not_run or set()
    zero_evidence = zero_evidence or set()

    for stage in ALL_MANDATORY_STAGES:
        if stage in skip:
            continue
        if stage in fail:
            status = StageStatus.FAIL
        elif stage in not_run:
            status = StageStatus.PROVIDER_NOT_RUN
        else:
            status = StageStatus.PASS

        evidence = (
            0
            if stage in zero_evidence or status != StageStatus.PASS
            else (0 if stage not in STAGES_REQUIRING_EVIDENCE else 10)
        )

        reg.register(
            StageStatusRecord(
                stage=stage,
                status=status,
                evidence_count=evidence,
                provider_name="TestProvider",
                provider_kind=ProviderKind.REAL,
                provider_version="1.0",
                provider_config_hash="abc123",
            )
        )
    return reg


class TestProfessorReadyRequiresAllMandatoryStages:
    """Assert that professor_ready=true is impossible if any mandatory stage
    is missing, FAIL, PROVIDER_NOT_RUN, or has evidence_count=0."""

    def test_all_pass_makes_professor_ready(self) -> None:
        reg = _make_registry()
        assert reg.professor_ready is True

    def test_missing_any_single_stage_blocks(self) -> None:
        for stage in ALL_MANDATORY_STAGES:
            reg = _make_registry(skip={stage})
            assert reg.professor_ready is False, (
                f"professor_ready should be False when {stage.value} is missing"
            )

    def test_failed_any_single_stage_blocks(self) -> None:
        for stage in ALL_MANDATORY_STAGES:
            reg = _make_registry(fail={stage})
            assert reg.professor_ready is False, (
                f"professor_ready should be False when {stage.value} is FAIL"
            )

    def test_provider_not_run_any_single_stage_blocks(self) -> None:
        for stage in ALL_MANDATORY_STAGES:
            reg = _make_registry(not_run={stage})
            assert reg.professor_ready is False, (
                f"professor_ready should be False when {stage.value} is PROVIDER_NOT_RUN"
            )

    def test_zero_evidence_blocks(self) -> None:
        for stage in STAGES_REQUIRING_EVIDENCE:
            reg = _make_registry(zero_evidence={stage})
            assert reg.professor_ready is False, (
                f"professor_ready should be False when {stage.value} has evidence_count=0"
            )

    def test_multiple_failures_block(self) -> None:
        reg = _make_registry(
            fail={ProfessorStage.DEIDENTIFY, ProfessorStage.RELEASE_GATE},
            not_run={ProfessorStage.ENTITY_DETECT_GLINER},
        )
        assert reg.professor_ready is False

    def test_beta_status_never_professor_ready_when_not_run(self) -> None:
        reg = _make_registry(not_run={ProfessorStage.ADVERSARIAL_QA})
        assert reg.beta_status == "NOT_PROFESSOR_READY"

    def test_beta_status_never_professor_ready_when_failed(self) -> None:
        reg = _make_registry(fail={ProfessorStage.RELEASE_GATE})
        assert reg.beta_status == "NOT_PROFESSOR_READY"
