"""LLM blind-guess harness for adversarial review.

Orchestrates the blind review flow:
1. Collects public bundle content (no private data).
2. Runs the LLM provider against the public content.
3. Scores the response against private source mapping (private only).
4. Produces public (redacted) and private QA reports.

The model must receive ONLY public bundle content — no private source map,
no private audit, no source tickers, no actual company names.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from .confidence_scoring import (
    PrivateScoreDetail,
    PublicScoreSummary,
    ScoreResult,
    ScoreVerdict,
    score_blind_guess,
)
from .llm_provider import (
    LLMProvider,
    LLMProviderError,
    _build_blind_review_prompt,
)

# ── Public content collectors ─────────────────────────────────────────────


def collect_public_content(public_dir: Path, company_id: str) -> str:
    """Collect public bundle content for a single anonymized company.

    Reads only public-facing files from the public/ directory tree,
    never touching private/, raw/, identity/, checkpoints/, or similar.

    Args:
        public_dir: Path to the public/ directory for the company.
        company_id: The anonymized company ID.

    Returns:
        Concatenated public content string for LLM review.
    """
    parts: list[str] = []
    parts.append(f"# Anonymized Company: {company_id}\n")

    company_path = public_dir / "anonymized" / company_id
    if not company_path.exists():
        # Try flat structure
        company_path = public_dir

    for fp in sorted(company_path.rglob("*")):
        if not fp.is_file():
            continue
        rel = str(fp.relative_to(public_dir))

        # Skip forbidden paths even within public
        if _is_private_path(rel):
            continue

        suffix = fp.suffix.lower()
        if suffix in {".md", ".csv", ".json", ".txt"}:
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
                # Limit per-file content to avoid overwhelming the model
                if len(content) > 20_000:
                    content = content[:15_000] + "\n\n... [truncated] ...\n"
                parts.append(f"\n## File: {rel}\n\n{content}")
            except (OSError, UnicodeDecodeError):
                continue

    return "\n".join(parts)


def _is_private_path(rel_path: str) -> bool:
    """Check if a relative path indicates private content."""
    private_indicators = [
        "private/",
        "raw/",
        "identity/",
        "checkpoints/",
        "source/",
        "mappings/",
        "evidence_graph",
        "identity_map",
        "source_map",
        "llm_blind_guess_private",
        "replacement_plan",
    ]
    for indicator in private_indicators:
        if indicator in rel_path.lower():
            return True
    return False


# ── Result dataclass ──────────────────────────────────────────────────────


@dataclass
class BlindGuessResult:
    """Result of a complete blind-guess review run."""

    company_id: str
    provider_name: str
    model_name: str
    run_timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    raw_response: dict[str, Any] | None = None
    parse_error: str | None = None
    provider_error: str | None = None
    score_result: ScoreResult | None = None
    passed: bool = False
    hash: str = ""

    def to_private_dict(self) -> dict[str, Any]:
        """Serialize with private scoring details."""
        return {
            "company_id": self.company_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "run_timestamp": self.run_timestamp,
            "passed": self.passed,
            "hash": self.hash,
            "parse_error": self.parse_error,
            "provider_error": self.provider_error,
            "raw_response": self.raw_response,
            "score": self.score_result.private.to_dict() if self.score_result else None,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize without private scoring details — safe for public release."""
        return {
            "company_id": self.company_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "run_timestamp": self.run_timestamp,
            "passed": self.passed,
            "hash": self.hash,
            "parse_error": self.parse_error,
            "provider_error": self.provider_error,
            "score": self.score_result.public.to_dict() if self.score_result else None,
        }


# ── Harness ───────────────────────────────────────────────────────────────


