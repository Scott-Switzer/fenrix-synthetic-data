"""LLM provider interface for blind-guess and decoy-aware adversarial review.

Provider-neutral Protocol with implementations:

- ``offline_stub``: deterministic, no network, configurable pass/fail
- ``openai_compatible``: works with NVIDIA NIM, OpenRouter, local servers
- ``local_ollama``: optional Ollama provider

V3.1: Added ``_build_decoy_aware_review_prompt()`` for constrained
multiple-choice adversarial review with opaque candidate labels.

No API keys are hardcoded. No live provider is required for CI.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ── JSON extraction helpers ───────────────────────────────────────────────

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from model output using multiple strategies."""
    # Strategy 1: Direct parse
    try:
        result: dict[str, Any] = json.loads(text.strip())
        return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from ```json ... ``` fence
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            result = json.loads(m.group(1).strip())
            return result
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find first {...} with required keys
    for _key in ("confidence", "most_likely_company"):
        m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
                return result
            except json.JSONDecodeError:
                pass

    return None


# ── Provider Protocol ─────────────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-neutral interface for LLM completion.

    All providers must implement ``complete_json`` which returns a
    parsed JSON dict or raises an exception.
    """

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def complete_json(self, prompt: str, *, timeout_s: int) -> dict[str, Any]:
        """Send a prompt and return parsed JSON response.

        Args:
            prompt: The full prompt to send.
            timeout_s: Maximum time in seconds to wait.

        Returns:
            Parsed JSON dict from the model response.

        Raises:
            LLMProviderError: If the call fails or response is malformed.
        """
        ...


class LLMProviderError(Exception):
    """Raised when an LLM provider fails to produce a valid response."""


# ── Offline stub provider ─────────────────────────────────────────────────


@dataclass
class StubConfig:
    """Configuration for the offline stub provider.

    Allows deterministic simulation of different review outcomes
    for testing without network calls.

    If ``decoy_response`` is set, ``OfflineStubProvider`` returns it
    directly for decoy-aware review testing.
    """

    confidence: float = 0.20
    most_likely_company: str | None = None
    most_likely_ticker: str | None = None
    top_candidates: list[dict[str, Any]] = field(default_factory=list)
    evidence_summary: str = "Insufficient evidence for company-level identification."
    refusal_or_uncertain: bool = True
    should_fail: bool = False
    fail_reason: str = ""
    decoy_response: dict[str, Any] | None = None

    @classmethod
    def pass_case(cls) -> StubConfig:
        """Create a config that simulates a passing (low confidence) review."""
        return cls(
            confidence=0.15,
            most_likely_company=None,
            most_likely_ticker=None,
            top_candidates=[
                {
                    "company": "Generic Financial Corp",
                    "ticker": "GFC",
                    "confidence": "low",
                    "evidence": ["broad sector only"],
                },
                {
                    "company": "Regional Bank Group",
                    "ticker": "RBG",
                    "confidence": "low",
                    "evidence": ["similar industry classification"],
                },
            ],
            evidence_summary="Insufficient evidence for a company-level identification.",
            refusal_or_uncertain=True,
        )

    @classmethod
    def exact_top1_hit(cls) -> StubConfig:
        """Simulate a model that correctly identifies the source."""
        return cls(
            confidence=0.85,
            most_likely_company="Canary Holdings Corporation",
            most_likely_ticker="CHC",
            top_candidates=[
                {
                    "company": "Canary Holdings Corporation",
                    "ticker": "CHC",
                    "confidence": "high",
                    "evidence": ["distinctive financial patterns match exactly"],
                },
            ],
            evidence_summary="Company identified with high confidence from financial patterns.",
            refusal_or_uncertain=False,
        )

    @classmethod
    def top3_hit(cls) -> StubConfig:
        """Simulate a model that has the source in its top 3."""
        return cls(
            confidence=0.55,
            most_likely_company="First Regional Bank",
            most_likely_ticker="FRB",
            top_candidates=[
                {
                    "company": "First Regional Bank",
                    "ticker": "FRB",
                    "confidence": "medium",
                    "evidence": ["similar revenue profile"],
                },
                {
                    "company": "Midwest Financial Inc",
                    "ticker": "MWF",
                    "confidence": "medium",
                    "evidence": ["matching asset structure"],
                },
                {
                    "company": "Canary Holdings Corporation",
                    "ticker": "CHC",
                    "confidence": "medium",
                    "evidence": ["business model overlap"],
                },
            ],
            evidence_summary="Several candidates have overlapping characteristics.",
            refusal_or_uncertain=False,
        )

    @classmethod
    def high_confidence(cls) -> StubConfig:
        """Simulate a model with high confidence (even if wrong)."""
        return cls(
            confidence=0.92,
            most_likely_company="Wrong Guess Corp",
            most_likely_ticker="WGC",
            top_candidates=[
                {
                    "company": "Wrong Guess Corp",
                    "ticker": "WGC",
                    "confidence": "high",
                    "evidence": ["unique business model pattern"],
                },
            ],
            evidence_summary="High confidence in identification based on distinctive features.",
            refusal_or_uncertain=False,
        )

    @classmethod
    def medium_with_actual(cls) -> StubConfig:
        """Simulate medium confidence with actual source in candidates."""
        return cls(
            confidence=0.48,
            most_likely_company="Regional Bank Corp",
            most_likely_ticker="RBC",
            top_candidates=[
                {
                    "company": "Regional Bank Corp",
                    "ticker": "RBC",
                    "confidence": "medium",
                    "evidence": ["revenue structure similarity"],
                },
                {
                    "company": "Canary Holdings Corporation",
                    "ticker": "CHC",
                    "confidence": "medium",
                    "evidence": ["asset composition match"],
                },
            ],
            evidence_summary="Moderate confidence with several plausible candidates.",
            refusal_or_uncertain=False,
        )

    @classmethod
    def medium_without_actual(cls) -> StubConfig:
        """Simulate medium confidence without actual source."""
        return cls(
            confidence=0.45,
            most_likely_company="Diversified Holdings Inc",
            most_likely_ticker="DHI",
            top_candidates=[
                {
                    "company": "Diversified Holdings Inc",
                    "ticker": "DHI",
                    "confidence": "medium",
                    "evidence": ["industry and size match"],
                },
                {
                    "company": "Financial Services Group",
                    "ticker": "FSG",
                    "confidence": "medium",
                    "evidence": ["similar market position"],
                },
            ],
            evidence_summary="Moderate confidence, likely wrong sector.",
            refusal_or_uncertain=False,
        )

    @classmethod
    def decoy_pass_low_confidence(cls) -> StubConfig:
        """Simulate decoy-aware review: PASS — wrong guess, low confidence."""
        return cls(
            confidence=0.15,
            decoy_response={
                "anonymized_company_id": "COMPANY_001",
                "top_guess_label": "Candidate D",
                "top_guess_confidence": "low",
                "top_3_labels": ["Candidate D", "Candidate B", "Candidate A"],
                "evidence": [
                    {"claim": "Broad sector characteristics are consistent but not diagnostic.", "basis": "business_model"}
                ],
                "inference_basis": "business_model",
                "would_identify_exact_source": False,
                "direct_leak_detected": False,
                "notes": "Insufficient evidence to distinguish from peers.",
            },
        )

    @classmethod
    def decoy_pass_wrong_guess_medium(cls) -> StubConfig:
        """Simulate decoy-aware review: PASS — wrong guess, medium confidence."""
        return cls(
            confidence=0.45,
            decoy_response={
                "anonymized_company_id": "COMPANY_001",
                "top_guess_label": "Candidate C",
                "top_guess_confidence": "medium",
                "top_3_labels": ["Candidate C", "Candidate E", "Candidate B"],
                "evidence": [
                    {"claim": "Financial patterns resemble the candidate's sector.", "basis": "financial_pattern"}
                ],
                "inference_basis": "financial_pattern",
                "would_identify_exact_source": False,
                "direct_leak_detected": False,
                "notes": "Moderate confidence but guessed wrong candidate.",
            },
        )

    @classmethod
    def decoy_fail_top1_high_confidence(cls) -> StubConfig:
        """Simulate decoy-aware review: FAIL — correct top-1, high confidence."""
        return cls(
            confidence=0.85,
            decoy_response={
                "anonymized_company_id": "COMPANY_001",
                "top_guess_label": "Candidate A",
                "top_guess_confidence": "high",
                "top_3_labels": ["Candidate A", "Candidate D", "Candidate B"],
                "evidence": [
                    {"claim": "Revenue structure and margin profile are uniquely identifying.", "basis": "financial_pattern"},
                    {"claim": "Geographic segment split matches exactly.", "basis": "business_model"},
                ],
                "inference_basis": "financial_pattern",
                "would_identify_exact_source": True,
                "direct_leak_detected": False,
                "notes": "Confident identification based on distinctive financial patterns.",
            },
        )

    @classmethod
    def decoy_fail_direct_leak(cls) -> StubConfig:
        """Simulate decoy-aware review: FAIL — evidence includes direct identifiers."""
        return cls(
            confidence=0.55,
            decoy_response={
                "anonymized_company_id": "COMPANY_001",
                "top_guess_label": "Candidate A",
                "top_guess_confidence": "medium",
                "top_3_labels": ["Candidate A", "Candidate B", "Candidate D"],
                "evidence": [
                    {"claim": "Product launch timeline matches exactly.", "basis": "product_event_fingerprint"},
                    {"claim": "Revenue matches known public figure.", "basis": "exact_number"},
                ],
                "inference_basis": "product_event_fingerprint",
                "would_identify_exact_source": True,
                "direct_leak_detected": True,
                "notes": "Direct identifier evidence found.",
            },
        )

    @classmethod
    def decoy_warn_business_model(cls) -> StubConfig:
        """Simulate decoy-aware review: WARN — true source top-3, low confidence, business model only."""
        return cls(
            confidence=0.25,
            decoy_response={
                "anonymized_company_id": "COMPANY_001",
                "top_guess_label": "Candidate B",
                "top_guess_confidence": "low",
                "top_3_labels": ["Candidate B", "Candidate A", "Candidate D"],
                "evidence": [
                    {"claim": "Business model and sector are consistent.", "basis": "business_model"}
                ],
                "inference_basis": "business_model",
                "would_identify_exact_source": False,
                "direct_leak_detected": False,
                "notes": "True source in top-3 but only through broad business-model inference.",
            },
        )

    @classmethod
    def malformed_output(cls) -> StubConfig:
        """Simulate a provider that returns malformed output."""
        return cls(should_fail=True, fail_reason="Malformed output")

    @classmethod
    def provider_error(cls) -> StubConfig:
        """Simulate a provider error."""
        return cls(should_fail=True, fail_reason="Provider error")


class OfflineStubProvider:
    """Deterministic offline stub for CI and testing.

    Returns configured responses without any network calls.
    """

    def __init__(self, config: StubConfig | None = None) -> None:
        self._config = config or StubConfig.pass_case()

    @property
    def provider_name(self) -> str:
        return "offline_stub"

    @property
    def model_name(self) -> str:
        return "offline-stub-v1"

    def configure(self, config: StubConfig) -> None:
        """Set the stub configuration for deterministic testing."""
        self._config = config

    def complete_json(self, prompt: str, *, timeout_s: int = 30) -> dict[str, Any]:
        """Return configured stub response.

        Args:
            prompt: Ignored by stub.
            timeout_s: Ignored by stub.

        Returns:
            Configured response dict — blind-review or decoy-aware format.

        Raises:
            LLMProviderError: If configured to simulate a provider error.
        """
        if self._config.should_fail:
            raise LLMProviderError(self._config.fail_reason)

        # Decoy-aware mode: return the decoy_response if configured.
        if self._config.decoy_response is not None:
            return self._config.decoy_response

        return {
            "anonymized_company_id": "COMPANY_001",
            "most_likely_company": self._config.most_likely_company,
            "most_likely_ticker": self._config.most_likely_ticker,
            "confidence": _confidence_label(self._config.confidence),
            "top_candidates": self._config.top_candidates,
            "evidence_summary": self._config.evidence_summary,
            "refusal_or_uncertain": self._config.refusal_or_uncertain,
        }


def _confidence_label(score: float) -> str:
    """Map numeric confidence to label."""
    if score >= 0.70:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


# ── OpenAI-compatible provider ────────────────────────────────────────────


_BLIND_REVIEW_SYSTEM_PROMPT = """\
You are reviewing an anonymized public-company packet for a finance class.

