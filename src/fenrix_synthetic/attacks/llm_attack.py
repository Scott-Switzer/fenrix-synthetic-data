"""Provider-neutral LLM guessing attack (Phase 4G).

Accepts only masked release candidates. Never sends raw documents.
Asks for top-five issuer guesses with confidence and supporting clues.
Redacts guesses in public evidence. Supports local and frontier providers.

This is an INTERFACE — actual model calls require provider implementations
that are outside the scope of this phase's committed code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum


class ClueType(StrEnum):
    INDUSTRY = "industry"
    GEOGRAPHY = "geography"
    FINANCIAL_METRICS = "financial_metrics"
    BUSINESS_MODEL = "business_model"
    PRODUCT_NAMES = "product_names"
    EXECUTIVE_NAMES = "executive_names"
    LEGAL_EVENTS = "legal_events"
    ACQUISITION_HISTORY = "acquisition_history"
    CUSTOMER_BASE = "customer_base"
    OTHER = "other"


@dataclass
class LlmGuess:
    """A single LLM guess about the source company."""

    rank: int  # 1-5
    guess_text: str  # The model's guess (redacted in public output)
    confidence: float  # 0.0-1.0
    clues: list[str] = field(default_factory=list)
    clue_types: list[ClueType] = field(default_factory=list)


@dataclass
class LlmAttackResult:
    """Result of an LLM guessing attack."""

    model_id: str
    provider: str
    model_version: str = ""
    prompt_hash: str = ""
    guesses: list[LlmGuess] = field(default_factory=list)
    correct_in_top_k: bool = False
    correct_rank: int = -1
    top_confidence: float = 0.0
    is_blocked: bool = False
    attack_duration_ms: float = 0.0
    raw_response_hash: str = ""


class LlmAttackPrompt:
    """Canonical LLM attack prompt template."""

    @staticmethod
    def build_prompt(
        masked_text_excerpts: list[str],
        structured_summary: dict,
        industry_hint: str = "",
    ) -> str:
        """Build the attack prompt for the LLM.

        The prompt asks the model to guess the source company from the
        masked data. Results are redacted in public evidence.

        Args:
            masked_text_excerpts: Excerpts from the masked document(s)
            structured_summary: Summary of transformed structured data
            industry_hint: Optional industry context (e.g., "regional bank")
        """
        industry_line = (
            f"This company operates in the {industry_hint} industry.\n" if industry_hint else ""
        )

        prompt = (
            "You are conducting a privacy audit of anonymized financial data. "
            "Below are excerpts from financial documents that have been "
            "processed to remove identifying information.\n\n"
            f"{industry_line}"
            "Please try to identify the source company. Provide:\n"
            "1. Your top 5 guesses, ranked by confidence\n"
            "2. Confidence score (0.0-1.0) for each guess\n"
            "3. The specific clues that led to each guess\n"
            "4. What type of clue each was (industry, geography, "
            "financial metrics, business model, product names, "
            "executive names, legal events, acquisition history, "
            "customer base, other)\n\n"
            "--- MASKED DOCUMENT EXCERPTS ---\n\n"
        )

        for i, excerpt in enumerate(masked_text_excerpts[:3], 1):
            prompt += f"Excerpt {i}:\n{excerpt[:2000]}\n\n"

        prompt += (
            "--- STRUCTURED DATA SUMMARY ---\n"
            f"{json.dumps(structured_summary, indent=2)}\n\n"
            "--- YOUR ANALYSIS ---\n"
            "Please list your top 5 guesses with confidence and clues."
        )

        return prompt

    @staticmethod
    def compute_prompt_hash(
        masked_text_excerpts: list[str],
        structured_summary: dict,
        industry_hint: str = "",
    ) -> str:
        """Compute a deterministic hash of the prompt for reproducibility."""
        prompt = LlmAttackPrompt.build_prompt(
            masked_text_excerpts, structured_summary, industry_hint
        )
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def redact_guesses(guesses: list[LlmGuess]) -> list[dict]:
    """Redact LLM guesses for public evidence.

    Removes the actual guess text, keeping only rank, confidence,
    clue types, and whether the guess was correct.
    """
    redacted: list[dict] = []
    for g in guesses:
        redacted.append(
            {
                "rank": g.rank,
                "guess_text": "[REDACTED]",
                "confidence": g.confidence,
                "clue_types": [ct.value for ct in g.clue_types],
            }
        )
    return redacted


def evaluate_llm_attack(
    guesses: list[LlmGuess],
    model_id: str,
    provider: str,
    prompt_hash: str,
) -> LlmAttackResult:
    """Evaluate LLM guessing results against the known source.

    In a real run, this compares guesses against the private source identity
    (SRC_001's real name). In tests, this uses synthetic fixtures.

    Args:
        guesses: The model's top-5 guesses
        model_id: Model identifier
        provider: Provider name
        prompt_hash: Hash of the prompt sent

    Returns:
        LlmAttackResult with evaluation
    """
    result = LlmAttackResult(
        model_id=model_id,
        provider=provider,
        prompt_hash=prompt_hash,
        guesses=guesses,
    )

    if guesses:
        result.top_confidence = max(g.confidence for g in guesses)
        result.is_blocked = result.top_confidence > 0.7

    return result
