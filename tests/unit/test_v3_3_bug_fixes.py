"""V3.3 bug-fix regression tests.

Tests for:
- Bug A: Privacy-cap race condition (utility must use blind/decoy summaries)
- Bug B: PASS_WITH_WAIVER reachable (on_merits field)
- Bug C: Blind confidence levels distinguished (high vs medium vs low)
"""

from __future__ import annotations

from fenrix_synthetic.qa.utility_audit import (
    PRIVACY_CAP_DIRECT_LEAK,
    PRIVACY_CAP_NONE,
    PRIVACY_CAP_TOP1_HIGH_MEDIUM,
    PRIVACY_CAP_TOP3_HIGH_MEDIUM,
    PRIVACY_CAP_TOP3_LOW_ONLY,
    UtilityAuditResult,
    UtilityVerdict,
    compute_privacy_cap_from_blind_decoy,
    score_v3_utility,
)
from fenrix_synthetic.qa.volume_gate import (
    VOLUME_FAIL,
    VOLUME_PASS,
    VOLUME_PASS_WITH_WAIVER,
    evaluate_volume_gate,
)

# ── Bug C: Blind confidence ignored ──────────────────────────────────


class TestBlindConfidenceDistinction:
    """V3.3: privacy cap must distinguish high/medium/low blind confidence."""

    def test_top1_with_high_confidence_gets_top1_cap(self):
        """True source top-1 => PRIVACY_CAP_TOP1_HIGH_MEDIUM regardless of confidence."""
        blind = {
            "actual_source_top_1": ["COMPANY_001"],
            "high_confidence_guesses": ["COMPANY_001"],
        }
        cap, classification = compute_privacy_cap_from_blind_decoy(blind, None, "COMPANY_001")
        assert cap == PRIVACY_CAP_TOP1_HIGH_MEDIUM
        assert classification == "top1_high_medium"

    def test_top3_with_high_confidence_gets_top3_cap(self):
        """True source top-3 with high confidence => PRIVACY_CAP_TOP3_HIGH_MEDIUM."""
        blind = {
            "actual_source_top_3": ["COMPANY_001"],
            "high_confidence_guesses": ["COMPANY_001"],
        }
        cap, classification = compute_privacy_cap_from_blind_decoy(blind, None, "COMPANY_001")
        assert cap == PRIVACY_CAP_TOP3_HIGH_MEDIUM
        assert classification == "top3_high_medium"

    def test_top3_with_medium_confidence_gets_top3_cap(self):
        """True source top-3 with medium confidence => PRIVACY_CAP_TOP3_HIGH_MEDIUM."""
        blind = {
            "actual_source_top_3": ["COMPANY_001"],
            "high_confidence_guesses": [],
            "medium_confidence_with_actual": ["COMPANY_001"],
        }
        cap, classification = compute_privacy_cap_from_blind_decoy(blind, None, "COMPANY_001")
        assert cap == PRIVACY_CAP_TOP3_HIGH_MEDIUM
        assert classification == "top3_high_medium"

    def test_top3_with_low_confidence_only_gets_low_cap(self):
        """True source top-3 with ONLY low confidence => PRIVACY_CAP_TOP3_LOW_ONLY."""
        blind = {
            "actual_source_top_3": ["COMPANY_001"],
            "high_confidence_guesses": [],
            "medium_confidence_with_actual": [],
        }
        cap, classification = compute_privacy_cap_from_blind_decoy(blind, None, "COMPANY_001")
        assert cap == PRIVACY_CAP_TOP3_LOW_ONLY
        assert classification == "top3_low_only"

    def test_high_confidence_wrong_guess_still_caps(self):
        """High confidence even on wrong company => TOP3_HIGH_MEDIUM cap."""
        blind = {
            "high_confidence_guesses": ["COMPANY_001"],
            "actual_source_top_3": [],
        }
        cap, classification = compute_privacy_cap_from_blind_decoy(blind, None, "COMPANY_001")
        assert cap == PRIVACY_CAP_TOP3_HIGH_MEDIUM

    def test_company_not_in_any_list_gets_no_cap(self):
        """Company not in top1/top3/high/medium => no privacy cap."""
        blind = {
            "actual_source_top_1": [],
            "actual_source_top_3": [],
            "high_confidence_guesses": [],
            "medium_confidence_with_actual": [],
        }
        cap, classification = compute_privacy_cap_from_blind_decoy(blind, None, "COMPANY_001")
        assert cap == PRIVACY_CAP_NONE
        assert classification == "pass"

    def test_decoy_direct_leak_overrides_blind(self):
        """Decoy direct leak always takes precedence over blind results."""
        blind = {
            "actual_source_top_3": ["COMPANY_001"],
            "high_confidence_guesses": [],
            "medium_confidence_with_actual": [],
        }
        decoy = {"direct_leak_detected": 1}
        cap, classification = compute_privacy_cap_from_blind_decoy(blind, decoy, "COMPANY_001")
        assert cap == PRIVACY_CAP_DIRECT_LEAK
        assert classification == "direct_leak"

    def test_decoy_top1_high_medium_overrides_blind(self):
        """Decoy true-source top-1 takes precedence over blind."""
        blind = {
            "actual_source_top_3": ["COMPANY_001"],
        }
        decoy = {
            "per_company": {
                "COMPANY_001": {
                    "top_guess_is_actual": True,
                    "model_confidence": "high",
                }
            }
        }
        cap, _ = compute_privacy_cap_from_blind_decoy(blind, decoy, "COMPANY_001")
        assert cap == PRIVACY_CAP_TOP1_HIGH_MEDIUM

    def test_utility_score_respects_privacy_cap(self, tmp_path):
        """score_v3_utility applies the privacy cap to the final score."""
        company_dir = tmp_path / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "financials").mkdir()
        (company_dir / "market").mkdir()
        (company_dir / "sec").mkdir()
        (company_dir / "profile").mkdir()
        (company_dir / "news").mkdir()

        # Set up a basic company that would score high
        (company_dir / "financials" / "transformed_metrics.csv").write_text(
            "2016,Revenue,5.0,income_statement\n"
            "2017,Revenue,5.2,income_statement\n"
            "2018,Revenue,5.4,income_statement\n"
            "2019,Revenue,5.6,income_statement\n"
            "2020,Revenue,5.8,income_statement\n"
            "2021,Revenue,6.0,income_statement\n"
            "2022,Revenue,6.2,income_statement\n"
            "2023,Revenue,6.4,income_statement\n"
            "2024,Revenue,6.6,income_statement\n"
            "2025,Revenue,6.8,income_statement\n"
        )

        # Blind says company is in top-1 => cap should apply
        blind_summary = {"actual_source_top_1": ["COMPANY_001"]}
        result = score_v3_utility(
            "COMPANY_001",
            company_dir,
            blind_summary=blind_summary,
            decoy_summary=None,
        )

        assert result.privacy_cap == PRIVACY_CAP_TOP1_HIGH_MEDIUM
        assert result.final_utility_score <= PRIVACY_CAP_TOP1_HIGH_MEDIUM
        # Verdict should be FAIL because of the cap
        assert result.verdict == UtilityVerdict.FAIL