Your task is to determine if you can identify the real company behind the
anonymized data. You must be honest about your confidence level.

Rules:
- You must return ONLY valid JSON — no explanation outside the JSON object.
- If the evidence is insufficient, say no justified company-level guess.
- Do not fabricate evidence or guess randomly.
- Citation patterns in the data (like "Company 001") are anonymized placeholders
  and should NOT be treated as clues."""

_DECOY_SYSTEM_PROMPT = """\
You are an adversarial privacy reviewer evaluating an anonymized company packet.

You will be given:
1. An anonymized company profile with financial, market, SEC, and news summaries.
2. A candidate set of 5 possible real public companies, labeled only as
   Candidate A, Candidate B, Candidate C, Candidate D, Candidate E.

Your task is to determine which candidate (if any) is the true source company
behind the anonymized packet. You must also identify any evidence that would
enable exact identification.

Rules:
- You must return ONLY valid JSON — no explanation outside the JSON object.
- One of the candidates IS the true source; the other 4 are sector/scale peers.
- If you cannot distinguish the true source from peers with confidence, say so.
- Evaluate: business model fit, financial pattern match, product/event
  fingerprints, any direct identifiers or exact numbers that survive
  anonymization, and any metadata leaks (tickers, CIKs, locations, executives).
- Be honest about your confidence level."""


def _build_blind_review_prompt(public_content: str, company_id: str) -> str:
    """Build the blind review prompt from public bundle content."""
    # Truncate to avoid token limits
    max_chars = 50_000
    if len(public_content) > max_chars:
        public_content = (
            public_content[: int(max_chars * 0.6)]
            + "\n\n... [content truncated] ...\n\n"
            + public_content[-int(max_chars * 0.4) :]
        )

    return f"""\
