"""Unit tests for LLM blind-guess harness and providers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fenrix_synthetic.qa.llm_blind_guess import (
    BlindGuessResult,
    LLMBlindGuessHarness,
    collect_public_content,
    _is_private_path,
)
from fenrix_synthetic.qa.llm_provider import (
    LLMProviderError,
    OfflineStubProvider,
    StubConfig,
    _extract_json,
    create_llm_provider,
)


class TestJsonExtraction:
    """Test JSON extraction from model output."""

    def test_direct_json(self) -> None:
        result = _extract_json('{"confidence": "low"}')
        assert result == {"confidence": "low"}

    def test_fenced_json(self) -> None:
        result = _extract_json('```json\n{"confidence": "low"}\n```')
        assert result == {"confidence": "low"}

    def test_malformed_returns_none(self) -> None:
        result = _extract_json("not json at all")
        assert result is None

    def test_empty_returns_none(self) -> None:
        result = _extract_json("")
        assert result is None


class TestOfflineStubProvider:
    """Test the offline stub provider."""

    def test_returns_configured_response(self) -> None:
        stub = OfflineStubProvider(StubConfig.pass_case())
        result = stub.complete_json("ignored prompt")
        assert result["confidence"] == "low"
        assert result["most_likely_company"] is None
        assert result["refusal_or_uncertain"] is True

    def test_exact_top1_hit_config(self) -> None:
        stub = OfflineStubProvider(StubConfig.exact_top1_hit())
        result = stub.complete_json("ignored")
        assert result["confidence"] == "high"
        assert result["most_likely_company"] == "Canary Holdings Corporation"

    def test_top3_hit_config(self) -> None:
        stub = OfflineStubProvider(StubConfig.top3_hit())
        result = stub.complete_json("ignored")
        assert result["confidence"] == "medium"
        assert len(result["top_candidates"]) == 3

    def test_high_confidence_config(self) -> None:
        stub = OfflineStubProvider(StubConfig.high_confidence())
        result = stub.complete_json("ignored")
        assert result["confidence"] == "high"

    def test_malformed_config_raises(self) -> None:
        stub = OfflineStubProvider(StubConfig.malformed_output())
        with pytest.raises(LLMProviderError):
            stub.complete_json("ignored")

    def test_provider_error_config_raises(self) -> None:
        stub = OfflineStubProvider(StubConfig.provider_error())
        with pytest.raises(LLMProviderError):
            stub.complete_json("ignored")

    def test_medium_with_actual(self) -> None:
        stub = OfflineStubProvider(StubConfig.medium_with_actual())
        result = stub.complete_json("ignored")
        assert result["confidence"] == "medium"

    def test_medium_without_actual(self) -> None:
        stub = OfflineStubProvider(StubConfig.medium_without_actual())
        result = stub.complete_json("ignored")
        assert result["confidence"] == "medium"

    def test_default_is_pass_case(self) -> None:
        stub = OfflineStubProvider()
        result = stub.complete_json("ignored")
        assert result["confidence"] == "low"
        assert result["most_likely_company"] is None


class TestProviderFactory:
    """Test LLM provider factory."""

    def test_create_offline_stub(self) -> None:
        provider = create_llm_provider("offline_stub")
        assert provider.provider_name == "offline_stub"
        result = provider.complete_json("test")
        assert "confidence" in result

    def test_create_offline_stub_with_mode(self) -> None:
        provider = create_llm_provider("offline_stub", {"stub_mode": "fail_top1"})
        result = provider.complete_json("test")
        assert result["confidence"] == "high"

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown LLM provider type"):
            create_llm_provider("unknown_provider")

    def test_stub_modes(self) -> None:
        modes = [
            "fail_top1",
            "fail_top3",
            "fail_high_confidence",
            "fail_medium_with_actual",
            "warn_medium_without_actual",
            "error_malformed",
            "error_provider",
        ]
        for mode in modes:
            provider = create_llm_provider("offline_stub", {"stub_mode": mode})
            if "error" in mode:
                with pytest.raises(LLMProviderError):
                    provider.complete_json("test")
            else:
                result = provider.complete_json("test")
                assert "confidence" in result


class TestPrivatePathDetection:
    """Test that private paths are correctly detected."""

    def test_private_dir_blocked(self) -> None:
        assert _is_private_path("private/something.json") is True

    def test_raw_dir_blocked(self) -> None:
        assert _is_private_path("raw/filing.html") is True

    def test_identity_blocked(self) -> None:
        assert _is_private_path("identity/map.json") is True

    def test_public_path_allowed(self) -> None:
        assert _is_private_path("public/anonymized/COMPANY_001/profile/profile.md") is False

    def test_qa_path_allowed(self) -> None:
        assert _is_private_path("qa/release_gate.json") is False

    def test_llm_private_blocked(self) -> None:
        assert _is_private_path("qa/llm_blind_guess_private.json") is True


class TestPublicContentCollection:
    """Test that public content collection reads only public files."""

    def test_collects_public_files(self, tmp_path: Path) -> None:
        public_dir = tmp_path / "public" / "anonymized" / "COMPANY_001"
        public_dir.mkdir(parents=True)
        (public_dir / "profile.md").write_text("Anonymized company profile.")

        content = collect_public_content(tmp_path / "public", "COMPANY_001")
        assert "Anonymized company profile" in content

    def test_skips_private_files(self, tmp_path: Path) -> None:
        public_dir = tmp_path / "public" / "anonymized" / "COMPANY_001"
        public_dir.mkdir(parents=True)
        (public_dir / "profile.md").write_text("Public content.")

        # Create a private-like file in public area
        private_dir = tmp_path / "public" / "private"
        private_dir.mkdir(parents=True)
        (private_dir / "secret.json").write_text("SECRET")

        content = collect_public_content(tmp_path / "public", "COMPANY_001")
        assert "Public content" in content
        assert "SECRET" not in content


class TestLLMBlindGuessHarness:
    """Test the overall harness."""

    def test_review_with_offline_stub_pass(self, tmp_path: Path) -> None:
        """Offline stub pass case produces low/no confidence."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"
        qa_dir = tmp_path / "qa"

        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Anonymized company profile.")

        provider = OfflineStubProvider(StubConfig.pass_case())
        harness = LLMBlindGuessHarness(provider, strict=True)

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
        )
        assert result.passed is True
        assert result.provider_error is None
        assert result.parse_error is None

        # Check public summary written
        harness.write_public_summary(result, qa_dir)
        summary_path = qa_dir / "llm_blind_guess_summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text())
        assert summary["passed"] is True

    def test_review_with_offline_stub_fail_top1(self, tmp_path: Path) -> None:
        """Offline stub exact top-1 hit fails."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"

        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Anonymized profile.")

        provider = OfflineStubProvider(StubConfig.exact_top1_hit())
        harness = LLMBlindGuessHarness(provider, strict=True)

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
            actual_source_company="Canary Holdings Corporation",
        )
        assert result.passed is False

    def test_review_with_provider_error_strict(self, tmp_path: Path) -> None:
        """Provider error fails closed in strict mode."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"

        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Content.")

        provider = OfflineStubProvider(StubConfig.provider_error())
        harness = LLMBlindGuessHarness(provider, strict=True)

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
        )
        assert result.passed is False
        assert result.provider_error is not None

    def test_review_with_provider_error_nonstrict(self, tmp_path: Path) -> None:
        """Provider error warns in non-strict mode."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"

        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Content.")

        provider = OfflineStubProvider(StubConfig.provider_error())
        harness = LLMBlindGuessHarness(provider, strict=False)

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
        )
        # Non-strict mode passes with warning
        assert result.passed is True

    def test_public_summary_excludes_private(self, tmp_path: Path) -> None:
        """Public summary should not include actual source mapping keys."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"
        qa_dir = tmp_path / "qa"

        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Anonymized profile.")

        provider = OfflineStubProvider(StubConfig.exact_top1_hit())
        harness = LLMBlindGuessHarness(provider, strict=True)

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
            actual_source_company="Canary Holdings Corporation",
        )
        harness.write_public_summary(result, qa_dir)

        summary = json.loads((qa_dir / "llm_blind_guess_summary.json").read_text())
        summary_str = json.dumps(summary)
        # Public summary should NOT contain actual_source keys
        # (it may contain the model's guess, which is public by design)
        assert "actual_source_company" not in summary_str
        assert "actual_source_ticker" not in summary_str

    def test_private_report_includes_scoring_details(self, tmp_path: Path) -> None:
        """Private report includes scoring details with actual source."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"

        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Content.")

        provider = OfflineStubProvider(StubConfig.exact_top1_hit())
        harness = LLMBlindGuessHarness(provider, strict=True)

        result = harness.review(
            public_dir=public_dir,
            private_dir=private_dir,
            company_id="COMPANY_001",
            actual_source_company="Canary Holdings Corporation",
        )
        # Private report should exist
        private_report = private_dir / "qa" / "llm_blind_guess_private.json"
        assert private_report.exists()

    def test_llm_stage_uses_only_public_files(self, tmp_path: Path) -> None:
        """LLM stage must not read private audit folders."""
        public_dir = tmp_path / "public"
        private_dir = tmp_path / "private"

        company_dir = public_dir / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text("Anonymized content only.")

        # Create a private file that would reveal source
        private_qa = private_dir / "qa"
        private_qa.mkdir(parents=True)
        (private_qa / "source_identity.json").write_text("SECRET: Canary Holdings")

        provider = OfflineStubProvider(StubConfig.pass_case())
        harness = LLMBlindGuessHarness(provider)

        # Content collection should not include private files
        content = collect_public_content(public_dir, "COMPANY_001")
        assert "SECRET" not in content
        assert "Canary" not in content
