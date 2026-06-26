"""V3.2 Utility Audit — privacy-capped, multi-component scoring.

Replaces the V3.0 thesis-overlap utility scoring with a multi-dimensional
audit that measures educational usefulness after anonymization.

The score is computed from these components:
- historical_coverage: enough years/documents to study trends
- statement_completeness: revenue, margins, assets, liabilities, cash flow, leverage
- ratio_usefulness: profitability, liquidity, leverage, growth, valuation proxies
- narrative_usefulness: business/risk/MD&A sections are coherent and non-identical
- event_usefulness: events explain market/financial movement
- cross_company_comparison: students can compare sectors
- privacy_penalty: LLM re-identification lowers utility

Adversarial privacy caps (applied AFTER computing base score):
- direct leak detected              => utility max 0.00
- true source top-1, high/medium    => utility max 0.60
- true source top-3, high/medium    => utility max 0.75
- true source top-3, low only       => utility max 0.85
- privacy pass                      => no adversarial cap

Verdict thresholds:
- PASS: >= 0.80 AND privacy gates pass
- WARN: 0.65-0.80 or minor coverage fallback
- FAIL: < 0.65 or privacy gate fails

Hard rule: If blind or decoy privacy gate fails, utility cannot be PASS.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Verdict constants ─────────────────────────────────────────────────


class UtilityVerdict:
    """V3.2 utility verdict constants."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


# ── Privacy cap constants ─────────────────────────────────────────────


#: Maps privacy failure classification to max allowed utility score.
PRIVACY_CAP_DIRECT_LEAK = 0.00
PRIVACY_CAP_TOP1_HIGH_MEDIUM = 0.60
PRIVACY_CAP_TOP3_HIGH_MEDIUM = 0.75
PRIVACY_CAP_TOP3_LOW_ONLY = 0.85
PRIVACY_CAP_NONE = 1.00  # no adversarial cap


@dataclass
class UtilityAuditComponent:
    """A single utility audit component score."""

    name: str
    score: float  # 0.0–1.0
    weight: float  # component weight in overall score
    detail: str = ""


