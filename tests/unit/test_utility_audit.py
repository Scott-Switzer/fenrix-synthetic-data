"""Unit tests for V3.2 utility audit module."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fenrix_synthetic.qa.utility_audit import (
    PRIVACY_CAP_DIRECT_LEAK,
    PRIVACY_CAP_NONE,
    PRIVACY_CAP_TOP1_HIGH_MEDIUM,
    PRIVACY_CAP_TOP3_HIGH_MEDIUM,
    PRIVACY_CAP_TOP3_LOW_ONLY,
    UtilityAuditResult,
    UtilityVerdict,
    aggregate_utility_audits,
    compute_privacy_cap_from_blind_decoy,
    score_v3_utility,
)


# ── Privacy cap computation ──────────────────────────────────────────


def test_privacy_cap_direct_leak():
    decoy = {"direct_leak_detected": 1}
    cap, cls = compute_privacy_cap_from_blind_decoy(None, decoy, "COMPANY_001")
    assert cap == PRIVACY_CAP_DIRECT_LEAK
    assert cls == "direct_leak"


def test_privacy_cap_top1_high_medium_decoy():
    decoy = {
        "direct_leak_detected": 0,
        "per_company": {
            "COMPANY_001": {
                "top_guess_is_actual": True,
                "model_confidence": "high",
            }
        },
    }
    cap, cls = compute_privacy_cap_from_blind_decoy(None, decoy, "COMPANY_001")
    assert cap == PRIVACY_CAP_TOP1_HIGH_MEDIUM
    assert cls == "top1_high_medium"


def test_privacy_cap_top1_medium_decoy():
    decoy = {
        "direct_leak_detected": 0,
        "per_company": {
            "COMPANY_002": {
                "top_guess_is_actual": True,
                "model_confidence": "medium",
            }
        },
    }
    cap, cls = compute_privacy_cap_from_blind_decoy(None, decoy, "COMPANY_002")
    assert cap == PRIVACY_CAP_TOP1_HIGH_MEDIUM
    assert cls == "top1_high_medium"


def test_privacy_cap_top3_high_medium_decoy():
    decoy = {
        "direct_leak_detected": 0,
        "per_company": {
            "COMPANY_003": {
                "top_guess_is_actual": False,
                "actual_in_top3": True,
                "model_confidence": "high",
            }
        },
    }
    cap, cls = compute_privacy_cap_from_blind_decoy(None, decoy, "COMPANY_003")
    assert cap == PRIVACY_CAP_TOP3_HIGH_MEDIUM
    assert cls == "top3_high_medium"


def test_privacy_cap_top3_low_only_decoy():
    decoy = {
        "direct_leak_detected": 0,
        "per_company": {
            "COMPANY_004": {
                "top_guess_is_actual": False,
                "actual_in_top3": True,
                "model_confidence": "low",
            }
        },
    }
    cap, cls = compute_privacy_cap_from_blind_decoy(None, decoy, "COMPANY_004")
    assert cap == PRIVACY_CAP_TOP3_LOW_ONLY
    assert cls == "top3_low_only"


def test_privacy_cap_pass():
    decoy = {
        "direct_leak_detected": 0,
        "per_company": {},
    }
    cap, cls = compute_privacy_cap_from_blind_decoy(None, decoy, "COMPANY_005")
    assert cap == PRIVACY_CAP_NONE
    assert cls == "pass"


def test_privacy_cap_from_blind_top1():
    blind = {"actual_source_top_1": ["COMPANY_001"]}
    cap, cls = compute_privacy_cap_from_blind_decoy(blind, None, "COMPANY_001")
    assert cap == PRIVACY_CAP_TOP1_HIGH_MEDIUM
    assert "top1" in cls


def test_privacy_cap_from_blind_top3():
    # V3.3: blind top-3 with high confidence => 0.75 cap
    blind = {
        "actual_source_top_3": ["COMPANY_002"],
        "high_confidence_guesses": ["COMPANY_002"],
    }
    cap, cls = compute_privacy_cap_from_blind_decoy(blind, None, "COMPANY_002")
    assert cap == PRIVACY_CAP_TOP3_HIGH_MEDIUM
    assert "top3" in cls


def test_privacy_cap_from_blind_high_conf():
    blind = {"high_confidence_guesses": ["COMPANY_003"]}
    cap, cls = compute_privacy_cap_from_blind_decoy(blind, None, "COMPANY_003")
    assert cap == PRIVACY_CAP_TOP3_HIGH_MEDIUM


# ── Utility scoring ──────────────────────────────────────────────────


def _build_minimal_company_dir(tmpdir: Path, company_id: str) -> Path:
    """Build a minimal public company directory for utility scoring."""
    cd = tmpdir / company_id
    cd.mkdir(parents=True)

    # Profile
    (cd / "profile").mkdir(parents=True, exist_ok=True)
    (cd / "profile" / "archetype_card.json").write_text(json.dumps({
        "archetype_key": "diversified_beverage_snack",
        "archetype_label": "Diversified Beverage and Snack",
        "broad_sector": "Consumer Staples",
        "description": "A diversified consumer goods company with broad distribution.",
        "peer_range": "5+ peers",
    }))

    (cd / "profile" / "profile.md").write_text(
        "# Company Profile\n\nA consumer goods company with diverse product lines. "
        "The business model is consistent with industry peers."
    )

    # Financials
    (cd / "financials").mkdir(parents=True, exist_ok=True)
    csv_lines = ["year,metric_name,transformed_value,family"]
    for y in range(2016, 2026):
        for metric in ["Revenue", "NetIncome", "TotalAssets"]:
            csv_lines.append(f"{y},{metric},{5.5 + (y - 2016) * 0.13},income_statement")
    (cd / "financials" / "transformed_metrics.csv").write_text("\n".join(csv_lines))

    (cd / "financials" / "statement_summary.csv").write_text(
        "statement,line_item,latest_value,trend\n"
        "INCOME_STATEMENT,Revenue,6.8,increasing\n"
        "BALANCE_SHEET,TotalAssets,28.0,stable\n"
        "CASH_FLOW,OperatingCashFlow,2.1,increasing\n"
    )

    (cd / "financials" / "ratio_summary.csv").write_text(
        "ratio_name,ratio_value\n"
        "net_margin,0.15\n"
        "current_ratio,1.8\n"
        "debt_to_equity,0.9\n"
        "asset_turnover,0.8\n"
        "free_cash_flow_yield,0.04\n"
    )

    (cd / "financials" / "summary.md").write_text(
        "# Financial Summary\nCoverage: 2016-2025 (10 fiscal years)."
    )

    (cd / "financials" / "reconciliation_summary.md").write_text(
        "# Reconciliation\nCoverage: 10 fiscal years (2016-2025)"
    )

    # Market
    (cd / "market").mkdir(parents=True, exist_ok=True)
    price_lines = ["relative_day,price,volume_indicator"]
    for i in range(1200):
        price_lines.append(f"DAY_{i:04d},{100 + i * 0.01},3")
    (cd / "market" / "price_series.csv").write_text("\n".join(price_lines))

    (cd / "market" / "return_summary.md").write_text("# Return Summary\nObservations: 1200")
    ev_lines = ["event_class,event_period,relative_return_window,return_pct"]
    for j in range(6):
        ev_lines.append(f"event_{j},Year -2 Q1,[-10,+10] days,{-5 + j * 2.0}")
    (cd / "market" / "event_window_returns.csv").write_text("\n".join(ev_lines))

    # SEC
    (cd / "sec").mkdir(parents=True, exist_ok=True)
    (cd / "sec" / "filing_coverage.md").write_text(
        "# Filing Coverage\nHonestly-labeled fallback stub content."
    )
    (cd / "sec" / "annual_report_business.md").write_text(
        "## Business\nArchive-backed content for business section."
    )

    # News
    (cd / "news").mkdir(parents=True, exist_ok=True)
    (cd / "news" / "synthetic_news_briefs.md").write_text(
        "# Synthetic News Briefs\n\n## Event 1\nSynthetic reconstruction for classroom use.\n\n"
        "Detailed description of a synthetic event with market context.\n\n"
        "## Event 2\nAnother synthetic event for analysis.\n"
    )
    news_csv = ["brief_id,company_id,event_class,relative_period,market_relevance"]
    for j in range(6):
        news_csv.append(f"news_{j},{company_id},demand_shift,Year -1 Q1,Class-level implication")
    (cd / "news" / "event_timeline.csv").write_text("\n".join(news_csv))

    return cd


def test_utility_score_privacy_pass():
    with TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        cd = _build_minimal_company_dir(tmpdir, "COMPANY_001")
        result = score_v3_utility("COMPANY_001", cd)
        assert isinstance(result, UtilityAuditResult)
        assert result.base_utility_score > 0.5
        assert result.privacy_cap == PRIVACY_CAP_NONE
        assert result.final_utility_score == result.base_utility_score
        assert result.verdict in {UtilityVerdict.PASS, UtilityVerdict.WARN}
        assert len(result.components) == 8
        assert len(result.signals_preserved) > 0


def test_utility_score_direct_leak_caps_to_zero():
    with TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        cd = _build_minimal_company_dir(tmpdir, "COMPANY_002")
        decoy = {
            "direct_leak_detected": 1,
            "per_company": {"COMPANY_002": {"top_guess_is_actual": False}},
        }
        result = score_v3_utility("COMPANY_002", cd, decoy_summary=decoy)
        assert result.privacy_cap == PRIVACY_CAP_DIRECT_LEAK
        assert result.final_utility_score == 0.0
        assert result.verdict == UtilityVerdict.FAIL


def test_utility_score_top1_capped_at_060():
    with TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        cd = _build_minimal_company_dir(tmpdir, "COMPANY_003")
        decoy = {
            "direct_leak_detected": 0,
            "per_company": {
                "COMPANY_003": {
                    "top_guess_is_actual": True,
                    "model_confidence": "high",
                }
            },
        }
        result = score_v3_utility("COMPANY_003", cd, decoy_summary=decoy)
        assert result.privacy_cap == PRIVACY_CAP_TOP1_HIGH_MEDIUM
        assert result.final_utility_score <= 0.60


def test_utility_score_top3_capped_at_075():
    with TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        cd = _build_minimal_company_dir(tmpdir, "COMPANY_004")
        # V3.3: must add confidence info for high/medium cap
        blind = {
            "actual_source_top_3": ["COMPANY_004"],
            "high_confidence_guesses": ["COMPANY_004"],
        }
        result = score_v3_utility("COMPANY_004", cd, blind_summary=blind)
        assert result.privacy_cap == PRIVACY_CAP_TOP3_HIGH_MEDIUM
        assert result.final_utility_score <= 0.75


def test_utility_score_top3_low_capped_at_085():
    with TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        cd = _build_minimal_company_dir(tmpdir, "COMPANY_005")
        decoy = {
            "direct_leak_detected": 0,
            "per_company": {
                "COMPANY_005": {
                    "top_guess_is_actual": False,
                    "actual_in_top3": True,
                    "model_confidence": "low",
                }
            },
        }
        result = score_v3_utility("COMPANY_005", cd, decoy_summary=decoy)
        assert result.privacy_cap == PRIVACY_CAP_TOP3_LOW_ONLY
        assert result.final_utility_score <= 0.85


def test_utility_cannot_pass_if_privacy_fails():
    """Hard rule: if privacy fails, utility cannot be PASS."""
    with TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        cd = _build_minimal_company_dir(tmpdir, "COMPANY_006")
        # Force a very high base score with a complete directory
        # Even if base is high, top-1 privacy hit caps final at 0.60
        decoy = {
            "direct_leak_detected": 0,
            "per_company": {
                "COMPANY_006": {
                    "top_guess_is_actual": True,
                    "model_confidence": "medium",
                }
            },
        }
        result = score_v3_utility("COMPANY_006", cd, decoy_summary=decoy)
        assert result.privacy_cap == PRIVACY_CAP_TOP1_HIGH_MEDIUM
        # Even if base score is high, capped score <= 0.60 → can't be PASS (needs >=0.80)
        assert result.verdict != UtilityVerdict.PASS


# ── Aggregate utility audits ──────────────────────────────────────────


def _mk_result(cid: str, score: float, verdict: str) -> UtilityAuditResult:
    return UtilityAuditResult(
        company_id=cid,
        base_utility_score=score,
        privacy_cap=1.0,
        final_utility_score=score,
        verdict=verdict,
    )


def test_aggregate_utility_all_pass():
    results = [_mk_result("COMPANY_001", 0.85, "PASS") for _ in range(8)]
    summary = aggregate_utility_audits(results)
    assert summary["average_utility_score"] == 0.85
    assert summary["utility_gate"] == "pass"
    assert summary["verdict_pass_count"] == 8


def test_aggregate_utility_fails_below_065():
    results = [_mk_result("COMPANY_001", 0.60, "FAIL") for _ in range(8)]
    summary = aggregate_utility_audits(results)
    assert summary["utility_gate"] == "fail"


def test_aggregate_utility_warns_when_privacy_gate_fails():
    results = [_mk_result("COMPANY_001", 0.85, "PASS") for _ in range(8)]
    blind = {"privacy_gate": "fail"}
    summary = aggregate_utility_audits(results, blind_summary=blind)
    # Privacy gate failed, so utility gate must be "warn" at best
    assert summary["utility_gate"] == "warn"


def test_aggregate_utility_warns_when_decoy_gate_fails():
    results = [_mk_result("COMPANY_001", 0.85, "PASS") for _ in range(8)]
    decoy = {"decoy_gate": "fail"}
    summary = aggregate_utility_audits(results, decoy_summary=decoy)
    assert summary["utility_gate"] == "warn"


# ── Public output safety ─────────────────────────────────────────────


def test_public_dict_no_private_data():
    result = UtilityAuditResult(
        company_id="COMPANY_001",
        base_utility_score=0.85,
        privacy_cap=0.60,
        final_utility_score=0.60,
        verdict=UtilityVerdict.WARN,
        privacy_classification="top1_high_medium",
    )
    pub = result.to_public_dict()
    assert "company_id" in pub
    assert "overall_utility_score" in pub
    assert "verdict" in pub
    # Should NOT leak actual source names
    assert "actual_source" not in pub
    assert "private" not in json.dumps(pub).lower()