# ── Bug B: PASS_WITH_WAIVER ─────────────────────────────────────────


class TestVolumeGateWaiver:
    """V3.3: PASS_WITH_WAIVER must be reachable when checks fail on merits."""

    def test_waiver_reaches_pass_with_waiver(self, tmp_path):
        """When waiver is provided and some checks fail on_merits, get PASS_WITH_WAIVER."""
        # Create a minimal bundle with 8 empty company dirs
        bundle = tmp_path / "bundle"
        public_dir = bundle / "public" / "anonymized"
        public_dir.mkdir(parents=True)
        for i in range(1, 9):
            cd = public_dir / f"COMPANY_{i:03d}"
            cd.mkdir()
            (cd / "financials").mkdir()
            # This company has only 5 years (below threshold of 7)
            (cd / "financials" / "transformed_metrics.csv").write_text(
                "2019,Revenue,5.0,income_statement\n"
                "2020,Revenue,5.2,income_statement\n"
                "2021,Revenue,5.4,income_statement\n"
                "2022,Revenue,5.6,income_statement\n"
                "2023,Revenue,5.8,income_statement\n"
            )

        result = evaluate_volume_gate(
            bundle,
            waiver_reason="Source archive for these companies begins in 2019 — 5-year coverage is the maximum available.",
        )

        # Should be PASS_WITH_WAIVER because year_span=5 < 7 on_merits
        assert result.verdict == VOLUME_PASS_WITH_WAIVER
        assert result.passed is True

    def test_no_waiver_with_all_passing_on_merits(self, tmp_path):
        """When all checks pass on their own merits, get PASS (not PASS_WITH_WAIVER)."""
        bundle = tmp_path / "bundle"
        public_dir = bundle / "public" / "anonymized"
        public_dir.mkdir(parents=True)

        # Add coverage files so non-blocking coverage checks pass
        (bundle / "coverage").mkdir(parents=True, exist_ok=True)
        (bundle / "coverage" / "source_coverage_by_company.csv").write_text("company,year_start,year_end\n")

        for i in range(1, 9):
            cd = public_dir / f"COMPANY_{i:03d}"
            cd.mkdir()
            (cd / "financials").mkdir()
            # 10 years — meets threshold
            lines = ["year,metric_name,value,family"]
            for y in range(2016, 2026):
                lines.append(f"{y},Revenue,{5.0 + y*0.1},income_statement")
            (cd / "financials" / "transformed_metrics.csv").write_text("\n".join(lines))

            # Add sec docs (>125 per company to exceed 1000 total entries threshold)
            (cd / "sec").mkdir()
            for j in range(150):
                (cd / "sec" / f"annual_report_business_{2016 + j % 10}.md").write_text(f"## Business section for year {2016 + j % 10}. Placeholder text for volume counting. " * 2)

            # Add market data (>=1000 rows)
            (cd / "market").mkdir()
            price_lines = ["relative_day,price,volume"]
            for k in range(1000):
                price_lines.append(f"DAY_{k:04d},100.0,1")
            (cd / "market" / "price_series.csv").write_text("\n".join(price_lines))

        result = evaluate_volume_gate(bundle)
        # All merit checks should pass, no waiver needed => PASS
        assert result.passed, f"Expected passed=True, got {result.verdict}: {result.warnings}"
        assert result.verdict in {VOLUME_PASS, VOLUME_PASS_WITH_WAIVER}, f"Unexpected verdict: {result.verdict}"

    def test_future_years_fail_even_with_waiver(self, tmp_path):
        """Future years always block — waiver cannot override (V3.3 fix)."""
        bundle = tmp_path / "bundle"
        public_dir = bundle / "public" / "anonymized"
        public_dir.mkdir(parents=True)
        # Need 8 company dirs to pass company_count check
        for i in range(1, 9):
            cd = public_dir / f"COMPANY_{i:03d}"
            cd.mkdir()
            (cd / "financials").mkdir()
            (cd / "financials" / "transformed_metrics.csv").write_text(
                "year,metric_name,value,family\n"
                "2026,Revenue,5.0,income_statement\n"
                "2027,Revenue,5.2,income_statement\n"
            )

        result = evaluate_volume_gate(
            bundle,
            waiver_reason="Historical data extends to 2027.",
        )
        assert result.verdict == VOLUME_FAIL

    def test_on_merits_field_present_and_correct(self, tmp_path):
        """VolumeCheck.on_merits is set — exists on all waiver-relevant checks."""
        bundle = tmp_path / "bundle"
        public_dir = bundle / "public" / "anonymized"
        public_dir.mkdir(parents=True)
        (bundle / "coverage").mkdir()
        (bundle / "coverage" / "source_coverage_by_company.csv").write_text("company,year_start,year_end\n")

        for i in range(1, 9):
            cd = public_dir / f"COMPANY_{i:03d}"
            cd.mkdir()
            (cd / "financials").mkdir()
            (cd / "financials" / "transformed_metrics.csv").write_text(
                "year,metric_name,value,family\n"  # header
                "2016,Revenue,5.0,income_statement\n"
                "2017,Revenue,5.2,income_statement\n"
                "2018,Revenue,5.4,income_statement\n"
                "2019,Revenue,5.6,income_statement\n"
                "2020,Revenue,5.8,income_statement\n"
                "2021,Revenue,6.0,income_statement\n"
                "2022,Revenue,6.2,income_statement\n"
                "2023,Revenue,6.4,income_statement\n"
                "2024,Revenue,6.6,income_statement\n"
                "2025,Revenue,6.8,income_statement\n"
            )

        result = evaluate_volume_gate(bundle, waiver_reason="Source archive limited to 2016-2025 window")

        # Check that on_merits exists on the relevant checks
        found_waiver_checks = 0
        for c in result.checks:
            if c.check_id in {"min_year_span", "min_sec_docs", "min_zip_entries", "min_market_rows"}:
                assert c.on_merits is not None, f"Check {c.check_id} missing on_merits"
                found_waiver_checks += 1
                # year_span = 10, which is >= 7, so on_merits should be True
                if c.check_id == "min_year_span":
                    assert c.on_merits is True, f"year_span on_merits expected True, got {c.on_merits}"
        assert found_waiver_checks == 4, f"Expected 4 waiver checks, found {found_waiver_checks}"