@dataclass
class UtilityAuditResult:
    """Complete V3.2 utility audit result."""

    company_id: str
    base_utility_score: float  # before privacy cap
    privacy_cap: float  # max allowed given privacy status
    final_utility_score: float  # min(base, cap)
    verdict: str  # PASS, WARN, FAIL
    components: list[UtilityAuditComponent] = field(default_factory=list)
    privacy_classification: str = "unknown"  # pass, top1, top3, direct_leak
    signals_preserved: list[str] = field(default_factory=list)
    signals_lost: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "base_utility_score": round(self.base_utility_score, 4),
            "privacy_cap": round(self.privacy_cap, 4),
            "overall_utility_score": round(self.final_utility_score, 4),
            "verdict": self.verdict,
            "components": [
                {"name": c.name, "score": round(c.score, 4), "weight": c.weight, "detail": c.detail}
                for c in self.components
            ],
            "signals_preserved": self.signals_preserved,
            "signals_lost": self.signals_lost,
            "privacy_classification": self.privacy_classification,
            "warnings": self.warnings,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Redacted public summary — safe for ZIP."""
        return {
            "company_id": self.company_id,
            "overall_utility_score": round(self.final_utility_score, 4),
            "verdict": self.verdict,
            "signals_preserved": self.signals_preserved,
            "signals_lost": self.signals_lost,
            "base_utility_score": round(self.base_utility_score, 4),
            "privacy_cap_applied": round(self.privacy_cap, 4) < 1.0,
            "privacy_classification": self.privacy_classification,
        }


def compute_privacy_cap_from_blind_decoy(
    blind_summary: dict[str, Any] | None,
    decoy_summary: dict[str, Any] | None,
    company_id: str,
) -> tuple[float, str]:
    """Compute the privacy cap for a company based on blind + decoy results.

    Args:
        blind_summary: Aggregated blind-guess summary from qa/.
        decoy_summary: Aggregated decoy-aware summary from qa/.
        company_id: The anonymized company ID.

    Returns:
        Tuple of (privacy_cap, classification_string).
    """
    # Check decoy direct leaks first (most severe)
    if decoy_summary:
        direct_leaks = decoy_summary.get("direct_leak_detected", 0)
        if direct_leaks > 0:
            return (PRIVACY_CAP_DIRECT_LEAK, "direct_leak")

    # Check per-company decoy results
    decoy_per_company: dict[str, Any] = {}
    if decoy_summary:
        decoy_per_company = decoy_summary.get("per_company", {})

    decoy_company = decoy_per_company.get(company_id, {})
    if decoy_company:
        if decoy_company.get("top_guess_is_actual") and decoy_company.get("model_confidence") in {"medium", "high"}:
            return (PRIVACY_CAP_TOP1_HIGH_MEDIUM, "top1_high_medium")
        if decoy_company.get("actual_in_top3") and decoy_company.get("model_confidence") in {"medium", "high"}:
            return (PRIVACY_CAP_TOP3_HIGH_MEDIUM, "top3_high_medium")
        if decoy_company.get("actual_in_top3") and decoy_company.get("model_confidence") == "low":
            return (PRIVACY_CAP_TOP3_LOW_ONLY, "top3_low_only")

    # Check blind results
    if blind_summary:
        # Check if company is in actual_source_top_1
        top1_list: list[str] = blind_summary.get("actual_source_top_1", [])
        if company_id in top1_list:
            # Determine confidence from per-company blind guess file
            return (PRIVACY_CAP_TOP1_HIGH_MEDIUM, "top1_high_medium")

        top3_list: list[str] = blind_summary.get("actual_source_top_3", [])
        if company_id in top3_list:
            return (PRIVACY_CAP_TOP3_HIGH_MEDIUM, "top3_high_medium")

        high_conf_list: list[str] = blind_summary.get("high_confidence_guesses", [])
        if company_id in high_conf_list:
            return (PRIVACY_CAP_TOP3_HIGH_MEDIUM, "top3_high_medium")

    # No adversarial hits
    return (PRIVACY_CAP_NONE, "pass")


def score_v3_utility(
    company_id: str,
    public_company_dir: Path,
    *,
    blind_summary: dict[str, Any] | None = None,
    decoy_summary: dict[str, Any] | None = None,
) -> UtilityAuditResult:
    """Score V3.2 utility for a single anonymized company.

    Measures educational usefulness across multiple components,
    then applies the adversarial privacy cap.

    Args:
        company_id: Anonymized company ID.
        public_company_dir: Path to public/anonymized/<COMPANY_NNN>/.
        blind_summary: Aggregated blind-guess summary.
        decoy_summary: Aggregated decoy-aware summary.

    Returns:
        UtilityAuditResult with privacy-capped final score.
    """
    components: list[UtilityAuditComponent] = []
    signals_preserved: list[str] = []
    signals_lost: list[str] = []
    warnings: list[str] = []

    # ── Component 1: Historical coverage (weight 0.15) ───────────────
    hist_score, hist_detail = _score_historical_coverage(company_id, public_company_dir)
    components.append(UtilityAuditComponent(
        name="historical_coverage", score=hist_score, weight=0.15, detail=hist_detail,
    ))
    if hist_score >= 0.7:
        signals_preserved.append("historical_coverage")
    else:
        signals_lost.append("historical_coverage")

    # ── Component 2: Statement completeness (weight 0.15) ────────────
    stmt_score, stmt_detail = _score_statement_completeness(public_company_dir)
    components.append(UtilityAuditComponent(
        name="statement_completeness", score=stmt_score, weight=0.15, detail=stmt_detail,
    ))
    if stmt_score >= 0.7:
        signals_preserved.append("statement_completeness")
    else:
        signals_lost.append("statement_completeness")

    # ── Component 3: Ratio usefulness (weight 0.10) ─────────────────
    ratio_score, ratio_detail = _score_ratio_usefulness(public_company_dir)
    components.append(UtilityAuditComponent(
        name="ratio_usefulness", score=ratio_score, weight=0.10, detail=ratio_detail,
    ))
    if ratio_score >= 0.7:
        signals_preserved.append("ratio_usefulness")
    else:
        signals_lost.append("ratio_usefulness")

    # ── Component 4: Narrative/SEC usefulness (weight 0.20) ─────────
    narrative_score, narrative_detail = _score_narrative_usefulness(company_id, public_company_dir)
    components.append(UtilityAuditComponent(
        name="narrative_usefulness", score=narrative_score, weight=0.20, detail=narrative_detail,
    ))
    if narrative_score >= 0.7:
        signals_preserved.append("narrative_usefulness")
    else:
        signals_lost.append("narrative_usefulness")

    # ── Component 5: Event usefulness (weight 0.10) ─────────────────
    event_score, event_detail = _score_event_usefulness(public_company_dir)
    components.append(UtilityAuditComponent(
        name="event_usefulness", score=event_score, weight=0.10, detail=event_detail,
    ))
    if event_score >= 0.7:
        signals_preserved.append("event_usefulness")
    else:
        signals_lost.append("event_usefulness")

    # ── Component 6: Cross-company comparison (weight 0.10) ─────────
    cross_score, cross_detail = _score_cross_company_comparison(public_company_dir)
    components.append(UtilityAuditComponent(
        name="cross_company_comparison", score=cross_score, weight=0.10, detail=cross_detail,
    ))
    if cross_score >= 0.7:
        signals_preserved.append("cross_company_comparison")
    else:
        signals_lost.append("cross_company_comparison")

    # ── Component 7: Readability (weight 0.10) ──────────────────────
    readability_score, readability_detail = _score_readability(public_company_dir)
    components.append(UtilityAuditComponent(
        name="readability", score=readability_score, weight=0.10, detail=readability_detail,
    ))
    if readability_score >= 0.7:
        signals_preserved.append("readability")
    else:
        signals_lost.append("readability")

    # ── Component 8: Market data usefulness (weight 0.10) ───────────
    market_score, market_detail = _score_market_usefulness(public_company_dir)
    components.append(UtilityAuditComponent(
        name="market_usefulness", score=market_score, weight=0.10, detail=market_detail,
    ))
    if market_score >= 0.7:
        signals_preserved.append("market_usefulness")
    else:
        signals_lost.append("market_usefulness")

    # ── Compute base utility score ──────────────────────────────────
    base_score = sum(c.score * c.weight for c in components)
    base_score = round(base_score, 4)

    # ── Apply adversarial privacy cap ───────────────────────────────
    privacy_cap, privacy_class = compute_privacy_cap_from_blind_decoy(
        blind_summary, decoy_summary, company_id
    )
    final_score = min(base_score, privacy_cap)
    final_score = round(final_score, 4)

    # ── Determine verdict ───────────────────────────────────────────
    privacy_passed = privacy_class == "pass"
    if privacy_cap <= 0.0:
        verdict = UtilityVerdict.FAIL
        warnings.append("Direct leak detected — utility score capped at 0.00")
    elif not privacy_passed and final_score >= 0.80:
        # Hard rule: if privacy fails, cannot be PASS
        verdict = UtilityVerdict.WARN
        warnings.append(f"Utility score exceeds 0.80 but privacy gate failed ({privacy_class}) — capped to WARN")
    elif final_score >= 0.80:
        verdict = UtilityVerdict.PASS
    elif final_score >= 0.65:
        verdict = UtilityVerdict.WARN
    else:
        verdict = UtilityVerdict.FAIL

    return UtilityAuditResult(
        company_id=company_id,
        base_utility_score=base_score,
        privacy_cap=privacy_cap,
        final_utility_score=final_score,
        verdict=verdict,
        components=components,
        privacy_classification=privacy_class,
        signals_preserved=signals_preserved,
        signals_lost=signals_lost,
        warnings=warnings,
    )


# ── Component scorers ──────────────────────────────────────────────────


def _score_historical_coverage(company_id: str, public_dir: Path) -> tuple[float, str]:
    """Score historical coverage completeness.

    Checks:
    - financials/transformed_metrics.csv has rows
    - financials/reconciliation_summary.md has coverage info
    - market/price_series.csv has rows
    - sec/filing_coverage.md exists
    """
    score = 0.0
    details: list[str] = []

    # Financial metrics years
    fin_path = public_dir / "financials" / "transformed_metrics.csv"
    if fin_path.exists():
        try:
            years: set[str] = set()
            with open(fin_path) as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if row and len(row) >= 1:
                        years.add(row[0])
            year_count = len(years)
            if year_count >= 10:
                score += 0.40
                details.append(f"financial_years={year_count} (>=10)")
            elif year_count >= 7:
                score += 0.30
                details.append(f"financial_years={year_count} (7-9)")
            elif year_count >= 5:
                score += 0.15
                details.append(f"financial_years={year_count} (5-6)")
            else:
                details.append(f"financial_years={year_count} (<5, insufficient)")
        except (OSError, csv.Error):
            details.append("financial_metrics: unreadable")
    else:
        details.append("financial_metrics: missing")

    # Market data rows
    market_path = public_dir / "market" / "price_series.csv"
    if market_path.exists():
        try:
            row_count = max(0, len(market_path.read_text().splitlines()) - 1)
            if row_count >= 1000:
                score += 0.30
                details.append(f"market_rows={row_count} (>=1000)")
            elif row_count >= 500:
                score += 0.15
                details.append(f"market_rows={row_count} (500-999)")
            else:
                details.append(f"market_rows={row_count} (<500, insufficient)")
        except OSError:
            details.append("market_data: unreadable")
    else:
        details.append("market_data: missing")

    # SEC filing coverage
    coverage_path = public_dir / "sec" / "filing_coverage.md"
    if coverage_path.exists():
        try:
            content = coverage_path.read_text(encoding="utf-8")
            if "Archive-Backed" in content:
                score += 0.20
                details.append("sec_coverage: archive-backed")
            elif "fallback" in content.lower() or "honest" in content.lower():
                score += 0.10
                details.append("sec_coverage: honestly-labeled fallback")
            else:
                score += 0.05
                details.append("sec_coverage: exists")
        except OSError:
            details.append("sec_coverage: unreadable")
    else:
        details.append("sec_coverage: missing")

    # News/event coverage
    news_path = public_dir / "news" / "event_timeline.csv"
    if news_path.exists():
        try:
            event_count = max(0, len(news_path.read_text().splitlines()) - 1)
            if event_count >= 5:
                score += 0.10
                details.append(f"events={event_count} (>=5)")
            else:
                score += 0.05
                details.append(f"events={event_count} (<5)")
        except OSError:
            pass

    return (min(score, 1.0), "; ".join(details) if details else "no data found")


def _score_statement_completeness(public_dir: Path) -> tuple[float, str]:
    """Score financial statement completeness.

    Checks for income statement, balance sheet, and cash flow coverage.
    """
    score = 0.0
    details: list[str] = []

    fin_dir = public_dir / "financials"
    if not fin_dir.exists():
        return (0.0, "financials directory missing")

    # Check statement_summary.csv for IS/BS/CF coverage
    stmt_path = fin_dir / "statement_summary.csv"
    if stmt_path.exists():
        try:
            stmt_types: set[str] = set()
            with open(stmt_path) as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if row and len(row) >= 1:
                        stmt_types.add(row[0])
            if "INCOME_STATEMENT" in stmt_types:
                score += 0.35
                details.append("income_statement: present")
            else:
                details.append("income_statement: missing")
            if "BALANCE_SHEET" in stmt_types:
                score += 0.35
                details.append("balance_sheet: present")
            else:
                details.append("balance_sheet: missing")
            if "CASH_FLOW" in stmt_types:
                score += 0.30
                details.append("cash_flow: present")
            else:
                details.append("cash_flow: missing")
        except (OSError, csv.Error):
            details.append("statement_summary: unreadable")
    else:
        details.append("statement_summary.csv missing")

    return (score, "; ".join(details) if details else "no statement data")


def _score_ratio_usefulness(public_dir: Path) -> tuple[float, str]:
    """Score ratio usefulness — variety of financial ratios."""
    ratio_path = public_dir / "financials" / "ratio_summary.csv"
    if not ratio_path.exists():
        return (0.0, "ratio_summary.csv missing")

    try:
        ratio_names: list[str] = []
        with open(ratio_path) as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if row and len(row) >= 1:
                    ratio_names.append(row[0])

        # Categories of ratios
        profitability = any(r in ratio_names for r in ["net_margin", "gross_margin", "operating_margin", "return_on_assets", "return_on_equity"])
        liquidity = any(r in ratio_names for r in ["current_ratio"])
        leverage = any(r in ratio_names for r in ["debt_to_equity"])
        efficiency = any(r in ratio_names for r in ["asset_turnover"])
        valuation = any(r in ratio_names for r in ["free_cash_flow_yield"])

        categories_present = sum([profitability, liquidity, leverage, efficiency, valuation])
        score = categories_present / 5.0

        detail_parts = []
        if profitability:
            detail_parts.append("profitability")
        if liquidity:
            detail_parts.append("liquidity")
        if leverage:
            detail_parts.append("leverage")
        if efficiency:
            detail_parts.append("efficiency")
        if valuation:
            detail_parts.append("valuation")

        return (score, f"ratio_categories={categories_present}/5: {', '.join(detail_parts)}" if detail_parts else "no ratio categories")
    except (OSError, csv.Error):
        return (0.0, "ratio_summary.csv unreadable")


def _score_narrative_usefulness(company_id: str, public_dir: Path) -> tuple[float, str]:
    """Score narrative/SEC usefulness — coherent, non-identical sections."""
    score = 0.0
    details: list[str] = []

    sec_dir = public_dir / "sec"
    if not sec_dir.exists():
        return (0.0, "sec directory missing")

    # Count distinct SEC files
    sec_files = list(sec_dir.glob("*.md"))
    if len(sec_files) >= 5:
        score += 0.30
        details.append(f"sec_files={len(sec_files)} (>=5)")
    elif len(sec_files) >= 3:
        score += 0.20
        details.append(f"sec_files={len(sec_files)} (3-4)")
    elif len(sec_files) >= 1:
        score += 0.10
        details.append(f"sec_files={len(sec_files)} (1-2)")
    else:
        details.append("sec_files=0")

    # Check for honest labeling vs stubs
    has_stub_label = False
    sec_text_total = 0
    for sf in sec_files:
        try:
            text = sf.read_text(encoding="utf-8", errors="replace")
            sec_text_total += len(text)
            if "honestly-labeled fallback stub" in text.lower():
                has_stub_label = True
            elif "archive-backed" in text.lower():
                score += 0.20
                details.append("archive_backed_content: found")
                break
        except OSError:
            pass

    if has_stub_label and not any("archive_backed_content" in d for d in details):
        score += 0.05
        details.append("content: honestly-labeled stubs")

    # Profile completeness
    profile_path = public_dir / "profile" / "profile.md"
    if profile_path.exists():
        try:
            profile_text = profile_path.read_text(encoding="utf-8", errors="replace")
            if len(profile_text) > 200:
                score += 0.15
                details.append("profile: substantive")
            else:
                score += 0.05
                details.append("profile: minimal")
        except OSError:
            pass
    else:
        details.append("profile: missing")

    # News narrative
    news_path = public_dir / "news" / "synthetic_news_briefs.md"
    if news_path.exists():
        try:
            news_text = news_path.read_text(encoding="utf-8", errors="replace")
            if len(news_text) > 500:
                score += 0.10
                details.append("news_briefs: substantive")
            else:
                score += 0.05
                details.append("news_briefs: minimal")
        except OSError:
            pass

    # Archetype card
    archetype_path = public_dir / "profile" / "archetype_card.json"
    if archetype_path.exists():
        try:
            card = json.loads(archetype_path.read_text(encoding="utf-8"))
            if card.get("archetype_key") and card.get("description"):
                score += 0.10
                details.append("archetype_card: complete")
        except (json.JSONDecodeError, OSError):
            pass

    return (min(score, 1.0), "; ".join(details) if details else "no narrative data")


def _score_event_usefulness(public_dir: Path) -> tuple[float, str]:
    """Score event usefulness — synthetic events with market context."""
    timeline_path = public_dir / "news" / "event_timeline.csv"
    events_path = public_dir / "news" / "synthetic_news_briefs.md"
    market_events = public_dir / "market" / "event_window_returns.csv"

    score = 0.0
    details: list[str] = []

    if timeline_path.exists():
        try:
            event_count = max(0, len(timeline_path.read_text().splitlines()) - 1)
            if event_count >= 5:
                score += 0.40
                details.append(f"timeline_events={event_count}")
            elif event_count >= 3:
                score += 0.25
                details.append(f"timeline_events={event_count}")
            else:
                score += 0.10
                details.append(f"timeline_events={event_count}")
        except OSError:
            pass

    if events_path.exists():
        try:
            event_text = events_path.read_text(encoding="utf-8", errors="replace")
            if len(event_text) > 300:
                score += 0.30
                details.append("news_briefs: detailed")
            else:
                score += 0.10
                details.append("news_briefs: minimal")
        except OSError:
            pass

    if market_events.exists():
        try:
            mkt_count = max(0, len(market_events.read_text().splitlines()) - 1)
            if mkt_count >= 5:
                score += 0.30
                details.append(f"event_window_returns={mkt_count}")
            elif mkt_count >= 1:
                score += 0.15
                details.append(f"event_window_returns={mkt_count}")
        except OSError:
            pass

    return (min(score, 1.0), "; ".join(details) if details else "no event data")


def _score_cross_company_comparison(public_dir: Path) -> tuple[float, str]:
    """Score cross-company comparison usefulness.

    Checks if archetype card has sector/sufficient-info for comparison.
    """
    archetype_path = public_dir / "profile" / "archetype_card.json"
    if not archetype_path.exists():
        return (0.3, "archetype_card missing — limited comparison value")

    try:
        card = json.loads(archetype_path.read_text(encoding="utf-8"))
        score = 0.0
        detail_parts = []

        if card.get("broad_sector"):
            score += 0.30
            detail_parts.append("sector_identified")
        if card.get("archetype_key"):
            score += 0.20
            detail_parts.append("archetype_key_present")
        if card.get("description") and len(card.get("description", "")) > 50:
            score += 0.30
            detail_parts.append("descriptive_profile")
        if card.get("peer_range"):
            score += 0.20
            detail_parts.append("peer_group_mapped")

        return (score, "; ".join(detail_parts) if detail_parts else "minimal archetype")
    except (json.JSONDecodeError, OSError):
        return (0.2, "archetype_card unreadable")


def _score_readability(public_dir: Path) -> tuple[float, str]:
    """Score readability — clean markdown, no excessive redaction."""
    score = 0.0
    details: list[str] = []
    file_count = 0

    for md_file in sorted(public_dir.rglob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
            file_count += 1

            # Penalize excessive [REDACTED] placeholders
            redacted_count = text.count("[REDACTED]")
            if redacted_count > 10:
                details.append(f"{md_file.name}: excessive REDACTED ({redacted_count})")
            elif redacted_count > 0:
                details.append(f"{md_file.name}: some REDACTED ({redacted_count})")

            # Check for raw HTML/XML in markdown
            if "<html" in text.lower() or "<div" in text.lower():
                details.append(f"{md_file.name}: contains HTML")

        except OSError:
            continue

    if file_count >= 10:
        score = 0.80
    elif file_count >= 5:
        score = 0.60
    elif file_count >= 1:
        score = 0.30

    # Reduce score if REDACTED is overused
    for d in details:
        if "excessive REDACTED" in d:
            score = max(0.0, score - 0.30)
        elif "some REDACTED" in d:
            score = max(0.0, score - 0.10)

    if "contains HTML" in " ".join(details):
        score = max(0.0, score - 0.20)

    return (min(score, 1.0), f"files={file_count}; " + ("clean" if not details else "; ".join(details[:3])))


def _score_market_usefulness(public_dir: Path) -> tuple[float, str]:
    """Score market data usefulness — price series, event returns."""
    score = 0.0
    details: list[str] = []

    price_path = public_dir / "market" / "price_series.csv"
    if price_path.exists():
        try:
            lines = price_path.read_text().splitlines()
            row_count = max(0, len(lines) - 1)
            if row_count >= 1000:
                score += 0.50
                details.append(f"price_rows={row_count} (>=1000)")
            elif row_count >= 500:
                score += 0.35
                details.append(f"price_rows={row_count} (500-999)")
            elif row_count >= 100:
                score += 0.20
                details.append(f"price_rows={row_count} (100-499)")
            else:
                score += 0.10
                details.append(f"price_rows={row_count} (<100)")
        except OSError:
            details.append("price_series: unreadable")
    else:
        details.append("price_series: missing")

    event_path = public_dir / "market" / "event_window_returns.csv"
    if event_path.exists():
        try:
            event_count = max(0, len(event_path.read_text().splitlines()) - 1)
            if event_count >= 5:
                score += 0.30
                details.append(f"event_windows={event_count}")
            elif event_count >= 1:
                score += 0.15
                details.append(f"event_windows={event_count}")
        except OSError:
            pass

    return_path = public_dir / "market" / "return_summary.md"
    if return_path.exists():
        score += 0.20
        details.append("return_summary: present")

    return (min(score, 1.0), "; ".join(details) if details else "no market data")


# ── Aggregate utility across companies ─────────────────────────────────


def aggregate_utility_audits(
    results: list[UtilityAuditResult],
    *,
    blind_summary: dict[str, Any] | None = None,
    decoy_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate per-company utility audits into bundle-level summary.

    Args:
        results: Per-company UtilityAuditResult list.
        blind_summary: Bundle-level blind guess summary.
        decoy_summary: Bundle-level decoy aware summary.

    Returns:
        Aggregated utility summary dict.
    """
    n = len(results)
    if n == 0:
        return {
            "schema_version": "1.0",
            "aggregate_kind": "multi_company_utility_v3_2",
            "companies_reviewed": 0,
            "average_utility_score": 0.0,
            "utility_gate": "fail",
        }

    scores = [r.final_utility_score for r in results]
    avg_score = round(sum(scores) / n, 4)
    min_score = round(min(scores), 4)
    max_score = round(max(scores), 4)

    passes = sum(1 for r in results if r.verdict == UtilityVerdict.PASS)
    warns = sum(1 for r in results if r.verdict == UtilityVerdict.WARN)
    fails = sum(1 for r in results if r.verdict == UtilityVerdict.FAIL)

    privacy_capped = sum(1 for r in results if r.privacy_cap < 1.0)

    # Overall utility gate
    privacy_gate_failed = False
    if blind_summary:
        privacy_gate_failed = blind_summary.get("privacy_gate") == "fail"
    if decoy_summary and decoy_summary.get("decoy_gate") == "fail":
        privacy_gate_failed = True

    if fails > 0 or avg_score < 0.65:
        utility_gate = "fail"
    elif avg_score < 0.80:
        utility_gate = "warn"
    elif privacy_gate_failed:
        # Hard rule: if privacy fails, utility gate is WARN at best
        utility_gate = "warn"
    elif warns > 0:
        utility_gate = "warn"
    else:
        utility_gate = "pass"

    per_company: dict[str, dict[str, Any]] = {}
    for r in results:
        per_company[r.company_id] = r.to_public_dict()

    return {
        "schema_version": "1.0",
        "aggregate_kind": "multi_company_utility_v3_2",
        "companies_reviewed": n,
        "average_utility_score": avg_score,
        "min_score": min_score,
        "max_score": max_score,
        "verdict_pass_count": passes,
        "verdict_warn_count": warns,
        "verdict_fail_count": fails,
        "privacy_capped_count": privacy_capped,
        "per_company": per_company,
        "utility_gate": utility_gate,
    }
