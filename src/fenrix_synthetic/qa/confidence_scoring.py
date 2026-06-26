"""Confidence scoring for LLM blind-guess and decoy-aware adversarial review.

Scores model responses against known source mappings to determine if
the release passes privacy review. The scoring function may use private
source mappings, but scoring output must stay private unless explicitly
redacted for public output.

Blind-guess scoring rules (strict mode):
- actual_source_top_1 = hard fail
- actual_source_top_3 = hard fail
- confidence_high = hard fail
- confidence_medium AND actual_source_in_candidates = hard fail
- confidence_medium without actual source = warning
- confidence_low with broad peer candidates = pass/warn
- no justified guess = target pass
- malformed output = fail in strict mode
- provider error = fail in strict mode, warn in non-strict mode

V3.1 Decoy-aware scoring rules:
- FAIL: true source is top-1 with medium or high confidence
- FAIL: true source is top-3 with high confidence
- FAIL: evidence includes direct_identifier, exact_number, or metadata_leak basis
- WARN: true source appears top-3 with low confidence and evidence is
  business_model or sector_only only
- PASS: true source not top-1/top-3 and no direct leaks detected
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ConfidenceLevel(StrEnum):
    """Confidence levels from model output."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ScoreVerdict(StrEnum):
    """Verdict from confidence scoring."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class PrivateScoreDetail:
    """Private scoring detail — contains actual source mapping."""

    verdict: ScoreVerdict
    reason: str
    actual_source_company: str | None = None
    actual_source_ticker: str | None = None
    top1_is_actual: bool = False
    actual_in_top3: bool = False
    actual_in_candidates: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "actual_source_company": self.actual_source_company,
            "actual_source_ticker": self.actual_source_ticker,
            "top1_is_actual": self.top1_is_actual,
            "actual_in_top3": self.actual_in_top3,
            "actual_in_candidates": self.actual_in_candidates,
        }


@dataclass
class PublicScoreSummary:
    """Redacted public score summary — no actual source mapping."""

    verdict: ScoreVerdict
    reason: str
    model_confidence: str = "none"
    model_top1_company: str | None = None
    model_refused: bool = True
    candidate_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "model_confidence": self.model_confidence,
            "model_top1_company": self.model_top1_company,
            "model_refused_or_uncertain": self.model_refused,
            "candidate_count": self.candidate_count,
        }


@dataclass
class ScoreResult:
    """Complete scoring result with both private and public components."""

    private: PrivateScoreDetail
    public: PublicScoreSummary


def score_blind_guess(
    model_response: dict[str, Any],
    actual_source_company: str | None = None,
    actual_source_ticker: str | None = None,
    *,
    strict: bool = True,
) -> ScoreResult:
    """Score a model's blind-guess response against known source mapping.

    Args:
        model_response: Parsed JSON response from the LLM.
        actual_source_company: The real source company name (private).
        actual_source_ticker: The real source ticker (private).
        strict: If True, malformed output and provider errors fail closed.

    Returns:
        ScoreResult with private details and public summary.

    Raises:
        ValueError: If model_response is missing required keys and strict=True.
    """
    # ── Validate response shape ─────────────────────────────────────
    confidence_str = model_response.get("confidence", "none")
    top_candidates: list[dict[str, Any]] = model_response.get("top_candidates", [])
    most_likely_company = model_response.get("most_likely_company")
    most_likely_ticker = model_response.get("most_likely_ticker")
    refusal = model_response.get("refusal_or_uncertain", True)

    # ── Check if model refused ──────────────────────────────────────
    if refusal and most_likely_company is None and not top_candidates:
        # Model refused to guess — this is the target pass scenario
        private = PrivateScoreDetail(
            verdict=ScoreVerdict.PASS,
            reason="Model refused to make a company-level guess.",
            actual_source_company=actual_source_company,
            actual_source_ticker=actual_source_ticker,
        )
        public = PublicScoreSummary(
            verdict=ScoreVerdict.PASS,
            reason="Model refused to make a company-level guess.",
            model_confidence="none",
            model_refused=True,
        )
        return ScoreResult(private=private, public=public)

    # ── Normalize confidence ────────────────────────────────────────
    try:
        confidence = ConfidenceLevel(confidence_str.lower())
    except ValueError:
        confidence = ConfidenceLevel.LOW

    # ── Check top-1 match ───────────────────────────────────────────
    top1_is_actual = _is_company_match(
        most_likely_company, most_likely_ticker, actual_source_company, actual_source_ticker
    )

    if top1_is_actual:
        private = PrivateScoreDetail(
            verdict=ScoreVerdict.FAIL,
            reason=f"Model correctly identified source as top-1: {most_likely_company} ({most_likely_ticker})",
            actual_source_company=actual_source_company,
            actual_source_ticker=actual_source_ticker,
            top1_is_actual=True,
            actual_in_top3=True,
            actual_in_candidates=True,
        )
        public = PublicScoreSummary(
            verdict=ScoreVerdict.FAIL,
            reason="Model identified source company as most likely candidate.",
            model_confidence=confidence.value,
            model_top1_company=most_likely_company,
            model_refused=False,
            candidate_count=len(top_candidates),
        )
        return ScoreResult(private=private, public=public)

    # ── Check top-3 match ───────────────────────────────────────────
    actual_in_top3 = False
    actual_in_candidates = False
    for candidate in top_candidates[:3]:
        if _is_company_match(
            candidate.get("company"),
            candidate.get("ticker"),
            actual_source_company,
            actual_source_ticker,
        ):
            actual_in_top3 = True
    for candidate in top_candidates:
        if _is_company_match(
            candidate.get("company"),
            candidate.get("ticker"),
            actual_source_company,
            actual_source_ticker,
        ):
            actual_in_candidates = True

    if actual_in_top3:
        private = PrivateScoreDetail(
            verdict=ScoreVerdict.FAIL,
            reason="Actual source found in model's top-3 candidates.",
            actual_source_company=actual_source_company,
            actual_source_ticker=actual_source_ticker,
            actual_in_top3=True,
            actual_in_candidates=True,
        )
        public = PublicScoreSummary(
            verdict=ScoreVerdict.FAIL,
            reason="Source company appeared in model's top-3 candidates.",
            model_confidence=confidence.value,
            model_top1_company=most_likely_company,
            model_refused=False,
            candidate_count=len(top_candidates),
        )
        return ScoreResult(private=private, public=public)

    # ── High confidence = hard fail ─────────────────────────────────
    if confidence == ConfidenceLevel.HIGH:
        private = PrivateScoreDetail(
            verdict=ScoreVerdict.FAIL,
            reason=f"Model expressed high confidence ({confidence.value}) in identification.",
            actual_source_company=actual_source_company,
            actual_source_ticker=actual_source_ticker,
        )
        public = PublicScoreSummary(
            verdict=ScoreVerdict.FAIL,
            reason="Model expressed high confidence in identification.",
            model_confidence=confidence.value,
            model_top1_company=most_likely_company,
            model_refused=False,
            candidate_count=len(top_candidates),
        )
        return ScoreResult(private=private, public=public)

    # ── Medium confidence with actual source in candidates = FAIL ───
    if confidence == ConfidenceLevel.MEDIUM and actual_in_candidates:
        private = PrivateScoreDetail(
            verdict=ScoreVerdict.FAIL,
            reason="Medium confidence with actual source in candidate list.",
            actual_source_company=actual_source_company,
            actual_source_ticker=actual_source_ticker,
            actual_in_candidates=True,
        )
        public = PublicScoreSummary(
            verdict=ScoreVerdict.FAIL,
            reason="Medium confidence with identifiable candidate overlap.",
            model_confidence=confidence.value,
            model_top1_company=most_likely_company,
            model_refused=False,
            candidate_count=len(top_candidates),
        )
        return ScoreResult(private=private, public=public)

    # ── Medium confidence without actual source = WARN ──────────────
    if confidence == ConfidenceLevel.MEDIUM:
        private = PrivateScoreDetail(
            verdict=ScoreVerdict.WARN,
            reason="Medium confidence but actual source not in candidates — warning.",
            actual_source_company=actual_source_company,
            actual_source_ticker=actual_source_ticker,
        )
        public = PublicScoreSummary(
            verdict=ScoreVerdict.WARN,
            reason="Medium confidence without direct source match — review recommended.",
            model_confidence=confidence.value,
            model_top1_company=most_likely_company,
            model_refused=False,
            candidate_count=len(top_candidates),
        )
        return ScoreResult(private=private, public=public)

    # ── Low confidence / no match = PASS ────────────────────────────
    private = PrivateScoreDetail(
        verdict=ScoreVerdict.PASS,
        reason="Low confidence with no actual source match.",
        actual_source_company=actual_source_company,
        actual_source_ticker=actual_source_ticker,
    )
    public = PublicScoreSummary(
        verdict=ScoreVerdict.PASS,
        reason="Low confidence — insufficient evidence for identification.",
        model_confidence=confidence.value,
        model_top1_company=most_likely_company,
        model_refused=False,
        candidate_count=len(top_candidates),
    )
    return ScoreResult(private=private, public=public)


def _is_company_match(
    guessed_company: str | None,
    guessed_ticker: str | None,
    actual_company: str | None,
    actual_ticker: str | None,
) -> bool:
    """Check if a guessed company/ticker matches the actual source.

    Uses case-insensitive comparison with basic normalization.
    Private function — never exposes values to public output.
    """
    if not actual_company and not actual_ticker:
        return False
    if not guessed_company and not guessed_ticker:
        return False

    if actual_company and guessed_company:
        actual_norm = actual_company.lower().strip()
        guessed_norm = guessed_company.lower().strip()
        if actual_norm == guessed_norm:
            return True
        if actual_norm in guessed_norm or guessed_norm in actual_norm:
            return True

    if actual_ticker and guessed_ticker:
        actual_t_norm = actual_ticker.upper().strip()
        guessed_t_norm = guessed_ticker.upper().strip()
        if actual_t_norm == guessed_t_norm:
            return True

    return False


def redact_for_public(private_score: PrivateScoreDetail) -> PublicScoreSummary:
    """Create a redacted public summary from private scoring.

    Removes all actual source mapping. Safe for public release artifacts.
    """
    return PublicScoreSummary(
        verdict=private_score.verdict,
        reason=_redact_reason(private_score.reason),
    )


def _redact_reason(reason: str) -> str:
    """Remove any actual source names from a reason string."""
    # Replace company names with placeholder
    result = reason
    # Strip anything that looks like a company/ticker in parentheses
    import re

    result = re.sub(r"\([^)]*\)", "(redacted)", result)
    result = re.sub(r": .*", ": [redacted]", result)
    return result


# ── V3.1 Decoy-aware scoring ─────────────────────────────────────────

#: Evidence bases that indicate a direct privacy leak.
_DIRECT_LEAK_BASES: frozenset[str] = frozenset({
    "direct_identifier",
    "exact_number",
    "metadata_leak",
    "product_event_fingerprint",
})

#: Evidence bases that are acceptable (broad inference only).
_ACCEPTABLE_BASES: frozenset[str] = frozenset({
    "business_model",
    "sector_only",
})

#: All valid evidence basis values.
_VALID_BASES: frozenset[str] = frozenset({
    "business_model",
    "financial_pattern",
    "product_event_fingerprint",
    "direct_identifier",
    "exact_number",
    "metadata_leak",
    "sector_only",
    "unknown",
})


@dataclass
class PrivateDecoyScoreDetail:
    """Private decoy scoring detail — contains actual source mapping."""

    verdict: ScoreVerdict
    reason: str
    actual_source_label: str  # e.g. "Candidate A"
    actual_source_company: str | None = None
    actual_source_ticker: str | None = None
    top1_is_actual: bool = False
    actual_in_top3: bool = False
    direct_leak_detected: bool = False
    evidence_bases: list[str] = field(default_factory=list)
    inference_basis: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "actual_source_label": self.actual_source_label,
            "actual_source_company": self.actual_source_company,
            "actual_source_ticker": self.actual_source_ticker,
            "top1_is_actual": self.top1_is_actual,
            "actual_in_top3": self.actual_in_top3,
            "direct_leak_detected": self.direct_leak_detected,
            "evidence_bases": self.evidence_bases,
            "inference_basis": self.inference_basis,
        }


@dataclass
class PublicDecoyScoreSummary:
    """Redacted public decoy score summary — no real source names or labels."""

    verdict: ScoreVerdict
    reason: str
    model_confidence: str = "none"
    top_guess_is_actual: bool = False
    actual_in_top3: bool = False
    direct_leak_detected: bool = False
    inference_basis: str = "unknown"
    evidence_basis_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "model_confidence": self.model_confidence,
            "top_guess_is_actual": self.top_guess_is_actual,
            "actual_in_top3": self.actual_in_top3,
            "direct_leak_detected": self.direct_leak_detected,
            "inference_basis": self.inference_basis,
            "evidence_basis_counts": self.evidence_basis_counts,
        }


@dataclass
class DecoyScoreResult:
    """Complete decoy-aware scoring result — private and public components."""

    private: PrivateDecoyScoreDetail
    public: PublicDecoyScoreSummary


def score_decoy_aware_guess(
    model_response: dict[str, Any],
    *,
    actual_source_label: str,
    private_label_map: dict[str, tuple[str, str | None]],
    company_id: str = "UNKNOWN",
) -> DecoyScoreResult:
    """Score a decoy-aware LLM response against the private candidate mapping.

    Args:
        model_response: Parsed JSON response from the LLM with keys
            ``top_guess_label``, ``top_guess_confidence``, ``top_3_labels``,
            ``evidence``, ``inference_basis``, ``direct_leak_detected``.
        actual_source_label: Which opaque label (e.g. "Candidate B") maps
            to the true source company.
        private_label_map: Mapping from opaque label to
            ``(company_name, ticker_or_None)``. MUST stay private.
        company_id: Anonymized company ID for context.

    Returns:
        ``DecoyScoreResult`` with private (full) and public (redacted) scores.
    """
    top_guess_label = model_response.get("top_guess_label")
    top_guess_confidence = str(model_response.get("top_guess_confidence", "low")).lower()
    top_3_labels: list[str] = model_response.get("top_3_labels", [])
    evidence: list[dict[str, Any]] = model_response.get("evidence", [])
    inference_basis = str(model_response.get("inference_basis", "unknown"))
    direct_leak_detected = bool(model_response.get("direct_leak_detected", False))

    # ── Validate confidence ─────────────────────────────────────────
    if top_guess_confidence not in {"low", "medium", "high"}:
        top_guess_confidence = "low"

    # ── Check top-1 match ───────────────────────────────────────────
    top1_is_actual = top_guess_label == actual_source_label
    actual_in_top3 = actual_source_label in top_3_labels[:3]

    # ── Check evidence for direct leaks ─────────────────────────────
    evidence_bases: list[str] = []
    has_direct_leak = direct_leak_detected
    for e in evidence:
        basis = str(e.get("basis", "unknown")).lower()
        if basis in _VALID_BASES:
            evidence_bases.append(basis)
        if basis in _DIRECT_LEAK_BASES:
            has_direct_leak = True

    # ── Scoring logic ───────────────────────────────────────────────

    # Rule 1: Direct leak evidence = FAIL
    if has_direct_leak:
        actual_name, actual_ticker = private_label_map.get(actual_source_label, ("unknown", None))
        private = PrivateDecoyScoreDetail(
            verdict=ScoreVerdict.FAIL,
            reason=(
                f"Decoy review detected direct leak evidence "
                f"(bases: {[b for b in evidence_bases if b in _DIRECT_LEAK_BASES]})."
            ),
            actual_source_label=actual_source_label,
            actual_source_company=actual_name,
            actual_source_ticker=actual_ticker,
            top1_is_actual=top1_is_actual,
            actual_in_top3=actual_in_top3,
            direct_leak_detected=True,
            evidence_bases=evidence_bases,
            inference_basis=inference_basis,
        )
        public = PublicDecoyScoreSummary(
            verdict=ScoreVerdict.FAIL,
            reason="Decoy review detected evidence that could enable exact identification.",
            model_confidence=top_guess_confidence,
            top_guess_is_actual=top1_is_actual,
            actual_in_top3=actual_in_top3,
            direct_leak_detected=True,
            inference_basis=inference_basis,
            evidence_basis_counts=_count_bases(evidence_bases),
        )
        return DecoyScoreResult(private=private, public=public)

    # Rule 2: True source top-1 with medium/high confidence = FAIL
    if top1_is_actual and top_guess_confidence in {"medium", "high"}:
        actual_name, actual_ticker = private_label_map.get(actual_source_label, ("unknown", None))
        private = PrivateDecoyScoreDetail(
            verdict=ScoreVerdict.FAIL,
            reason=(
                f"Model correctly identified true source as top-1 "
                f"({actual_source_label}) with {top_guess_confidence} confidence."
            ),
            actual_source_label=actual_source_label,
            actual_source_company=actual_name,
            actual_source_ticker=actual_ticker,
            top1_is_actual=True,
            actual_in_top3=True,
            direct_leak_detected=False,
            evidence_bases=evidence_bases,
            inference_basis=inference_basis,
        )
        public = PublicDecoyScoreSummary(
            verdict=ScoreVerdict.FAIL,
            reason="Model identified true source as most likely candidate with meaningful confidence.",
            model_confidence=top_guess_confidence,
            top_guess_is_actual=True,
            actual_in_top3=True,
            direct_leak_detected=False,
            inference_basis=inference_basis,
            evidence_basis_counts=_count_bases(evidence_bases),
        )
        return DecoyScoreResult(private=private, public=public)

    # Rule 3: True source top-3 with high confidence = FAIL
    if actual_in_top3 and top_guess_confidence == "high":
        actual_name, actual_ticker = private_label_map.get(actual_source_label, ("unknown", None))
        private = PrivateDecoyScoreDetail(
            verdict=ScoreVerdict.FAIL,
            reason=(
                f"True source ({actual_source_label}) appeared in top-3 "
                f"with high confidence."
            ),
            actual_source_label=actual_source_label,
            actual_source_company=actual_name,
            actual_source_ticker=actual_ticker,
            top1_is_actual=False,
            actual_in_top3=True,
            direct_leak_detected=False,
            evidence_bases=evidence_bases,
            inference_basis=inference_basis,
        )
        public = PublicDecoyScoreSummary(
            verdict=ScoreVerdict.FAIL,
            reason="True source appeared in top-3 with high confidence.",
            model_confidence=top_guess_confidence,
            top_guess_is_actual=False,
            actual_in_top3=True,
            direct_leak_detected=False,
            inference_basis=inference_basis,
            evidence_basis_counts=_count_bases(evidence_bases),
        )
        return DecoyScoreResult(private=private, public=public)

    # Rule 4: True source top-3 with low confidence, broad evidence only = WARN
    if actual_in_top3 and top_guess_confidence == "low":
        all_broad = all(b in _ACCEPTABLE_BASES or b == "financial_pattern" for b in evidence_bases)
        actual_name, actual_ticker = private_label_map.get(actual_source_label, ("unknown", None))
        if all_broad:
            private = PrivateDecoyScoreDetail(
                verdict=ScoreVerdict.WARN,
                reason=(
                    f"True source ({actual_source_label}) appeared in top-3 "
                    f"with low confidence and broad evidence only."
                ),
                actual_source_label=actual_source_label,
                actual_source_company=actual_name,
                actual_source_ticker=actual_ticker,
                top1_is_actual=False,
                actual_in_top3=True,
                direct_leak_detected=False,
                evidence_bases=evidence_bases,
                inference_basis=inference_basis,
            )
            public = PublicDecoyScoreSummary(
                verdict=ScoreVerdict.WARN,
                reason="True source appeared in top-3 with low confidence — broad inference only.",
                model_confidence=top_guess_confidence,
                top_guess_is_actual=False,
                actual_in_top3=True,
                direct_leak_detected=False,
                inference_basis=inference_basis,
                evidence_basis_counts=_count_bases(evidence_bases),
            )
            return DecoyScoreResult(private=private, public=public)

        # Non-broad evidence in top-3 = FAIL
        private = PrivateDecoyScoreDetail(
            verdict=ScoreVerdict.FAIL,
            reason=(
                f"True source ({actual_source_label}) appeared in top-3 "
                f"with non-broad evidence (bases: {evidence_bases})."
            ),
            actual_source_label=actual_source_label,
            actual_source_company=actual_name,
            actual_source_ticker=actual_ticker,
            top1_is_actual=False,
            actual_in_top3=True,
            direct_leak_detected=False,
            evidence_bases=evidence_bases,
            inference_basis=inference_basis,
        )
        public = PublicDecoyScoreSummary(
            verdict=ScoreVerdict.FAIL,
            reason="True source appeared in top-3 with non-broad evidence.",
            model_confidence=top_guess_confidence,
            top_guess_is_actual=False,
            actual_in_top3=True,
            direct_leak_detected=False,
            inference_basis=inference_basis,
            evidence_basis_counts=_count_bases(evidence_bases),
        )
        return DecoyScoreResult(private=private, public=public)

    # Rule 5: True source not top-1/top-3, no direct leak = PASS
    actual_name, actual_ticker = private_label_map.get(actual_source_label, ("unknown", None))
    private = PrivateDecoyScoreDetail(
        verdict=ScoreVerdict.PASS,
        reason="True source not in top-1 or top-3 — decoy-aware privacy preserved.",
        actual_source_label=actual_source_label,
        actual_source_company=actual_name,
        actual_source_ticker=actual_ticker,
        top1_is_actual=False,
        actual_in_top3=False,
        direct_leak_detected=False,
        evidence_bases=evidence_bases,
        inference_basis=inference_basis,
    )
    public = PublicDecoyScoreSummary(
        verdict=ScoreVerdict.PASS,
        reason="True source not identified in top-3 candidates — privacy preserved.",
        model_confidence=top_guess_confidence,
        top_guess_is_actual=False,
        actual_in_top3=False,
        direct_leak_detected=False,
        inference_basis=inference_basis,
        evidence_basis_counts=_count_bases(evidence_bases),
    )
    return DecoyScoreResult(private=private, public=public)


def _count_bases(bases: list[str]) -> dict[str, int]:
    """Count occurrences of each evidence basis value."""
    counts: dict[str, int] = {}
    for b in bases:
        counts[b] = counts.get(b, 0) + 1
    return counts