You are reviewing an anonymized public-company packet for a finance class.

Your task:
1. Identify the most likely real public company, if possible.
2. Provide ticker if you believe one is justified.
3. Give confidence: none, low, medium, high.
4. Provide top 5 candidate companies/tickers.
5. Explain evidence.
6. If the evidence is insufficient, say no justified company-level guess.

Return strict JSON only.

The anonymized company ID is: {company_id}

--- PUBLIC COMPANY PACKET ---
{public_content}
--- END PACKET ---

Return a JSON object with these exact keys:
{{
  "anonymized_company_id": "{company_id}",
  "most_likely_company": null,
  "most_likely_ticker": null,
  "confidence": "low",
  "top_candidates": [
    {{"company": "Example Corp", "ticker": "EXM", "confidence": "low", "evidence": ["broad sector only"]}}
  ],
  "evidence_summary": "Insufficient evidence for a company-level identification.",
  "refusal_or_uncertain": true
}}
"""


def _build_decoy_aware_review_prompt(
    public_content: str,
    company_id: str,
    candidate_labels: list[str],
) -> str:
    """Build a decoy-aware review prompt with opaque candidate labels.

    The prompt presents the anonymized content alongside a constrained
    candidate set (Candidate A/B/C/D/E). Real company names are NEVER
    included in the prompt — only opaque labels.

    Args:
        public_content: Aggregated public bundle content.
        company_id: The anonymized company ID.
        candidate_labels: List of 5 opaque labels e.g.
            ``["Candidate A", "Candidate B", ..., "Candidate E"]``.

    Returns:
        Full prompt string for the LLM.
    """
    max_chars = 50_000
    if len(public_content) > max_chars:
        public_content = (
            public_content[: int(max_chars * 0.6)]
            + "\n\n... [content truncated] ...\n\n"
            + public_content[-int(max_chars * 0.4) :]
        )

    candidate_block = "\n".join(f"- {label}" for label in candidate_labels)

    return f"""\
