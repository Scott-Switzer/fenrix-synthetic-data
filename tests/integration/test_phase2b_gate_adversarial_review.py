"""Integration tests: adversarial review gate wiring."""

from __future__ import annotations

import json
from pathlib import Path

from fenrix_synthetic.professor.stages import (
    BuildMode,
    ProfessorStage,
    ProviderKind,
    StageRegistry,
    StageStatus,
    StageStatusRecord,
)


class TestAdversarialReviewReportRequired:
    """Gate must require adversarial_review_report.json."""

    def test_missing_review_report_is_blocker(self, tmp_path: Path) -> None:
        """Missing adversarial_review_report.json must block."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        output_dir.mkdir(parents=True)
        qa_dir = output_dir / "qa"
        qa_dir.mkdir(parents=True)
        public_dir = output_dir / "public"
        public_dir.mkdir(parents=True)

        _write_minimal_gate_files(qa_dir, public_dir)
        # Remove the review report to trigger missing-file blocker
        (qa_dir / "adversarial_review_report.json").unlink()

        registry = _make_pass_registry(BuildMode.FIXTURE)

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

        blockers = " ".join(gate_result["blocking_failures"])
        assert "adversarial_review_report" in blockers, (
            f"Expected adversarial_review_report blocker, got: {blockers}"
        )

    def test_review_report_blocks_on_direct_identifiers(self, tmp_path: Path) -> None:
        """Review report with direct_identifier_findings must block."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)

        _write_minimal_gate_files(qa_dir, public_dir)
        _write_review_report(qa_dir, release_recommendation="block", direct_identifiers=True)

        registry = _make_pass_registry(BuildMode.FIXTURE)

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

        blockers = " ".join(gate_result["blocking_failures"])
        assert "adversarial_review_blocks_release" in blockers, (
            f"Expected review block blocker, got: {blockers}"
        )

    def test_review_report_blocks_on_source_guess(self, tmp_path: Path) -> None:
        """Review report with guessed_source_identities must block."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)

        _write_minimal_gate_files(qa_dir, public_dir)
        _write_review_report(
            qa_dir,
            release_recommendation="review_required",
            guessed_identities=["HBAN", "Huntington Bancshares"],
        )

        registry = _make_pass_registry(BuildMode.FIXTURE)

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

        blockers = " ".join(gate_result["blocking_failures"])
        assert "adversarial_review_guessed_source" in blockers, (
            f"Expected source guess blocker, got: {blockers}"
        )

    def test_review_report_blocks_on_direct_identifier_findings(self, tmp_path: Path) -> None:
        """Review report with non-empty direct_identifier_findings blocks."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)

        _write_minimal_gate_files(qa_dir, public_dir)
        _write_review_report(
            qa_dir,
            release_recommendation="release",
            direct_identifiers=False,
            direct_identifier_findings_list=[
                {
                    "finding_id": "di-001",
                    "finding_type": "direct_identifier",
                    "severity": "blocking",
                    "evidence_span": "HBAN",
                    "confidence": 0.99,
                }
            ],
        )

        registry = _make_pass_registry(BuildMode.FIXTURE)

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

        blockers = " ".join(gate_result["blocking_failures"])
        assert "adversarial_review_found_direct_identifiers" in blockers, (
            f"Expected direct identifier blocker, got: {blockers}"
        )

    def test_clean_review_report_passes_gate(self, tmp_path: Path) -> None:
        """Clean review report with release recommendation should not block."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)

        _write_minimal_gate_files(qa_dir, public_dir)
        _write_review_report(qa_dir, release_recommendation="release")

        registry = _make_pass_registry(BuildMode.FIXTURE)

        gate_result = evaluate_classroom_gate(
            bundle_root=output_dir,
            release_date="2026-06-22",
            strict=False,
            stage_registry=registry,
        )

        blockers = " ".join(gate_result["blocking_failures"])
        assert "adversarial_review" not in blockers, (
            f"Unexpected adversarial_review blocker: {blockers}"
        )


class TestPhase2bProductionProviderEnforcement:
    """Production mode must require real review and metrics providers."""

    def test_production_blocks_mock_review_provider(self, tmp_path: Path) -> None:
        """Mock review provider in production must block."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        output_dir.mkdir(parents=True)

        registry = StageRegistry(build_mode=BuildMode.PRODUCTION)
        for stage in ProfessorStage:
            if stage == ProfessorStage.ADVERSARIAL_QA:
                registry.register(
                    StageStatusRecord(
                        stage=stage,
                        status=StageStatus.PASS,
                        provider_name="MockReviewProvider",
                        provider_kind=ProviderKind.FIXTURE,
                        is_production_provider=False,
                    )
                )
            elif stage in (ProfessorStage.METRIC_SYNTHESIS, ProfessorStage.METRIC_EVALUATION):
                registry.register(
                    StageStatusRecord(
                        stage=stage,
                        status=StageStatus.PASS,
                        provider_name="FixtureMetricsProvider",
                        provider_kind=ProviderKind.FIXTURE,
                        is_production_provider=False,
                    )
                )
            else:
                registry.register(
                    StageStatusRecord(
                        stage=stage,
                        status=StageStatus.PASS,
                        evidence_count=1,
                        provider_name="RealProvider",
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

        blockers = " ".join(gate_result["blocking_failures"])
        assert (
            "ADVERSARIAL_QA" in blockers
            or "METRIC_SYNTHESIS" in blockers
            or "METRIC_EVALUATION" in blockers
        ), f"Expected critical stage provider enforcement blocker, got: {blockers}"

    def test_production_allows_real_providers(self, tmp_path: Path) -> None:
        """Real providers for critical stages must not block on provider kind alone."""
        from fenrix_synthetic.release.classroom_gate import evaluate_classroom_gate

        output_dir = tmp_path / "bundle"
        qa_dir = output_dir / "qa"
        public_dir = output_dir / "public"
        qa_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)

        _write_minimal_gate_files(qa_dir, public_dir)
        _write_review_report(qa_dir, release_recommendation="release")

        registry = StageRegistry(build_mode=BuildMode.PRODUCTION)
        for stage in ProfessorStage:
            registry.register(
                StageStatusRecord(
                    stage=stage,
                    status=StageStatus.PASS,
                    evidence_count=1,
                    provider_name="RealProvider",
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

        provider_blockers = [
            b for b in gate_result["blocking_failures"] if "non_production_provider" in b
        ]
        assert len(provider_blockers) == 0, (
            f"Expected no provider blockers, got: {provider_blockers}"
        )


# ── Helpers ────────────────────────────────────────────────────────────────


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
    # Write checksums at bundle root (parent of qa_dir and public_dir)
    bundle_root = qa_dir.parent
    (bundle_root / "checksums.sha256").write_text("")


def _write_review_report(
    qa_dir: Path,
    *,
    release_recommendation: str = "release",
    direct_identifiers: bool = False,
    guessed_identities: list[str] | None = None,
    direct_identifier_findings_list: list[dict] | None = None,
) -> None:
    """Write an adversarial review report with specified properties."""
    report = {
        "report_id": "review-test-001",
        "provider_name": "mock_review",
        "provider_kind": "mock",
        "release_recommendation": release_recommendation,
        "succeeded": True,
        "bundle_level_risk_score": 0.05,
        "guessed_source_identities": guessed_identities or [],
        "direct_identifier_findings": direct_identifier_findings_list
        or (
            [{"finding_id": "di-001", "finding_type": "direct_identifier", "severity": "blocking"}]
            if direct_identifiers
            else []
        ),
        "semantic_clue_findings": [],
        "numeric_fingerprint_findings": [],
        "findings": [],
        "blockers": [],
    }
    (qa_dir / "adversarial_review_report.json").write_text(json.dumps(report, indent=2))


def _make_pass_registry(build_mode: BuildMode) -> StageRegistry:
    """Create a registry with all stages PASS."""
    registry = StageRegistry(build_mode=build_mode)
    for stage in ProfessorStage:
        registry.register(
            StageStatusRecord(
                stage=stage,
                status=StageStatus.PASS,
                evidence_count=1,
                provider_name="TestProvider",
                provider_kind=ProviderKind.FIXTURE,
                is_production_provider=(build_mode == BuildMode.PRODUCTION),
            )
        )
    return registry
