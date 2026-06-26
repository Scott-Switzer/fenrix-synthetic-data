"""Unit tests: V3.1 decoy-aware LLM review.

Covers the prompt builder, scoring function, and orchestrator integration.
All tests are offline (stub provider) — no network calls.
"""

from __future__ import annotations

import json

from src.fenrix_synthetic.qa.confidence_scoring import (
    _DIRECT_LEAK_BASES,
    ScoreVerdict,
    score_decoy_aware_guess,
)
from src.fenrix_synthetic.qa.llm_provider import (
    _DECOY_SYSTEM_PROMPT,
    OfflineStubProvider,
    StubConfig,
    _build_decoy_aware_review_prompt,
)

# ── Helpers ──────────────────────────────────────────────────────────

_CANDIDATE_LABELS: list[str] = [
    "Candidate A",
    "Candidate B",
    "Candidate C",
    "Candidate D",
    "Candidate E",
]


def _make_label_map(
    actual_label: str = "Candidate B",
) -> dict[str, tuple[str, str | None]]:
    """Build a private label map with the true source at `actual_label`."""
    return {
        "Candidate A": ("PepsiCo Inc", "PEP"),
        "Candidate B": ("The Coca-Cola Company", "KO"),
        "Candidate C": ("Keurig Dr Pepper Inc", "KDP"),
        "Candidate D": ("Monster Beverage Corporation", "MNST"),
        "Candidate E": ("Constellation Brands Inc", "STZ"),
    }


# ── Prompt builder tests ─────────────────────────────────────────────


class TestDecoyPromptBuilder:
    """Verify the decoy-aware prompt has only opaque labels, not real names."""

    def test_prompt_contains_only_opaque_labels(self) -> None:
        content = "# Anonymized Company Profile\n\nThis is a test."
        prompt = _build_decoy_aware_review_prompt(content, "COMPANY_001", _CANDIDATE_LABELS)

        # Must have Candidate labels
        for label in _CANDIDATE_LABELS:
            assert label in prompt, f"Missing label: {label}"

        # Must NOT have any real company names from the peer pool
        forbidden = [
            "Coca-Cola", "PepsiCo", "Keurig", "Monster", "Constellation",
            "Procter", "Unilever", "Amazon", "Apple",
        ]
        for name in forbidden:
            assert name not in prompt, f"Real company name leaked into prompt: {name}"

    def test_prompt_contains_required_schema_keys(self) -> None:
        content = "Test content."
        prompt = _build_decoy_aware_review_prompt(content, "COMPANY_TEST", _CANDIDATE_LABELS)

        required = [
            "top_guess_label",
            "top_guess_confidence",
            "top_3_labels",
            "evidence",
            "inference_basis",
            "would_identify_exact_source",
            "direct_leak_detected",
        ]
        for key in required:
            assert key in prompt, f"Missing schema key in prompt: {key}"

    def test_prompt_includes_company_id(self) -> None:
        content = "Test."
        prompt = _build_decoy_aware_review_prompt(content, "COMPANY_007", _CANDIDATE_LABELS)
        assert "COMPANY_007" in prompt

    def test_system_prompt_exists_and_has_no_real_names(self) -> None:
        assert len(_DECOY_SYSTEM_PROMPT) > 100
        forbidden = ["Coca-Cola", "PepsiCo", "Apple"]
        for name in forbidden:
            assert name not in _DECOY_SYSTEM_PROMPT


# ── Scoring tests: PASS scenarios ────────────────────────────────────


