"""Unit tests for utility preservation scoring."""

from __future__ import annotations

import json
from pathlib import Path

from fenrix_synthetic.qa.utility_preservation import (
    CompanyThesis,
    extract_public_thesis,
    score_utility_preservation,
    write_utility_reports,
)


class TestUtilityScoring:
    """Test utility preservation scoring against source theses."""

    def test_pass_when_broad_thesis_matches(self) -> None:
        """Utility score passes when broad thesis matches."""
        source = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking and lending",
            product_exposure=["consumer banking", "commercial banking"],
            fundamentals_signal="strong",
            valuation_signal="low",
            profitability_signal="strong",
            balance_sheet_signal="strong",
            growth_signal="positive",
            risk_signals=["regulatory", "credit risk"],
            market_signal="value",
        )
        public = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking and lending",
            product_exposure=["consumer banking", "commercial banking"],
            fundamentals_signal="strong",
            valuation_signal="low",
            profitability_signal="strong",
            balance_sheet_signal="strong",
            growth_signal="positive",
            risk_signals=["regulatory", "credit risk"],
            market_signal="value",
        )
        result = score_utility_preservation(source, public)
        assert result.private.verdict == "PASS"
        assert result.private.overall_utility_score >= 0.70

    def test_warn_when_partially_preserved(self) -> None:
        """Utility score warns when partially preserved."""
        source = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking and lending",
            product_exposure=["consumer banking", "commercial banking"],
            fundamentals_signal="strong",
            valuation_signal="low",
            profitability_signal="strong",
            balance_sheet_signal="strong",
            growth_signal="positive",
            risk_signals=["regulatory", "credit risk"],
            market_signal="value",
        )
        public = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking and lending",  # matches
            product_exposure=["consumer banking"],  # partial
            fundamentals_signal="mixed",  # different
            valuation_signal="unknown",  # different
            profitability_signal="mixed",  # different
            balance_sheet_signal="strong",  # matches
            growth_signal="positive",  # matches
            risk_signals=["regulatory"],  # partial
            market_signal="value",  # matches
        )
        result = score_utility_preservation(source, public)
        assert result.private.verdict in ("WARN", "PASS")
        assert 0.40 < result.private.overall_utility_score < 0.90

    def test_fail_when_thesis_lost(self) -> None:
        """Utility score fails when sanitized output loses the thesis."""
        source = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking and lending",
            product_exposure=["consumer banking", "commercial banking"],
            fundamentals_signal="strong",
            valuation_signal="low",
            profitability_signal="strong",
            balance_sheet_signal="strong",
            growth_signal="positive",
            risk_signals=["regulatory", "credit risk"],
            market_signal="value",
        )
        public = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="technology",  # wrong
            product_exposure=["software"],  # wrong
            fundamentals_signal="weak",  # opposite
            valuation_signal="high",  # opposite
            profitability_signal="mixed",
            balance_sheet_signal="mixed",
            growth_signal="negative",
            risk_signals=["competition"],
            market_signal="momentum",
        )
        result = score_utility_preservation(source, public)
        assert result.private.verdict == "FAIL"
        assert result.private.overall_utility_score < 0.55

    def test_public_summary_excludes_source_identity(self) -> None:
        """Public utility summary excludes source identity."""
        source = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking",
            fundamentals_signal="strong",
        )
        public = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking",
            fundamentals_signal="strong",
        )
        result = score_utility_preservation(source, public)
        public_dict = result.public.to_dict()
        # Should not contain source company/ticker info
        assert "source_company" not in str(public_dict).lower()
        assert "source_ticker" not in str(public_dict).lower()

    def test_signals_preserved_and_lost(self) -> None:
        """Public summary correctly reports preserved and lost signals."""
        source = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking",
            fundamentals_signal="strong",
            product_exposure=["consumer"],
        )
        public = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking",
            fundamentals_signal="strong",
            product_exposure=["commercial"],
        )
        result = score_utility_preservation(source, public)
        assert "business_model" in result.public.signals_preserved
        assert "fundamentals" in result.public.signals_preserved
        assert "product_exposure" in result.public.signals_lost


class TestExtractPublicThesis:
    """Test thesis extraction from public outputs."""

    def test_extracts_from_public_dir(self, tmp_path: Path) -> None:
        """Extracts thesis from public directory content."""
        company_dir = tmp_path / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text(
            "A diversified financial services company focused on banking and lending. "
            "The company demonstrates strong fundamentals with growing revenue. "
            "Regulatory compliance and credit risk management are key focus areas. "
            "The stock appears undervalued relative to peers."
        )

        thesis = extract_public_thesis(tmp_path, "COMPANY_001")
        assert thesis.business_model == "banking and lending"
        assert thesis.fundamentals_signal == "strong"
        assert thesis.valuation_signal == "low"
        assert "regulatory" in thesis.risk_signals

    def test_extracts_insurance_business(self, tmp_path: Path) -> None:
        company_dir = tmp_path / "anonymized" / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "profile.md").write_text(
            "An insurance company focused on underwriting and premium growth."
        )
        thesis = extract_public_thesis(tmp_path, "COMPANY_001")
        assert thesis.business_model == "insurance"


class TestWriteReports:
    """Test report writing."""

    def test_writes_private_and_public(self, tmp_path: Path) -> None:
        source = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking",
        )
        public = CompanyThesis(
            anonymized_company_id="COMPANY_001",
            business_model="banking",
        )
        result = score_utility_preservation(source, public)

        private_dir = tmp_path / "private" / "qa"
        qa_dir = tmp_path / "qa"
        priv_path, pub_path = write_utility_reports(result, private_dir, qa_dir)

        assert priv_path.exists()
        assert pub_path.exists()

        # Private report should exist in private dir
        assert "private" in str(priv_path)
        # Public report in qa dir
        assert pub_path.name == "utility_preservation_summary.json"
        data = json.loads(pub_path.read_text())
        assert data["verdict"] == "PASS"
