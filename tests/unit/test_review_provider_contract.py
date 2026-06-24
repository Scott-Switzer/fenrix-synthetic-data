"""Contract tests for ReviewProvider protocol implementations."""

from __future__ import annotations

import pytest

from fenrix_synthetic.professor.review_providers import (
    MockReviewProvider,
    NVIDIAReviewProvider,
    ReviewArtifact,
    ReviewFinding,
    ReviewPolicy,
    ReviewReport,
    create_review_provider,
    default_review_policy,
)


class TestReviewArtifact:
    """ReviewArtifact model validation."""

    def test_basic_artifact(self) -> None:
        artifact = ReviewArtifact(
            artifact_id="sec-001",
            artifact_type="sec_section",
            content="Company 001 reported revenue of $10M.",
            company_id="COMPANY_001",
        )
        assert artifact.artifact_id == "sec-001"
        assert artifact.artifact_type == "sec_section"

    def test_artifact_with_metadata(self) -> None:
        artifact = ReviewArtifact(
            artifact_id="sec-001",
            artifact_type="sec_section",
            content="text",
            metadata={"source_filing": "10-K", "year": 2024},
        )
        assert artifact.metadata["source_filing"] == "10-K"

    def test_artifact_rejects_extra_fields(self) -> None:
        with pytest.raises(ValueError):
            ReviewArtifact(
                artifact_id="test",
                artifact_type="sec_section",
                content="text",
                invalid_field="value",  # type: ignore
            )


class TestReviewPolicy:
    """ReviewPolicy model validation."""

    def test_default_policy(self) -> None:
        policy = default_review_policy()
        assert policy.policy_id == "adversarial_review_v1"
        assert policy.block_on_direct_identifier is True
        assert policy.block_on_likely_source_identity is True
        assert policy.max_bundle_level_risk_score == 0.50

    def test_custom_policy(self) -> None:
        policy = ReviewPolicy(
            policy_id="custom_policy",
            policy_version="2.0.0",
            max_bundle_level_risk_score=0.30,
            block_on_direct_identifier=True,
        )
        assert policy.max_bundle_level_risk_score == 0.30

    def test_policy_rejects_extra_fields(self) -> None:
        with pytest.raises(ValueError):
            ReviewPolicy(
                policy_id="test",
                policy_version="1.0",
                invalid=True,  # type: ignore
            )


class TestReviewFinding:
    """ReviewFinding model validation."""

    def test_blocking_finding(self) -> None:
        finding = ReviewFinding(
            finding_id="find-001",
            finding_type="direct_identifier",
            severity="blocking",
            evidence_span="NVIDIA Corporation",
            confidence=0.95,
        )
        assert finding.severity == "blocking"

    def test_warning_finding(self) -> None:
        finding = ReviewFinding(
            finding_id="find-002",
            finding_type="semantic_clue",
            severity="warning",
            description="Industry-specific language detected",
        )
        assert finding.severity == "warning"


class TestReviewReport:
    """ReviewReport model validation."""

    def test_basic_report(self) -> None:
        report = ReviewReport(
            report_id="review-001",
            provider_name="mock_review",
            provider_kind="mock",
            release_recommendation="release",
        )
        assert report.succeeded is True
        assert report.release_recommendation == "release"

    def test_report_with_findings(self) -> None:
        report = ReviewReport(
            report_id="review-002",
            provider_name="nvidia_review",
            provider_kind="real",
            release_recommendation="block",
            blockers=["Direct identifier found"],
            direct_identifier_findings=[
                ReviewFinding(
                    finding_id="find-001",
                    finding_type="direct_identifier",
                    severity="blocking",
                    evidence_span="HBAN",
                    confidence=0.99,
                )
            ],
        )
        assert len(report.blockers) == 1
        assert report.release_recommendation == "block"

    def test_report_serializes(self) -> None:
        report = ReviewReport(
            report_id="review-003",
            provider_name="test",
            provider_kind="mock",
            release_recommendation="release",
        )
        dumped = report.model_dump()
        assert dumped["report_id"] == "review-003"
        assert dumped["succeeded"] is True


class TestMockReviewProvider:
    """MockReviewProvider contract tests."""

    @pytest.fixture
    def provider(self) -> MockReviewProvider:
        return MockReviewProvider()

    @pytest.fixture
    def sample_artifacts(self) -> list[ReviewArtifact]:
        return [
            ReviewArtifact(
                artifact_id="sec-item7",
                artifact_type="sec_section",
                content="Revenue increased 12% year-over-year.",
                company_id="COMPANY_001",
            ),
            ReviewArtifact(
                artifact_id="sec-item8",
                artifact_type="sec_section",
                content="Total assets were $150B.",
                company_id="COMPANY_001",
            ),
        ]

    def test_health_check(self, provider: MockReviewProvider) -> None:
        assert provider.health_check() is True

    def test_provider_properties(self, provider: MockReviewProvider) -> None:
        assert provider.provider_name == "mock_review"
        assert provider.provider_kind == "mock"
        assert provider.model_id == "mock-review-model-v1"

    def test_review_artifacts_returns_report(
        self, provider: MockReviewProvider, sample_artifacts: list[ReviewArtifact]
    ) -> None:
        policy = default_review_policy()
        report = provider.review_artifacts(sample_artifacts, policy=policy, run_id="test-run-001")
        assert isinstance(report, ReviewReport)
        assert report.succeeded is True
        assert report.release_recommendation == "release"
        assert report.artifacts_reviewed == 2

    def test_review_low_risk_scores(
        self, provider: MockReviewProvider, sample_artifacts: list[ReviewArtifact]
    ) -> None:
        policy = default_review_policy()
        report = provider.review_artifacts(sample_artifacts, policy=policy, run_id="test-run-002")
        assert report.bundle_level_risk_score <= 0.10
        for score in report.artifact_level_risk_scores.values():
            assert score <= 0.10

    def test_empty_artifacts(self, provider: MockReviewProvider) -> None:
        policy = default_review_policy()
        report = provider.review_artifacts([], policy=policy, run_id="test-empty")
        assert report.artifacts_reviewed == 0
        assert report.succeeded is True


class TestNVIDIAReviewProvider:
    """NVIDIAReviewProvider contract tests (no API key)."""

    def test_missing_key_health_check(self) -> None:
        import os

        original_key = os.environ.get("NVIDIA_API_KEY")
        if original_key:
            del os.environ["NVIDIA_API_KEY"]
        try:
            provider = NVIDIAReviewProvider(api_key="")
            assert provider.health_check() is False
        finally:
            if original_key:
                os.environ["NVIDIA_API_KEY"] = original_key

    def test_missing_key_properties(self) -> None:
        provider = NVIDIAReviewProvider(api_key="")
        assert provider.provider_name == "nvidia_review"
        assert provider.provider_kind == "real"
        assert provider.model_id == "meta/llama-3.1-70b-instruct"

    def test_missing_key_review_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        provider = NVIDIAReviewProvider(api_key="")
        with pytest.raises((RuntimeError, ModuleNotFoundError)):
            provider.review_artifacts([], policy=default_review_policy(), run_id="test")


class TestCreateReviewProvider:
    """Factory function tests."""

    def test_create_mock(self) -> None:
        provider = create_review_provider("mock")
        assert isinstance(provider, MockReviewProvider)

    def test_create_nvidia(self) -> None:
        provider = create_review_provider("nvidia", {"api_key": "test-key"})
        assert isinstance(provider, NVIDIAReviewProvider)

    def test_create_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown review provider"):
            create_review_provider("unknown")