class LLMBlindGuessHarness:
    """Orchestrates blind-guess adversarial review.

    Usage:
        provider = create_llm_provider("offline_stub")
        harness = LLMBlindGuessHarness(provider, strict=True)
        result = harness.review(public_dir, private_dir, company_id,
                                actual_source_company="Acme Corp")
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        strict: bool = True,
    ) -> None:
        self._provider = provider
        self._strict = strict

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    def review(
        self,
        public_dir: Path,
        private_dir: Path,
        company_id: str,
        *,
        actual_source_company: str | None = None,
        actual_source_ticker: str | None = None,
    ) -> BlindGuessResult:
        """Run blind review on public content for a single company.

        Args:
            public_dir: Path to the public/ directory.
            private_dir: Path to the private/ directory (for writing private report).
            company_id: The anonymized company ID.
            actual_source_company: The real source company name (private, never in public output).
            actual_source_ticker: The real source ticker (private, never in public output).

        Returns:
            BlindGuessResult with pass/fail and both private and public views.
        """
        result = BlindGuessResult(
            company_id=company_id,
            provider_name=self._provider.provider_name,
            model_name=self._provider.model_name,
        )

        # ── Step 1: Collect public content ──────────────────────────
        public_content = collect_public_content(public_dir, company_id)

        # ── Step 2: Run LLM review ──────────────────────────────────
        prompt = _build_blind_review_prompt(public_content, company_id)

        try:
            raw_response = self._provider.complete_json(prompt, timeout_s=120)
            result.raw_response = raw_response
        except LLMProviderError as e:
            result.provider_error = str(e)
            if self._strict:
                result.score_result = ScoreResult(
                    private=PrivateScoreDetail(
                        verdict=ScoreVerdict.FAIL,
                        reason=f"Provider error in strict mode: {e}",
                        actual_source_company=actual_source_company,
                        actual_source_ticker=actual_source_ticker,
                    ),
                    public=PublicScoreSummary(
                        verdict=ScoreVerdict.FAIL,
                        reason="Provider error prevented blind review.",
                    ),
                )
                result.passed = False
                result.hash = _compute_result_hash(result)
                return result
            else:
                # Non-strict: warn but don't fail
                result.score_result = ScoreResult(
                    private=PrivateScoreDetail(
                        verdict=ScoreVerdict.WARN,
                        reason=f"Provider error (non-strict): {e}",
                        actual_source_company=actual_source_company,
                        actual_source_ticker=actual_source_ticker,
                    ),
                    public=PublicScoreSummary(
                        verdict=ScoreVerdict.WARN,
                        reason="Provider error prevented blind review.",
                    ),
                )
                result.passed = True
                result.hash = _compute_result_hash(result)
                return result

        # ── Step 3: Validate response shape ─────────────────────────
        required_keys = {"confidence", "top_candidates"}
        missing = required_keys - set(raw_response.keys())
        if missing and self._strict:
            result.parse_error = f"Missing required keys: {missing}"
            result.score_result = ScoreResult(
                private=PrivateScoreDetail(
                    verdict=ScoreVerdict.FAIL,
                    reason=f"Malformed model output: missing keys {missing}",
                    actual_source_company=actual_source_company,
                    actual_source_ticker=actual_source_ticker,
                ),
                public=PublicScoreSummary(
                    verdict=ScoreVerdict.FAIL,
                    reason="Model response was malformed.",
                ),
            )
            result.passed = False
            result.hash = _compute_result_hash(result)
            return result

        # ── Step 4: Score against source ────────────────────────────
        score = score_blind_guess(
            raw_response,
            actual_source_company=actual_source_company,
            actual_source_ticker=actual_source_ticker,
            strict=self._strict,
        )
        result.score_result = score
        result.passed = score.private.verdict in {ScoreVerdict.PASS, ScoreVerdict.WARN}

        # ── Step 5: Write reports ───────────────────────────────────
        result.hash = _compute_result_hash(result)

        # Write private report
        private_qa_dir = private_dir / "qa"
        private_qa_dir.mkdir(parents=True, exist_ok=True)
        (private_qa_dir / "llm_blind_guess_private.json").write_bytes(
            orjson.dumps(
                result.to_private_dict(),
                option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
            )
        )

        return result

    def write_public_summary(self, result: BlindGuessResult, qa_dir: Path) -> Path:
        """Write the redacted public summary to the QA directory.

        Args:
            result: The blind guess result.
            qa_dir: Path to the public qa/ directory.

        Returns:
            Path to the written summary file.
        """
        qa_dir.mkdir(parents=True, exist_ok=True)
        summary_path = qa_dir / "llm_blind_guess_summary.json"
        summary_path.write_bytes(
            orjson.dumps(
                result.to_public_dict(),
                option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
            )
        )
        return summary_path


def _compute_result_hash(result: BlindGuessResult) -> str:
    """Compute a deterministic hash of the result."""
    return hashlib.sha256(
        json.dumps(
            {
                "company_id": result.company_id,
                "passed": result.passed,
                "provider_name": result.provider_name,
                "parse_error": result.parse_error,
                "provider_error": result.provider_error,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:16]
