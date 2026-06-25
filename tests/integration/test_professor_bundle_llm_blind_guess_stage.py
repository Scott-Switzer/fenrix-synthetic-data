"""Integration tests for the LLM_BLIND_GUESS professor bundle stage."""

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
from fenrix_synthetic.qa.llm_blind_guess import (
    LLMBlindGuessHarness,
    collect_public_content,
)
from fenrix_synthetic.qa.llm_provider import (
    OfflineStubProvider,
    StubConfig,
    create_llm_provider,
)
from fenrix_synthetic.qa.confidence_scoring import (
    ScoreVerdict,
    score_blind_guess,
)


class TestLLMBlindGuessStage:
    """Integration tests for LLM blind guess stage behavior."""

    def test_stage_can_pass_with_offline_stub(self, tmp_path: Path) -> None:
        """Bundle stage can pass with offline stub."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"

        # Create minimal public content
        company_dir = public_dir / "anonymized" / "COMPANY_001" / "profile"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text(
            "# Company 001\n\nA diversified financial services company.\n"
        )

        provider = OfflineStubProvider(StubConfig.pass_case())
        harness = LLMBlindGuessHarness(provider, strict=True)

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
        )
        assert result.passed is True

    def test_stage_fails_with_offline_exact_hit_stub(self, tmp_path: Path) -> None:
        """Bundle stage fails with offline exact-hit stub."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"

        company_dir = public_dir / "anonymized" / "COMPANY_001" / "profile"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Anonymized content.")

        provider = OfflineStubProvider(StubConfig.exact_top1_hit())
        harness = LLMBlindGuessHarness(provider, strict=True)

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
            actual_source_company="Canary Holdings Corporation",
            actual_source_ticker="CHC",
        )
        assert result.passed is False

    def test_llm_stage_does_not_read_private_audit(self, tmp_path: Path) -> None:
        """LLM stage does not read private audit folders."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"

        # Public content
        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Anonymized content only.")

        # Private content with source identity
        private_qa = private_dir / "qa"
        private_qa.mkdir(parents=True)
        (private_qa / "source_identity.json").write_text(
            '{"actual_company": "Canary Holdings Corporation", "ticker": "CHC"}'
        )

        # Collect content — should NOT include private files
        content = collect_public_content(public_dir, "COMPANY_001")
        assert "Canary" not in content
        assert "CHC" not in content

    def test_final_zip_includes_llm_summary(self, tmp_path: Path) -> None:
        """Final ZIP includes redacted llm_blind_guess_summary.json."""
        qa_dir = tmp_path / "qa"
        qa_dir.mkdir(parents=True)

        provider = OfflineStubProvider(StubConfig.pass_case())
        harness = LLMBlindGuessHarness(provider)

        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"
        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Public content.")

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
        )
        summary_path = harness.write_public_summary(result, qa_dir)

        assert summary_path.exists()
        assert summary_path.name == "llm_blind_guess_summary.json"
        # Public summary should not contain private details
        data = json.loads(summary_path.read_text())
        assert "actual_source" not in str(data).lower()

    def test_final_zip_excludes_private_llm_report(self, tmp_path: Path) -> None:
        """Final ZIP excludes private LLM report."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"
        qa_dir = tmp_path / "qa"

        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Content.")

        provider = OfflineStubProvider(StubConfig.pass_case())
        harness = LLMBlindGuessHarness(provider)

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
            actual_source_company="Canary Holdings Corporation",
        )
        # Private report should exist in private dir
        private_report = private_dir / "qa" / "llm_blind_guess_private.json"
        assert private_report.exists()

        # Public summary should exist in qa dir
        harness.write_public_summary(result, qa_dir)
        public_summary = qa_dir / "llm_blind_guess_summary.json"
        assert public_summary.exists()

        # The private report path should not be in the allowlisted qa area
        assert not (qa_dir / "llm_blind_guess_private.json").exists()

    def test_stage_registry_includes_llm_blind_guess(self) -> None:
        """LLM_BLIND_GUESS stage is registered in the professor stages."""
        registry = StageRegistry(build_mode=BuildMode.FIXTURE)
        registry.register(
            StageStatusRecord(
                stage=ProfessorStage.LLM_BLIND_GUESS,
                status=StageStatus.PASS,
                evidence_count=1,
                provider_name="offline_stub",
                provider_kind=ProviderKind.FIXTURE,
            )
        )
        record = registry.get(ProfessorStage.LLM_BLIND_GUESS)
        assert record is not None
        assert record.status == StageStatus.PASS


class TestScoringIntegration:
    """Test that scoring integrates correctly with blind guess results."""

    def test_pass_scenario(self) -> None:
        """Target: low confidence, no justified guess."""
        response = {
            "confidence": "low",
            "most_likely_company": None,
            "most_likely_ticker": None,
            "top_candidates": [
                {
                    "company": "Generic Corp",
                    "ticker": "GEN",
                    "confidence": "low",
                    "evidence": ["broad sector only"],
                },
            ],
            "evidence_summary": "Insufficient evidence.",
            "refusal_or_uncertain": True,
        }
        result = score_blind_guess(
            response,
            actual_source_company="Canary Holdings Corporation",
            actual_source_ticker="CHC",
        )
        assert result.private.verdict == ScoreVerdict.PASS

    def test_high_confidence_fails(self) -> None:
        """High confidence should fail even without actual match."""
        response = {
            "confidence": "high",
            "most_likely_company": "Some Corp",
            "most_likely_ticker": "SOM",
            "top_candidates": [
                {
                    "company": "Some Corp",
                    "ticker": "SOM",
                    "confidence": "high",
                    "evidence": ["unique business model"],
                },
            ],
            "evidence_summary": "High confidence guess.",
            "refusal_or_uncertain": False,
        }
        result = score_blind_guess(
            response,
            actual_source_company="Canary Holdings Corporation",
        )
        assert result.private.verdict == ScoreVerdict.FAIL

    def test_medium_confidence_with_actual_fails(self) -> None:
        """Medium confidence with actual source in candidates fails."""
        response = {
            "confidence": "medium",
            "most_likely_company": "First Corp",
            "most_likely_ticker": "FST",
            "top_candidates": [
                {
                    "company": "First Corp",
                    "ticker": "FST",
                    "confidence": "medium",
                    "evidence": ["revenue similarity"],
                },
                {
                    "company": "Canary Holdings Corporation",
                    "ticker": "CHC",
                    "confidence": "medium",
                    "evidence": ["asset pattern match"],
                },
            ],
            "evidence_summary": "Several plausible matches.",
            "refusal_or_uncertain": False,
        }
        result = score_blind_guess(
            response,
            actual_source_company="Canary Holdings Corporation",
        )
        assert result.private.verdict == ScoreVerdict.FAIL
