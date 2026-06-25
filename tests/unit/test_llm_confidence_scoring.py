"""Unit tests for LLM blind-guess confidence scoring."""

from __future__ import annotations

from fenrix_synthetic.qa.confidence_scoring import (
    PrivateScoreDetail,
    ScoreVerdict,
    _is_company_match,
    redact_for_public,
    score_blind_guess,
)


class TestScoreBlindGuess:
    """Test confidence scoring against various model response scenarios."""

    def test_pass_when_model_refuses(self) -> None:
        """No justified guess → target pass."""
        response = {
            "confidence": "none",
            "most_likely_company": None,
            "most_likely_ticker": None,
            "top_candidates": [],
            "evidence_summary": "Insufficient evidence.",
            "refusal_or_uncertain": True,
        }
        result = score_blind_guess(response, actual_source_company="Acme Corp")
        assert result.private.verdict == ScoreVerdict.PASS
        assert result.public.verdict == ScoreVerdict.PASS
        assert result.public.model_refused is True

    def test_pass_low_confidence_no_match(self) -> None:
        """Low confidence with no actual source in candidates → pass."""
        response = {
            "confidence": "low",
            "most_likely_company": "Generic Corp",
            "most_likely_ticker": "GEN",
            "top_candidates": [
                {
                    "company": "Generic Corp",
                    "ticker": "GEN",
                    "confidence": "low",
                    "evidence": ["broad sector"],
                },
            ],
            "evidence_summary": "Insufficient evidence.",
            "refusal_or_uncertain": False,
        }
        result = score_blind_guess(response, actual_source_company="Acme Corp")
        assert result.private.verdict == ScoreVerdict.PASS

    def test_fail_top1_exact_match(self) -> None:
        """Exact top-1 hit → hard fail."""
        response = {
            "confidence": "high",
            "most_likely_company": "Canary Holdings Corporation",
            "most_likely_ticker": "CHC",
            "top_candidates": [
                {
                    "company": "Canary Holdings Corporation",
                    "ticker": "CHC",
                    "confidence": "high",
                    "evidence": ["distinctive patterns"],
                },
            ],
            "evidence_summary": "Identified with high confidence.",
            "refusal_or_uncertain": False,
        }
        result = score_blind_guess(
            response,
            actual_source_company="Canary Holdings Corporation",
            actual_source_ticker="CHC",
        )
        assert result.private.verdict == ScoreVerdict.FAIL
        assert result.private.top1_is_actual is True
        assert result.public.verdict == ScoreVerdict.FAIL

    def test_fail_top3_match(self) -> None:
        """Actual source in top-3 → hard fail."""
        response = {
            "confidence": "medium",
            "most_likely_company": "First Bank",
            "most_likely_ticker": "FBK",
            "top_candidates": [
                {"company": "First Bank", "ticker": "FBK", "confidence": "medium", "evidence": []},
                {"company": "Second Corp", "ticker": "SEC", "confidence": "medium", "evidence": []},
                {
                    "company": "Canary Holdings Corporation",
                    "ticker": "CHC",
                    "confidence": "medium",
                    "evidence": [],
                },
            ],
            "evidence_summary": "Multiple candidates.",
            "refusal_or_uncertain": False,
        }
        result = score_blind_guess(
            response,
            actual_source_company="Canary Holdings Corporation",
            actual_source_ticker="CHC",
        )
        assert result.private.verdict == ScoreVerdict.FAIL
        assert result.private.actual_in_top3 is True

    def test_fail_high_confidence(self) -> None:
        """High confidence fails even if wrong company."""
        response = {
            "confidence": "high",
            "most_likely_company": "Wrong Company Inc",
            "most_likely_ticker": "WRG",
            "top_candidates": [
                {
                    "company": "Wrong Company Inc",
                    "ticker": "WRG",
                    "confidence": "high",
                    "evidence": [],
                },
            ],
            "evidence_summary": "High confidence guess.",
            "refusal_or_uncertain": False,
        }
        result = score_blind_guess(response, actual_source_company="Acme Corp")
        assert result.private.verdict == ScoreVerdict.FAIL

    def test_fail_medium_with_actual_in_candidates(self) -> None:
        """Medium confidence + actual source in candidates → fail."""
        response = {
            "confidence": "medium",
            "most_likely_company": "First Guess",
            "most_likely_ticker": "FGS",
            "top_candidates": [
                {"company": "First Guess", "ticker": "FGS", "confidence": "medium", "evidence": []},
                {
                    "company": "Canary Holdings Corporation",
                    "ticker": "CHC",
                    "confidence": "medium",
                    "evidence": [],
                },
            ],
            "evidence_summary": "Moderate confidence.",
            "refusal_or_uncertain": False,
        }
        result = score_blind_guess(
            response,
            actual_source_company="Canary Holdings Corporation",
        )
        assert result.private.verdict == ScoreVerdict.FAIL
        assert result.private.actual_in_candidates is True

    def test_warn_medium_without_actual(self) -> None:
        """Medium confidence without actual source → warn."""
        response = {
            "confidence": "medium",
            "most_likely_company": "Some Other Corp",
            "most_likely_ticker": "SOC",
            "top_candidates": [
                {
                    "company": "Some Other Corp",
                    "ticker": "SOC",
                    "confidence": "medium",
                    "evidence": [],
                },
            ],
            "evidence_summary": "Moderate confidence.",
            "refusal_or_uncertain": False,
        }
        result = score_blind_guess(
            response,
            actual_source_company="Canary Holdings Corporation",
        )
        assert result.private.verdict == ScoreVerdict.WARN

    def test_public_summary_excludes_source(self) -> None:
        """Public summary must not include actual source mapping."""
        response = {
            "confidence": "high",
            "most_likely_company": "Canary Holdings Corporation",
            "most_likely_ticker": "CHC",
            "top_candidates": [
                {
                    "company": "Canary Holdings Corporation",
                    "ticker": "CHC",
                    "confidence": "high",
                    "evidence": [],
                },
            ],
            "evidence_summary": "Identified.",
            "refusal_or_uncertain": False,
        }
        result = score_blind_guess(
            response,
            actual_source_company="Canary Holdings Corporation",
            actual_source_ticker="CHC",
        )
        public = result.public.to_dict()
        # The public summary should NOT contain 'actual_source_company' or 'actual_source_ticker' keys
        public_str = str(public)
        assert "actual_source_company" not in public_str
        assert "actual_source_ticker" not in public_str