You are an adversarial privacy reviewer evaluating an anonymized company packet.

You are given:
1. An anonymized public-company packet (profile, financials, market data,
   SEC summaries, and synthetic news).
2. A candidate set of 5 real public companies, labeled only as:

{candidate_block}

One of these candidates is the TRUE source company behind the anonymized packet.
The other 4 are sector/scale peers that are NOT the source.

Your task:
1. Determine which candidate (if any) you believe is most likely the true source.
2. Choose top 3 candidates in order of likelihood.
3. Provide your confidence: low, medium, or high.
4. List the evidence that informed your ranking.
5. Classify each piece of evidence by inference basis.
6. State whether you would identify an exact source company from the evidence alone.

Return strict JSON only. Do NOT include real company names in your output —
use only the opaque labels (Candidate A, Candidate B, etc.).

The anonymized company ID is: {company_id}

--- PUBLIC COMPANY PACKET ---
{public_content}
--- END PACKET ---

Return a JSON object with these exact keys:
{{
  "anonymized_company_id": "{company_id}",
  "top_guess_label": "Candidate B",
  "top_guess_confidence": "medium",
  "top_3_labels": ["Candidate B", "Candidate D", "Candidate A"],
  "evidence": [
    {{
      "claim": "The revenue structure and margin profile are consistent with a consumer staples manufacturer of this scale.",
      "basis": "business_model",
      "criteria_type": "financial_pattern"
    }},
    {{
      "claim": "The geographic revenue split matches the candidate's disclosed segment reporting pattern.",
      "basis": "financial_pattern",
      "criteria_type": "segment_geography"
    }}
  ],
  "inference_basis": "business_model",
  "would_identify_exact_source": false,
  "direct_leak_detected": false,
  "notes": "Sector-level evidence only — cannot distinguish from peers with high confidence."
}}