# ── Bug A: Privacy-cap race condition ───────────────────────────────


class TestPrivacyCapRaceCondition:
    """V3.3: utility score must use blind/decoy summaries to apply caps."""

    def test_score_with_blind_summary_applies_cap(self, tmp_path):
        """Passing blind_summary causes privacy cap to be applied in score_v3_utility."""
        company_dir = tmp_path / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "financials").mkdir()
        (company_dir / "market").mkdir()
        (company_dir / "market" / "price_series.csv").write_text(
            "relative_day,price,volume\n" + "\n".join(f"DAY_{i:04d},{100 + i*0.01},1" for i in range(1000))
        )
        (company_dir / "sec").mkdir()
        (company_dir / "sec" / "filing_coverage.md").write_text("Archive-Backed content for teaching use.\n")
        (company_dir / "profile").mkdir()
        (company_dir / "profile" / "archetype_card.json").write_text(
            '{"archetype_key":"global_consumer_staples","broad_sector":"Consumer Staples","description":"A diversified consumer company."}'
        )
        (company_dir / "profile" / "profile.md").write_text("# Profile\n\nThis is a substantive profile for a consumer staples company with multiple product categories and international operations.\n")
        (company_dir / "news").mkdir()
        (company_dir / "news" / "event_timeline.csv").write_text("event,period\nevent1,2020\nevent2,2021\nevent3,2022\nevent4,2023\nevent5,2024\n")
        (company_dir / "news" / "synthetic_news_briefs.md").write_text("# News\n\nDetailed news briefs for classroom analysis. " * 20)

        # 10 years of financials with full statement coverage
        rows = ["year,metric_name,value,family"]
        for y in range(2016, 2026):
            for metric in ["Revenue","GrossProfit","OperatingIncome","NetIncome","TotalAssets","TotalLiabilities","TotalEquity","LongTermDebt","CashAndCashEquivalents"]:
                rows.append(f"{y},{metric},{5.0 + y*0.1},income_statement")
        (company_dir / "financials" / "transformed_metrics.csv").write_text("\n".join(rows))

        # Without summaries — no cap
        result_no_cap = score_v3_utility("COMPANY_001", company_dir, blind_summary=None, decoy_summary=None)
        assert result_no_cap.privacy_cap == PRIVACY_CAP_NONE
        assert result_no_cap.privacy_classification == "pass"

        # With decoy showing direct leak — cap applied (privacy_cap = 0.0)
        decoy = {"direct_leak_detected": 1}
        result_with_cap = score_v3_utility("COMPANY_001", company_dir, blind_summary=None, decoy_summary=decoy)
        assert result_with_cap.privacy_cap == PRIVACY_CAP_DIRECT_LEAK
        assert result_with_cap.final_utility_score == 0.0
        assert result_with_cap.privacy_classification == "direct_leak"

    def test_utility_with_None_summaries_behaves_as_V3_2(self, tmp_path):
        """When summaries are None, score behaves as before (no cap)."""
        company_dir = tmp_path / "COMPANY_001"
        company_dir.mkdir(parents=True)
        (company_dir / "financials").mkdir()
        (company_dir / "market").mkdir()
        (company_dir / "sec").mkdir()
        (company_dir / "profile").mkdir()
        (company_dir / "news").mkdir()

        rows = ["year,metric_name,value,family"]
        for y in range(2016, 2026):
            rows.append(f"{y},Revenue,{5.0 + y*0.1},income_statement")
        (company_dir / "financials" / "transformed_metrics.csv").write_text("\n".join(rows))

        result = score_v3_utility("COMPANY_001", company_dir, blind_summary=None, decoy_summary=None)
        assert result.privacy_cap == PRIVACY_CAP_NONE
        # No privacy cap means final_score == base_score
        assert result.final_utility_score == result.base_utility_score


# ── Public output safety ─────────────────────────────────────────────


class TestPublicOutputSafety:
    """Public utility dicts must NOT leak source names, tickers, or private data."""

    def test_public_dict_no_source_fields(self):
        result = UtilityAuditResult(
            company_id="COMPANY_001",
            base_utility_score=0.85,
            privacy_cap=0.75,
            final_utility_score=0.75,
            verdict="WARN",
            privacy_classification="top3_high_medium",
        )
        public = result.to_public_dict()
        # Must not contain any private field
        for key in ["actual_source_company", "source_ticker", "private_score", "raw_response"]:
            assert key not in public, f"Public dict leaked {key}"
        assert "company_id" in public
        assert "overall_utility_score" in public