class TestDecoyScoringPass:
    """Verify PASS verdicts when true source is not identified."""

    def test_pass_wrong_guess_low_confidence(self) -> None:
        label_map = _make_label_map("Candidate B")  # true source = B
        response = {
            "top_guess_label": "Candidate D",
            "top_guess_confidence": "low",
            "top_3_labels": ["Candidate D", "Candidate A", "Candidate C"],
            "evidence": [{"claim": "Broad match.", "basis": "business_model"}],
            "inference_basis": "business_model",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate B",
            private_label_map=label_map,
            company_id="TEST",
        )
        assert result.public.verdict == ScoreVerdict.PASS
        assert result.private.verdict == ScoreVerdict.PASS
        assert result.public.top_guess_is_actual is False
        assert result.public.actual_in_top3 is False

    def test_pass_wrong_guess_medium_confidence(self) -> None:
        label_map = _make_label_map("Candidate A")
        response = {
            "top_guess_label": "Candidate C",
            "top_guess_confidence": "medium",
            "top_3_labels": ["Candidate C", "Candidate E", "Candidate D"],
            "evidence": [{"claim": "Sector match.", "basis": "sector_only"}],
            "inference_basis": "sector_only",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate A",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.PASS

    def test_pass_source_not_in_top3(self) -> None:
        label_map = _make_label_map("Candidate E")
        response = {
            "top_guess_label": "Candidate A",
            "top_guess_confidence": "high",
            "top_3_labels": ["Candidate A", "Candidate B", "Candidate C"],
            "evidence": [{"claim": "Financial pattern match.", "basis": "financial_pattern"}],
            "inference_basis": "financial_pattern",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate E",
            private_label_map=label_map,
        )
        # High confidence on a wrong guess, source not in top-3 = PASS
        assert result.public.verdict == ScoreVerdict.PASS


# ── Scoring tests: FAIL scenarios ────────────────────────────────────


class TestDecoyScoringFail:
    """Verify FAIL verdicts when privacy boundary is breached."""

    def test_fail_top1_high_confidence(self) -> None:
        label_map = _make_label_map("Candidate A")
        response = {
            "top_guess_label": "Candidate A",
            "top_guess_confidence": "high",
            "top_3_labels": ["Candidate A", "Candidate B", "Candidate D"],
            "evidence": [{"claim": "Match.", "basis": "business_model"}],
            "inference_basis": "business_model",
            "would_identify_exact_source": True,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate A",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.FAIL
        assert result.private.top1_is_actual is True

    def test_fail_top1_medium_confidence(self) -> None:
        label_map = _make_label_map("Candidate B")
        response = {
            "top_guess_label": "Candidate B",
            "top_guess_confidence": "medium",
            "top_3_labels": ["Candidate B", "Candidate A", "Candidate C"],
            "evidence": [{"claim": "Match.", "basis": "financial_pattern"}],
            "inference_basis": "financial_pattern",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate B",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.FAIL

    def test_fail_direct_identifier_evidence(self) -> None:
        label_map = _make_label_map("Candidate C")
        response = {
            "top_guess_label": "Candidate D",
            "top_guess_confidence": "low",
            "top_3_labels": ["Candidate D", "Candidate A", "Candidate C"],
            "evidence": [
                {"claim": "Ticker survived anonymization.", "basis": "direct_identifier"},
            ],
            "inference_basis": "direct_identifier",
            "would_identify_exact_source": False,
            "direct_leak_detected": True,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate C",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.FAIL
        assert result.public.direct_leak_detected is True

    def test_fail_exact_number_evidence(self) -> None:
        label_map = _make_label_map("Candidate A")
        response = {
            "top_guess_label": "Candidate B",
            "top_guess_confidence": "low",
            "top_3_labels": ["Candidate B", "Candidate D", "Candidate E"],
            "evidence": [
                {"claim": "Revenue matches known figure.", "basis": "exact_number"},
            ],
            "inference_basis": "exact_number",
            "would_identify_exact_source": False,
            "direct_leak_detected": True,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate A",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.FAIL

    def test_fail_product_event_fingerprint_evidence(self) -> None:
        label_map = _make_label_map("Candidate D")
        response = {
            "top_guess_label": "Candidate B",
            "top_guess_confidence": "medium",
            "top_3_labels": ["Candidate B", "Candidate D", "Candidate C"],
            "evidence": [
                {"claim": "Product launch sequence matches.", "basis": "product_event_fingerprint"},
            ],
            "inference_basis": "product_event_fingerprint",
            "would_identify_exact_source": False,
            "direct_leak_detected": True,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate D",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.FAIL
        assert result.public.direct_leak_detected is True

    def test_fail_top3_high_confidence(self) -> None:
        label_map = _make_label_map("Candidate C")
        response = {
            "top_guess_label": "Candidate A",
            "top_guess_confidence": "high",
            "top_3_labels": ["Candidate A", "Candidate B", "Candidate C"],
            "evidence": [{"claim": "Match.", "basis": "business_model"}],
            "inference_basis": "business_model",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate C",
            private_label_map=label_map,
        )
        # True source in top-3 with high confidence = FAIL
        assert result.public.verdict == ScoreVerdict.FAIL


# ── Scoring tests: WARN scenarios ────────────────────────────────────


class TestDecoyScoringWarn:
    """Verify WARN verdicts for borderline cases."""

    def test_warn_top3_low_confidence_business_model_only(self) -> None:
        label_map = _make_label_map("Candidate E")
        response = {
            "top_guess_label": "Candidate A",
            "top_guess_confidence": "low",
            "top_3_labels": ["Candidate A", "Candidate E", "Candidate C"],
            "evidence": [
                {"claim": "Sector and business model are consistent.", "basis": "business_model"},
            ],
            "inference_basis": "business_model",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate E",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.WARN
        assert result.public.actual_in_top3 is True

    def test_top3_low_with_financial_pattern_evidence_warns(self) -> None:
        """Top-3 with low confidence and financial_pattern evidence → WARN.

        financial_pattern is treated as non-leak (acceptable for WARN)
        alongside business_model and sector_only.
        """
        label_map = _make_label_map("Candidate B")
        response = {
            "top_guess_label": "Candidate D",
            "top_guess_confidence": "low",
            "top_3_labels": ["Candidate D", "Candidate A", "Candidate B"],
            "evidence": [
                {"claim": "Margin structure diagnostic.", "basis": "financial_pattern"},
            ],
            "inference_basis": "financial_pattern",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate B",
            private_label_map=label_map,
        )
        # financial_pattern is treated as broad-enough for WARN in Rule 4
        assert result.public.verdict == ScoreVerdict.WARN


# ── Public summary safety tests ──────────────────────────────────────


class TestDecoyPublicSafety:
    """Verify redacted public summaries contain no real company names."""

    def test_public_summary_has_no_source_names(self) -> None:
        label_map = _make_label_map("Candidate A")
        response = {
            "top_guess_label": "Candidate B",
            "top_guess_confidence": "low",
            "top_3_labels": ["Candidate B", "Candidate D", "Candidate E"],
            "evidence": [{"claim": "Broad match.", "basis": "business_model"}],
            "inference_basis": "business_model",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate A",
            private_label_map=label_map,
        )
        public_dict = result.public.to_dict()

        # Must NOT contain real company names or tickers
        for name in ["Coca-Cola", "PepsiCo", "Keurig", "KO", "PEP"]:
            json_str = json.dumps(public_dict)
            assert name not in json_str, f"Real company name leaked into public summary: {name}"

        # Must NOT contain label-to-company mapping
        assert "private_label_map" not in public_dict
        assert "candidate_mapping" not in public_dict

    def test_public_summary_has_evidence_basis_counts(self) -> None:
        label_map = _make_label_map("Candidate D")
        response = {
            "top_guess_label": "Candidate A",
            "top_guess_confidence": "low",
            "top_3_labels": ["Candidate A", "Candidate B", "Candidate C"],
            "evidence": [
                {"claim": "A", "basis": "business_model"},
                {"claim": "B", "basis": "business_model"},
                {"claim": "C", "basis": "financial_pattern"},
            ],
            "inference_basis": "mixed",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate D",
            private_label_map=label_map,
        )
        counts = result.public.evidence_basis_counts
        assert counts.get("business_model", 0) == 2
        assert counts.get("financial_pattern", 0) == 1

    def test_private_detail_contains_source_mapping(self) -> None:
        """Private detail must contain the actual source name (for internal audit)."""
        label_map = _make_label_map("Candidate B")
        response = {
            "top_guess_label": "Candidate A",
            "top_guess_confidence": "low",
            "top_3_labels": ["Candidate A", "Candidate D", "Candidate E"],
            "evidence": [{"claim": "x.", "basis": "business_model"}],
            "inference_basis": "business_model",
            "would_identify_exact_source": False,
            "direct_leak_detected": False,
        }
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate B",
            private_label_map=label_map,
        )
        assert result.private.actual_source_company == "The Coca-Cola Company"
        assert result.private.actual_source_ticker == "KO"
        assert result.private.actual_source_label == "Candidate B"


# ── Direct leak base tests ───────────────────────────────────────────


class TestDirectLeakBases:
    """Verify the direct leak bases constant includes required types."""

    def test_product_event_fingerprint_is_direct_leak(self) -> None:
        assert "product_event_fingerprint" in _DIRECT_LEAK_BASES

    def test_direct_identifier_is_direct_leak(self) -> None:
        assert "direct_identifier" in _DIRECT_LEAK_BASES

    def test_exact_number_is_direct_leak(self) -> None:
        assert "exact_number" in _DIRECT_LEAK_BASES

    def test_metadata_leak_is_direct_leak(self) -> None:
        assert "metadata_leak" in _DIRECT_LEAK_BASES


# ── Stub provider tests ──────────────────────────────────────────────


class TestDecoyStubProvider:
    """Verify offline stub produces correct decoy-format responses."""

    def test_decoy_stub_returns_decoy_schema(self) -> None:
        config = StubConfig.decoy_pass_low_confidence()
        provider = OfflineStubProvider(config)
        response = provider.complete_json("any prompt")

        assert "top_guess_label" in response
        assert "top_3_labels" in response
        assert "evidence" in response
        assert "inference_basis" in response
        assert response.get("top_guess_label") == "Candidate D"
        assert response.get("top_guess_confidence") == "low"

    def test_decoy_stub_fail_direct_leak(self) -> None:
        config = StubConfig.decoy_fail_direct_leak()
        provider = OfflineStubProvider(config)
        response = provider.complete_json("any prompt")

        assert response.get("direct_leak_detected") is True
        # Evidence should include product_event_fingerprint or exact_number
        bases = [e.get("basis") for e in response.get("evidence", [])]
        assert any(b in _DIRECT_LEAK_BASES for b in bases)

    def test_decoy_stub_warn_business_model(self) -> None:
        config = StubConfig.decoy_warn_business_model()
        provider = OfflineStubProvider(config)
        response = provider.complete_json("any prompt")

        assert response.get("top_guess_confidence") == "low"
        assert "Candidate A" in response.get("top_3_labels", [])

    def test_empty_decoy_response_falls_back_to_blind_format(self) -> None:
        config = StubConfig.pass_case()  # no decoy_response set
        provider = OfflineStubProvider(config)
        response = provider.complete_json("any prompt")

        # Should be blind-review format, not decoy format
        assert "most_likely_company" in response
        assert "top_candidates" in response
        assert "top_guess_label" not in response


# ── Integration-like: stub round-trip ────────────────────────────────


class TestDecoyStubRoundTrip:
    """End-to-end: stub produces decoy response → scoring scores it."""

    def test_pass_round_trip(self) -> None:
        config = StubConfig.decoy_pass_low_confidence()
        provider = OfflineStubProvider(config)
        response = provider.complete_json("prompt")

        # Stub says top_guess = Candidate D, top_3 = [D, B, A]
        # True source = Candidate E → not in top-3 at all → PASS
        label_map = _make_label_map("Candidate E")
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate E",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.PASS

    def test_fail_top1_round_trip(self) -> None:
        config = StubConfig.decoy_fail_top1_high_confidence()
        provider = OfflineStubProvider(config)
        response = provider.complete_json("prompt")

        label_map = _make_label_map("Candidate A")  # stub says top_guess = Candidate A
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate A",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.FAIL
        assert result.private.top1_is_actual is True

    def test_fail_direct_leak_round_trip(self) -> None:
        config = StubConfig.decoy_fail_direct_leak()
        provider = OfflineStubProvider(config)
        response = provider.complete_json("prompt")

        label_map = _make_label_map("Candidate B")
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate B",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.FAIL
        assert result.public.direct_leak_detected is True

    def test_warn_round_trip(self) -> None:
        config = StubConfig.decoy_warn_business_model()
        provider = OfflineStubProvider(config)
        response = provider.complete_json("prompt")

        # Stub says Candidate B is top-1, Candidate A is top-3
        # True source = Candidate A → A is in top-3 with low confidence, business model only
        label_map = _make_label_map("Candidate A")
        result = score_decoy_aware_guess(
            response,
            actual_source_label="Candidate A",
            private_label_map=label_map,
        )
        assert result.public.verdict == ScoreVerdict.WARN