Evidence basis must be one of:
- business_model
- financial_pattern
- product_event_fingerprint
- direct_identifier
- exact_number
- metadata_leak
- sector_only
- unknown

Confidence must be one of: low, medium, high.
"""


class OpenAICompatibleProvider:
    """LLM provider using OpenAI-compatible chat completions API.

    Works with:
    - NVIDIA NIM / NVIDIA API catalog
    - OpenRouter
    - Local OpenAI-compatible servers
    - Any service with a /v1/chat/completions endpoint

    Configuration is drawn from constructor args or environment variables.
    The API key is never printed, logged, or persisted.

    HTTP 429 retry behaviour:
    - Honors ``Retry-After`` header if present and reasonable (≤ ``max_delay``).
    - Otherwise uses bounded exponential backoff with jitter.
    - ``_sleeper`` is injectable for deterministic tests (set to a callable
      accepting seconds).
    """

    #: Default retry constants — kept at module level so tests can reference them.
    RETRY_DEFAULT_MAX_ATTEMPTS: int = 4
    RETRY_DEFAULT_INITIAL_DELAY: float = 20.0
    RETRY_DEFAULT_MAX_DELAY: float = 180.0
    RETRY_DEFAULT_JITTER: float = 5.0

    def __init__(
        self,
        base_url: str | None = None,
        api_key_env: str = "NVIDIA_API_KEY",
        model: str = "meta/llama-3.1-70b-instruct",
        timeout: float = 60.0,
        max_retries: int = RETRY_DEFAULT_MAX_ATTEMPTS,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        retry_initial_delay: float = RETRY_DEFAULT_INITIAL_DELAY,
        retry_max_delay: float = RETRY_DEFAULT_MAX_DELAY,
        retry_jitter: float = RETRY_DEFAULT_JITTER,
    ) -> None:
        self._base_url = (base_url or "https://integrate.api.nvidia.com/v1").rstrip("/")
        self._api_key = os.environ.get(api_key_env, "")
        self._api_key_env = api_key_env
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._retry_initial_delay = retry_initial_delay
        self._retry_max_delay = retry_max_delay
        self._retry_jitter = retry_jitter
        # Test-injectable sleeper (seconds → None).
        self._sleeper: Callable[[float], None] = time.sleep

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def complete_json(self, prompt: str, *, timeout_s: int = 60) -> dict[str, Any]:
        """Send prompt and return parsed JSON response.

        Retries on HTTP 429 with ``Retry-After`` header support and
        bounded exponential backoff + jitter.

        Args:
            prompt: The full prompt to send.
            timeout_s: Maximum time to wait per attempt.

        Returns:
            Parsed JSON dict.

        Raises:
            LLMProviderError: On any non-retryable failure or exhaustion.
        """
        if httpx is None:
            raise LLMProviderError("httpx package not available for LLM requests")
        if not self._api_key:
            raise LLMProviderError(
                f"API key not configured: set {self._api_key_env} environment variable"
            )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # Detect decoy-aware prompt by checking for candidate label markers.
        is_decoy = "Candidate A" in prompt and "top_guess_label" in prompt
        system_prompt = _DECOY_SYSTEM_PROMPT if is_decoy else _BLIND_REVIEW_SYSTEM_PROMPT

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }

        last_error: str | None = None
        max_attempts = max(1, self._max_retries + 1)

        for attempt in range(max_attempts):
            try:
                resp = httpx.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=min(timeout_s, self._timeout),
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                content: str = data["choices"][0]["message"]["content"]
                parsed = _extract_json(content)
                if parsed is None:
                    raise LLMProviderError(
                        f"Failed to parse JSON from model response: {content[:200]}..."
                    )
                return parsed

            except httpx.HTTPStatusError as exc:
                body_snippet = ""
                try:
                    body_snippet = exc.response.text[:300]
                except Exception:
                    pass
                last_error = f"HTTP {exc.response.status_code}: {body_snippet}"
                if exc.response.status_code in (401, 403):
                    raise LLMProviderError(f"Authentication failed: {last_error}") from exc
                if exc.response.status_code == 429 and attempt + 1 < max_attempts:
                    delay = self._compute_429_delay(exc.response.headers)
                    logger.warning(
                        "LLM provider HTTP 429 on attempt %d/%d; sleeping %.1fs",
                        attempt + 1,
                        max_attempts,
                        delay,
                    )
                    self._sleeper(delay)
                    continue

            except httpx.TimeoutException:
                last_error = "timeout"
                if attempt + 1 < max_attempts:
                    delay = self._compute_backoff_delay(attempt)
                    self._sleeper(delay)
                    continue

            except LLMProviderError:
                raise

            except Exception as exc:
                last_error = str(exc)
                if attempt + 1 < max_attempts:
                    delay = self._compute_backoff_delay(attempt)
                    self._sleeper(delay)
                    continue

        raise LLMProviderError(
            f"LLM request failed after {max_attempts} attempts: {last_error}"
        ) from None

    def _compute_429_delay(self, response_headers: Any | None) -> float:
        """Compute delay for an HTTP 429 response.

        Honors ``Retry-After`` header if present and reasonable (≤ max_delay).
        Falls back to bounded exponential backoff with jitter.
        """
        if response_headers is not None:
            try:
                retry_after = response_headers.get("Retry-After") or response_headers.get("retry-after")
                if retry_after is not None:
                    try:
                        val = float(str(retry_after))
                        if 0 < val <= self._retry_max_delay:
                            return val
                    except (ValueError, TypeError):
                        pass
            except AttributeError:
                pass
        return self._compute_backoff_delay(0)

    def _compute_backoff_delay(self, attempt: int, /) -> float:
        """Compute bounded exponential backoff delay with jitter."""
        delay: float = min(self._retry_max_delay, self._retry_initial_delay * (2 ** attempt))
        jitter: float = random.uniform(0, self._retry_jitter)
        return delay + jitter

    def __repr__(self) -> str:
        return (
            f"OpenAICompatibleProvider(model={self._model!r}, "
            f"base_url={self._base_url!r}, configured={self.is_configured})"
        )


# ── Provider factory ─────────────────────────────────────────────────────


def create_llm_provider(
    provider_type: str,
    config: dict[str, Any] | None = None,
) -> LLMProvider:
    """Create an LLM provider from type and configuration.

    Args:
        provider_type: One of "offline_stub", "openai_compatible", "local_ollama".
        config: Provider-specific configuration dict.

    Returns:
        An LLMProvider instance.

    Raises:
        ValueError: If provider_type is unknown.
    """
    cfg = config or {}

    if provider_type == "offline_stub":
        stub_config = StubConfig.pass_case()
        if cfg.get("stub_mode") == "fail_top1":
            stub_config = StubConfig.exact_top1_hit()
        elif cfg.get("stub_mode") == "fail_top3":
            stub_config = StubConfig.top3_hit()
        elif cfg.get("stub_mode") == "fail_high_confidence":
            stub_config = StubConfig.high_confidence()
        elif cfg.get("stub_mode") == "fail_medium_with_actual":
            stub_config = StubConfig.medium_with_actual()
        elif cfg.get("stub_mode") == "warn_medium_without_actual":
            stub_config = StubConfig.medium_without_actual()
        elif cfg.get("stub_mode") == "error_malformed":
            stub_config = StubConfig.malformed_output()
        elif cfg.get("stub_mode") == "error_provider":
            stub_config = StubConfig.provider_error()
        return OfflineStubProvider(stub_config)

    if provider_type == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=cfg.get("base_url"),
            api_key_env=cfg.get("api_key_env", "NVIDIA_API_KEY"),
            model=cfg.get("model", "meta/llama-3.1-70b-instruct"),
            timeout=cfg.get("timeout", 60.0),
            max_retries=cfg.get("max_retries", 4),
            retry_initial_delay=cfg.get("retry_initial_delay_s", 20.0),
            retry_max_delay=cfg.get("retry_max_delay_s", 180.0),
            retry_jitter=cfg.get("retry_jitter_s", 5.0),
        )

    if provider_type == "local_ollama":
        # Ollama uses the same OpenAI-compatible interface on localhost
        return OpenAICompatibleProvider(
            base_url=cfg.get("base_url", "http://localhost:11434/v1"),
            api_key_env=cfg.get("api_key_env", "OLLAMA_API_KEY"),
            model=cfg.get("model", "llama3.2"),
            timeout=cfg.get("timeout", 120.0),
            max_retries=cfg.get("max_retries", 1),
        )

    raise ValueError(f"Unknown LLM provider type: {provider_type}")
