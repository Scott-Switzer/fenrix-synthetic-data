"""Utility-preservation scoring for Phase 8C.

Measures whether sanitized outputs still communicate the same broad
business/investment thesis without revealing source identity.

Compares structured signals, not exact text. Private source thesis is
compared against public packet thesis extracted from sanitized outputs.

Scoring rules:
- overall_utility_score >= 0.70 = PASS
- 0.55-0.70 = WARNING
- <0.55 = FAIL
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

# ── Thesis schemas ────────────────────────────────────────────────────────


@dataclass
class CompanyThesis:
    """Broad company investment/finance thesis — signals only, no identity."""

    anonymized_company_id: str
    business_model: str = "unknown"
    product_exposure: list[str] = field(default_factory=list)
    fundamentals_signal: str = "mixed"  # strong, mixed, weak
    valuation_signal: str = "unknown"  # low, fair, high, unknown
    profitability_signal: str = "mixed"  # strong, mixed, weak
    balance_sheet_signal: str = "mixed"  # strong, mixed, weak
    growth_signal: str = "mixed"  # positive, flat, negative, mixed
    risk_signals: list[str] = field(default_factory=list)
    market_signal: str = "mixed"  # momentum, value, defensive, cyclical, mixed
    teaching_goal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "anonymized_company_id": self.anonymized_company_id,
            "business_model": self.business_model,
            "product_exposure": self.product_exposure,
            "fundamentals_signal": self.fundamentals_signal,
            "valuation_signal": self.valuation_signal,
            "profitability_signal": self.profitability_signal,
            "balance_sheet_signal": self.balance_sheet_signal,
            "growth_signal": self.growth_signal,
            "risk_signals": self.risk_signals,
            "market_signal": self.market_signal,
            "teaching_goal": self.teaching_goal,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompanyThesis:
        return cls(
            anonymized_company_id=data.get("anonymized_company_id", ""),
            business_model=data.get("business_model", "unknown"),
            product_exposure=data.get("product_exposure", []),
            fundamentals_signal=data.get("fundamentals_signal", "mixed"),
            valuation_signal=data.get("valuation_signal", "unknown"),
            profitability_signal=data.get("profitability_signal", "mixed"),
            balance_sheet_signal=data.get("balance_sheet_signal", "mixed"),
            growth_signal=data.get("growth_signal", "mixed"),
            risk_signals=data.get("risk_signals", []),
            market_signal=data.get("market_signal", "mixed"),
            teaching_goal=data.get("teaching_goal", ""),
        )


# ── Scoring ───────────────────────────────────────────────────────────────


@dataclass
class UtilityScoreDetail:
    """Private utility scoring detail with source vs public comparison."""

    business_model_match: bool = False
    product_exposure_overlap: float = 0.0
    fundamentals_match: bool = False
    valuation_match: bool = False
    profitability_match: bool = False
    balance_sheet_match: bool = False
    growth_match: bool = False
    risk_overlap: float = 0.0
    market_signal_match: bool = False
    overall_utility_score: float = 0.0
    verdict: str = "WARN"  # PASS, WARN, FAIL

    def to_dict(self) -> dict[str, Any]:
        return {
            "business_model_match": self.business_model_match,
            "product_exposure_overlap": round(self.product_exposure_overlap, 2),
            "fundamentals_match": self.fundamentals_match,
            "valuation_match": self.valuation_match,
            "profitability_match": self.profitability_match,
            "balance_sheet_match": self.balance_sheet_match,
            "growth_match": self.growth_match,
            "risk_overlap": round(self.risk_overlap, 2),
            "market_signal_match": self.market_signal_match,
            "overall_utility_score": round(self.overall_utility_score, 2),
            "verdict": self.verdict,
        }


@dataclass
class PublicUtilitySummary:
    """Redacted public utility summary — no source identity."""

    company_id: str
    overall_utility_score: float = 0.0
    verdict: str = "WARN"
    signals_preserved: list[str] = field(default_factory=list)
    signals_lost: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "overall_utility_score": round(self.overall_utility_score, 2),
            "verdict": self.verdict,
            "signals_preserved": self.signals_preserved,
            "signals_lost": self.signals_lost,
        }


@dataclass
class UtilityPreservationResult:
    """Complete utility preservation result."""

    private: UtilityScoreDetail
    public: PublicUtilitySummary


def score_utility_preservation(
    source_thesis: CompanyThesis,
    public_thesis: CompanyThesis,
    threshold_pass: float = 0.70,
    threshold_warn: float = 0.55,
) -> UtilityPreservationResult:
    """Score utility preservation between source and public theses.

    Args:
        source_thesis: Private source company thesis (may be derived from private data).
        public_thesis: Public packet thesis (extracted from sanitized outputs).
        threshold_pass: Minimum score for PASS verdict.
        threshold_warn: Minimum score for WARN verdict (below = FAIL).

    Returns:
        UtilityPreservationResult with private and public components.
    """
    detail = UtilityScoreDetail()

    # 1. Business model match (binary)
    detail.business_model_match = (
        source_thesis.business_model.lower() == public_thesis.business_model.lower()
    )

    # 2. Product exposure overlap (Jaccard-like)
    source_prods = {p.lower().strip() for p in source_thesis.product_exposure}
    public_prods = {p.lower().strip() for p in public_thesis.product_exposure}
    if source_prods:
        detail.product_exposure_overlap = (
            len(source_prods & public_prods) / len(source_prods)
        )

    # 3. Fundamentals match
    detail.fundamentals_match = (
        source_thesis.fundamentals_signal.lower()
        == public_thesis.fundamentals_signal.lower()
    )

    # 4. Valuation match
    detail.valuation_match = (
        source_thesis.valuation_signal.lower()
        == public_thesis.valuation_signal.lower()
    )

    # 5. Profitability match
    detail.profitability_match = (
        source_thesis.profitability_signal.lower()
        == public_thesis.profitability_signal.lower()
    )

    # 6. Balance sheet match
    detail.balance_sheet_match = (
        source_thesis.balance_sheet_signal.lower()
        == public_thesis.balance_sheet_signal.lower()
    )

    # 7. Growth match
    detail.growth_match = (
        source_thesis.growth_signal.lower()
        == public_thesis.growth_signal.lower()
    )

    # 8. Risk overlap
    source_risks = {r.lower().strip() for r in source_thesis.risk_signals}
    public_risks = {r.lower().strip() for r in public_thesis.risk_signals}
    if source_risks:
        detail.risk_overlap = len(source_risks & public_risks) / len(source_risks)

    # 9. Market signal match
    detail.market_signal_match = (
        source_thesis.market_signal.lower()
        == public_thesis.market_signal.lower()
    )

    # Compute weighted overall score
    weights = {
        "business_model": 0.20,
        "product_exposure": 0.10,
        "fundamentals": 0.10,
        "valuation": 0.10,
        "profitability": 0.10,
        "balance_sheet": 0.10,
        "growth": 0.10,
        "risk": 0.10,
        "market_signal": 0.10,
    }

    detail.overall_utility_score = (
        weights["business_model"] * float(detail.business_model_match)
        + weights["product_exposure"] * detail.product_exposure_overlap
        + weights["fundamentals"] * float(detail.fundamentals_match)
        + weights["valuation"] * float(detail.valuation_match)
        + weights["profitability"] * float(detail.profitability_match)
        + weights["balance_sheet"] * float(detail.balance_sheet_match)
        + weights["growth"] * float(detail.growth_match)
        + weights["risk"] * detail.risk_overlap
        + weights["market_signal"] * float(detail.market_signal_match)
    )

    # Determine verdict
    if detail.overall_utility_score >= threshold_pass:
        detail.verdict = "PASS"
    elif detail.overall_utility_score >= threshold_warn:
        detail.verdict = "WARN"
    else:
        detail.verdict = "FAIL"

    # Build public summary
    signals_preserved: list[str] = []
    signals_lost: list[str] = []

    if detail.business_model_match:
        signals_preserved.append("business_model")
    else:
        signals_lost.append("business_model")

    if detail.product_exposure_overlap >= 0.5:
        signals_preserved.append("product_exposure")
    else:
        signals_lost.append("product_exposure")

    if detail.fundamentals_match:
        signals_preserved.append("fundamentals")
    else:
        signals_lost.append("fundamentals")

    if detail.valuation_match:
        signals_preserved.append("valuation")
    else:
        signals_lost.append("valuation")

    if detail.profitability_match:
        signals_preserved.append("profitability")
    else:
        signals_lost.append("profitability")

    if detail.balance_sheet_match:
        signals_preserved.append("balance_sheet")
    else:
        signals_lost.append("balance_sheet")

    if detail.growth_match:
        signals_preserved.append("growth")
    else:
        signals_lost.append("growth")

    if detail.risk_overlap >= 0.5:
        signals_preserved.append("risk_signals")
    else:
        signals_lost.append("risk_signals")

    if detail.market_signal_match:
        signals_preserved.append("market_signal")
    else:
        signals_lost.append("market_signal")

    public = PublicUtilitySummary(
        company_id=source_thesis.anonymized_company_id,
        overall_utility_score=detail.overall_utility_score,
        verdict=detail.verdict,
        signals_preserved=signals_preserved,
        signals_lost=signals_lost,
    )

    return UtilityPreservationResult(private=detail, public=public)


def extract_public_thesis(public_dir: Path, company_id: str) -> CompanyThesis:
    """Extract a company thesis from public sanitized outputs.

    Analyzes profile.md, transformed_metrics.csv, ratio_summary.csv,
    and other public files to infer the broad thesis signals.

    This is a heuristic extraction — it does not use ML or NLP.
    It scans for keywords and patterns in the public output.

    Args:
        public_dir: Public output directory.
        company_id: Anonymized company ID.

    Returns:
        CompanyThesis inferred from public outputs.
    """
    company_path = public_dir / "anonymized" / company_id
    if not company_path.exists():
        company_path = public_dir

    thesis = CompanyThesis(anonymized_company_id=company_id)

    # Collect all text content
    all_text: list[str] = []
    for fp in sorted(company_path.rglob("*")):
        if not fp.is_file():
            continue
        if fp.suffix.lower() in {".md", ".txt", ".csv"}:
            try:
                all_text.append(fp.read_text(encoding="utf-8", errors="replace").lower())
            except (OSError, UnicodeDecodeError):
                continue

    combined = " ".join(all_text)

    # Business model detection
    if any(w in combined for w in ["banking", "deposits", "lending", "loans", "interest income"]):
        thesis.business_model = "banking and lending"
    elif any(w in combined for w in ["insurance", "underwriting", "premiums"]):
        thesis.business_model = "insurance"
    elif any(w in combined for w in ["technology", "software", "saas", "platform"]):
        thesis.business_model = "technology"
    elif any(w in combined for w in ["retail", "stores", "ecommerce"]):
        thesis.business_model = "retail"
    elif any(w in combined for w in ["manufacturing", "production", "factory"]):
        thesis.business_model = "manufacturing"
    elif any(w in combined for w in ["financial services", "wealth management", "asset management"]):
        thesis.business_model = "financial services"
    else:
        thesis.business_model = "diversified"

    # Product exposure
    product_keywords = {
        "consumer banking": ["consumer", "retail banking", "checking", "savings"],
        "commercial banking": ["commercial", "business banking", "corporate lending"],
        "wealth management": ["wealth", "advisory", "private banking"],
        "insurance": ["insurance", "underwriting"],
        "investment banking": ["investment banking", "m&a", "capital markets"],
    }
    products_found: list[str] = []
    for product, keywords in product_keywords.items():
        if any(kw in combined for kw in keywords):
            products_found.append(product)
    thesis.product_exposure = products_found if products_found else ["financial services"]

    # Fundamentals signal
    if any(w in combined for w in ["strong revenue", "revenue growth", "growing revenue"]):
        thesis.fundamentals_signal = "strong"
    elif any(w in combined for w in ["declining revenue", "revenue decline", "weak revenue"]):
        thesis.fundamentals_signal = "weak"
    else:
        thesis.fundamentals_signal = "mixed"

    # Valuation signal
    if any(w in combined for w in ["undervalued", "attractive valuation", "low p/e", "low price"]):
        thesis.valuation_signal = "low"
    elif any(w in combined for w in ["overvalued", "high valuation", "premium"]):
        thesis.valuation_signal = "high"
    elif any(w in combined for w in ["fair value", "fairly valued"]):
        thesis.valuation_signal = "fair"
    else:
        thesis.valuation_signal = "unknown"

    # Profitability signal
    if any(w in combined for w in ["strong margins", "high profitability", "profitable"]):
        thesis.profitability_signal = "strong"
    elif any(w in combined for w in ["margin pressure", "declining margins", "unprofitable"]):
        thesis.profitability_signal = "weak"
    else:
        thesis.profitability_signal = "mixed"

    # Balance sheet signal
    if any(w in combined for w in ["strong balance sheet", "low debt", "high liquidity"]):
        thesis.balance_sheet_signal = "strong"
    elif any(w in combined for w in ["high leverage", "weak balance sheet", "debt burden"]):
        thesis.balance_sheet_signal = "weak"
    else:
        thesis.balance_sheet_signal = "mixed"

    # Growth signal
    if any(w in combined for w in ["growth", "expanding", "increasing"]):
        thesis.growth_signal = "positive"
    elif any(w in combined for w in ["declining", "shrinking", "contracting"]):
        thesis.growth_signal = "negative"
    else:
        thesis.growth_signal = "mixed"

    # Risk signals
    risk_keywords = {
        "regulatory": ["regulatory", "compliance", "regulation"],
        "competition": ["competition", "competitive", "market share"],
        "margin pressure": ["margin", "cost pressure", "pricing pressure"],
        "credit risk": ["credit", "default", "non-performing"],
        "interest rate": ["interest rate", "rate sensitivity"],
    }
    risks_found: list[str] = []
    for risk, keywords in risk_keywords.items():
        if any(kw in combined for kw in keywords):
            risks_found.append(risk)
    thesis.risk_signals = risks_found if risks_found else ["general business risk"]

    # Market signal
    if any(w in combined for w in ["momentum", "trending", "outperforming"]):
        thesis.market_signal = "momentum"
    elif any(w in combined for w in ["value", "undervalued", "bargain"]):
        thesis.market_signal = "value"
    elif any(w in combined for w in ["defensive", "stable", "recession"]):
        thesis.market_signal = "defensive"
    elif any(w in combined for w in ["cyclical", "economic cycle"]):
        thesis.market_signal = "cyclical"
    else:
        thesis.market_signal = "mixed"

    return thesis


def write_utility_reports(
    result: UtilityPreservationResult,
    private_dir: Path,
    qa_dir: Path,
) -> tuple[Path, Path]:
    """Write private and public utility reports.

    Args:
        result: Utility preservation result.
        private_dir: Private output directory.
        qa_dir: Public QA directory.

    Returns:
        Tuple of (private_path, public_path).
    """
    private_dir.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)

    private_path = private_dir / "utility_preservation_private.json"
    private_path.write_bytes(
        orjson.dumps(
            result.private.to_dict(),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
        )
    )

    public_path = qa_dir / "utility_preservation_summary.json"
    public_path.write_bytes(
        orjson.dumps(
            result.public.to_dict(),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
        )
    )

    return private_path, public_path
