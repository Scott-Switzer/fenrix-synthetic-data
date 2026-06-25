"""LLM provider interface for blind-guess adversarial review.

Provider-neutral Protocol with implementations:

- ``offline_stub``: deterministic, no network, configurable pass/fail
- ``openai_compatible``: works with NVIDIA NIM, OpenRouter, local servers
- ``local_ollama``: optional Ollama provider

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
    """

    confidence: float = 0.20
    most_likely_company: str | None = None
    most_likely_ticker: str | None = None
    top_candidates: list[dict[str, Any]] = field(default_factory=list)
    evidence_summary: str = "Insufficient evidence for company-level identification."
    refusal_or_uncertain: bool = True
    should_fail: bool = False
    fail_reason: str = ""

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
            Configured response dict.

        Raises:
            LLMProviderError: If configured to simulate a provider error.
        """
        if self._config.should_fail:
            raise LLMProviderError(self._config.fail_reason)

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

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _BLIND_REVIEW_SYSTEM_PROMPT},
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