class TestRedaction:
    """Test that redaction removes actual source info."""

    def test_redact_for_public_removes_source(self) -> None:
        private = PrivateScoreDetail(
            verdict=ScoreVerdict.FAIL,
            reason="Model correctly identified source as top-1: Canary Holdings Corporation (CHC)",
            actual_source_company="Canary Holdings Corporation",
            actual_source_ticker="CHC",
            top1_is_actual=True,
        )
        public = redact_for_public(private)
        assert "Canary" not in public.reason
        assert "CHC" not in public.reason


class TestCompanyMatching:
    """Test the private company matching logic."""

    def test_exact_match(self) -> None:
        assert _is_company_match("Acme Corp", "ACM", "Acme Corp", "ACM") is True

    def test_case_insensitive_match(self) -> None:
        assert _is_company_match("acme corp", None, "Acme Corp", None) is True

    def test_substring_match(self) -> None:
        assert _is_company_match("Acme Corporation", None, "Acme Corp", None) is True

    def test_ticker_only_match(self) -> None:
        assert _is_company_match(None, "CHC", None, "CHC") is True

    def test_no_match(self) -> None:
        assert _is_company_match("Different Corp", "DIF", "Acme Corp", "ACM") is False

    def test_none_inputs(self) -> None:
        assert _is_company_match(None, None, "Acme Corp", None) is False
        assert _is_company_match(None, None, None, None) is False
