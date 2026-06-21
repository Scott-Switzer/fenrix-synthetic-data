"""NVIDIA NIM client — OpenAI-compatible chat-completions API.

Implements the 3-pass NVIDIA adversarial review:

1. **Attacker pass** — can the model identify the company?
2. **Rewrite pass** — generalize leaked clues while preserving utility.
3. **Re-attack pass** — after rewrite, can the model still identify?

The client uses the standard ``POST /v1/chat/completions`` endpoint
exposed by NVIDIA NIM.  Configuration is drawn from environment
variables and never written to logs, manifests, or artifacts.

Environment variables
---------------------
``NVIDIA_API_KEY``
    Required.  Never printed, logged, or persisted.
``NVIDIA_BASE_URL``
    Base URL for the chat-completions endpoint.
    Default: ``https://integrate.api.nvidia.com/v1``
``NVIDIA_MODEL``
    Model identifier.  Default: ``nvidia/llama-3.1-nemotron-70b-instruct``

Gate rules
----------
* PASS    — confidence < 0.35  AND  no direct identifiers remain.
* REVIEW  — confidence 0.35–0.60  OR  clues are vague.
* FAIL    — confidence > 0.60  OR  correct company guess
            OR  direct identifiers remain.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "meta/llama-3.1-70b-instruct"
_DEFAULT_MAX_TOKENS = 2048
_DEFAULT_TIMEOUT = 120.0
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_MAX_CHARS = 100_000

_ATTACKER_SYSTEM_PROMPT = """\
You are a privacy auditor reviewing anonymized financial documents.
Your task is to determine whether the document still reveals the
identity of the company it describes.

Analyze the text for:
- direct identifiers (ticker, CIK, legal name, domain)
- semantic phrases (distinctive product names, brand language)
- business model clues (unique revenue structure, market position)
- numeric fingerprints (unusual financial ratios, margin patterns)
- filing structure (recognizable SEC patterns)
- news events (specific dates, acquisitions, product launches)

Return ONLY valid JSON — no explanation outside the JSON object."""

_ATTACKER_USER_PROMPT = """\
Review the following anonymized financial document and determine
if you can identify the company.

Return a JSON object with these exact keys:

{
  "guessed_company": "string or null if you cannot identify",
  "confidence": 0.0 to 1.0,
  "leaked_clues": ["specific phrases or patterns that gave it away"],
  "evidence_type": ["one or more of: direct_identifier, semantic_phrase, business_model, numeric_fingerprint, filing_structure, news_event"],
  "needs_rewrite": true if clues should be removed,
  "short_explanation": "one-sentence summary"
}

Document:
---
%s
---"""

_REWRITE_SYSTEM_PROMPT = """\
You are a privacy-preserving document editor. Rewrite the anonymized
financial document to remove identifying clues while preserving:

- financial meaning and numeric relationships
- document structure and section hierarchy
- educational utility for finance students
- timeline relationships using relative periods
- coarse financial direction/regime information

Rules:
- Do NOT invent new facts or financial figures.
- Do NOT add company-specific details.
- Do NOT restore real names, tickers, CIKs, domains, products,
  executives, or locations.
- Generalize distinctive issuer-specific clues to generic descriptions.
- Preserve the overall document length and paragraph structure."""

_REWRITE_USER_PROMPT = """\
Rewrite the following document to remove these leaked clues:

Leaked clues: %s

Rewrite the full document below. Preserve section structure and
financial meaning. Generalize only the identifying details.

Document:
---
%s
---"""

# ── JSON extraction helpers ───────────────────────────────────────────

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

    # Strategy 3: Find first {...} object (constrained to single object)
    m = re.search(r'\{[^{}]*"guessed_company"[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            return result
        except json.JSONDecodeError:
            pass

    return None


# ── Result dataclasses ─────────────────────────────────────────────────


@dataclass
class AttackerResult:
    """Result of the NVIDIA attacker pass."""

    guessed_company: str | None = None
    confidence: float = 0.0
    leaked_clues: list[str] = field(default_factory=list)
    evidence_type: list[str] = field(default_factory=list)
    needs_rewrite: bool = False
    short_explanation: str = ""
    parse_error: bool = False
    raw_response: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttackerResult:
        """Parse and validate attacker response dict."""
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        guessed = data.get("guessed_company")
        if guessed is not None and not isinstance(guessed, str):
            guessed = str(guessed)
        if guessed == "" or guessed == "null":
            guessed = None

        clues = data.get("leaked_clues", [])
        if not isinstance(clues, list):
            clues = []

        evidence = data.get("evidence_type", [])
        if not isinstance(evidence, list):
            evidence = []

        valid_evidence = [
            "direct_identifier",
            "semantic_phrase",
            "business_model",
            "numeric_fingerprint",
            "filing_structure",
            "news_event",
        ]
        evidence = [e for e in evidence if e in valid_evidence]

        needs_rewrite = bool(data.get("needs_rewrite", False))

        return cls(
            guessed_company=guessed,
            confidence=confidence,
            leaked_clues=clues,
            evidence_type=evidence,
            needs_rewrite=needs_rewrite,
            short_explanation=str(data.get("short_explanation", "")),
        )

    def has_direct_identifiers(self) -> bool:
        """Check if evidence includes direct identifiers."""
        return "direct_identifier" in self.evidence_type

    def to_sanitized_dict(self) -> dict[str, Any]:
        """Return a sanitized dict safe for public artifacts."""
        return {
            "guessed_company": self.guessed_company,
            "confidence": self.confidence,
            "leaked_clues": self.leaked_clues,
            "evidence_type": self.evidence_type,
            "needs_rewrite": self.needs_rewrite,
            "short_explanation": self.short_explanation,
            "parse_error": self.parse_error,
        }


@dataclass
class NVIDIAGateVerdict:
    """Per-artifact NVIDIA gate verdict."""

    status: str  # PASS, REVIEW, FAIL
    attacker: AttackerResult | None = None
    rewritten_text: str = ""
    reattacker: AttackerResult | None = None
    error: str = ""

    @classmethod
    def evaluate(
        cls,
        attacker: AttackerResult,
        known_identifiers: list[str] | None = None,
    ) -> NVIDIAGateVerdict:
        """Evaluate attacker result against gate thresholds."""
        # Check for correct guess
        correct_guess = False
        if attacker.guessed_company and known_identifiers:
            guess_lower = attacker.guessed_company.lower()
            for ident in known_identifiers:
                if ident.lower() in guess_lower or guess_lower in ident.lower():
                    correct_guess = True
                    break

        # FAIL conditions
        if correct_guess:
            return cls(
                status="FAIL", attacker=attacker, error=f"Correct guess: {attacker.guessed_company}"
            )

        if attacker.has_direct_identifiers():
            return cls(status="FAIL", attacker=attacker, error="Direct identifiers remain")

        if attacker.confidence > 0.60:
            return cls(
                status="FAIL",
                attacker=attacker,
                error=f"High confidence: {attacker.confidence:.2f}",
            )

        # REVIEW conditions
        if attacker.confidence >= 0.35 or attacker.leaked_clues:
            return cls(
                status="REVIEW", attacker=attacker, error="Vague clues or moderate confidence"
            )

        # PASS
        return cls(status="PASS", attacker=attacker)


@dataclass
class NVIDIABatchResult:
    """Aggregate result across all reviewed artifacts."""

    status: str = "NOT_RUN"
    artifacts_reviewed: int = 0
    pass_count: int = 0
    review_count: int = 0
    fail_count: int = 0
    rewrite_count: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def decision(self) -> str:
        if self.artifacts_reviewed == 0:
            return "NOT_RUN"
        if self.fail_count > 0:
            return "FAIL"
        if self.review_count > 0:
            return "REVIEW_REQUIRED"
        return "PASS"


# ── Client ─────────────────────────────────────────────────────────────


class NVIDIAClient:
    """OpenAI-compatible chat-completions client for NVIDIA NIM.

    Configuration is drawn from environment variables.  The API key
    is **never** printed, logged, serialised, or written to disk by
    this class.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = 0.0,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._api_key = api_key or os.environ.get("NVIDIA_API_KEY", "")
        self._base_url = (base_url or os.environ.get("NVIDIA_BASE_URL", _DEFAULT_BASE_URL)).rstrip(
            "/"
        )
        self._model = model or os.environ.get("NVIDIA_MODEL", _DEFAULT_MODEL)
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout
        self._max_retries = max_retries

    # ── Public API ──────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        """Smart truncation: head + tail to capture document structure."""
        if len(text) <= max_chars:
            return text
        head_size = int(max_chars * 0.6)
        tail_size = max_chars - head_size - 200
        return (
            text[:head_size] + "\n\n... [truncated for NVIDIA review] ...\n\n" + text[-tail_size:]
        )

    def attacker_pass(
        self,
        text: str,
        known_identifiers: list[str] | None = None,
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> AttackerResult:
        """Run the attacker pass — can the model identify the company?

        Documents larger than *max_chars* are smart-truncated
        (head 60 % + tail 40 %) before being sent to the model.

        Returns an ``AttackerResult`` with ``parse_error=True`` if
        the model response could not be parsed as valid JSON.
        """
        truncated = self._truncate_text(text, max_chars)
        user_prompt = _ATTACKER_USER_PROMPT % (truncated,)
        raw = self._chat(
            system=_ATTACKER_SYSTEM_PROMPT,
            user=user_prompt,
        )

        if raw is None:
            return AttackerResult(parse_error=True, raw_response="[no response]")

        parsed = _extract_json(raw)
        if parsed is None:
            return AttackerResult(parse_error=True, raw_response=raw)

        try:
            result = AttackerResult.from_dict(parsed)
            result.raw_response = raw
            return result
        except (ValueError, TypeError, KeyError):
            return AttackerResult(parse_error=True, raw_response=raw)

    def rewrite_pass(self, text: str, leaked_clues: list[str]) -> str:
        """Rewrite the document to remove leaked clues.

        Returns the rewritten text, or the original text if the
        rewrite fails.
        """
        clues_str = ", ".join(leaked_clues) if leaked_clues else "none specified"
        user_prompt = _REWRITE_USER_PROMPT % (clues_str, text)
        raw = self._chat(
            system=_REWRITE_SYSTEM_PROMPT,
            user=user_prompt,
        )
        # ── Strip markdown fences and preamble from rewrite output ──
        # Models often wrap responses in ``` fences or add preamble
        fence_m = _JSON_FENCE_RE.search(raw) if raw else None
        if fence_m:
            raw = fence_m.group(1).strip()
        # Strip common preambles
        for prefix in (
            "Here is the rewritten document:",
            "Rewritten document:",
            "Here is the rewritten text:",
            "Rewritten text:",
        ):
            if raw and raw.startswith(prefix):
                raw = raw[len(prefix) :].strip()
        return raw if raw else text

    def reattack_pass(
        self,
        rewritten_text: str,
        known_identifiers: list[str] | None = None,
    ) -> AttackerResult:
        """Re-run the attacker pass on rewritten text."""
        return self.attacker_pass(rewritten_text, known_identifiers)

    def review_artifact(
        self,
        text: str,
        artifact_id: str,
        known_identifiers: list[str] | None = None,
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> dict[str, Any]:
        """Run the full 3-pass review on a single artifact.

        Large documents are smart-truncated before the attacker pass.

        Returns a sanitized dict with attacker, rewrite, and re-attack
        results plus the per-artifact gate verdict.
        """
        result: dict[str, Any] = {
            "artifact_id": artifact_id,
            "text_length": len(text),
        }

        # Pass 1: Attacker
        attacker = self.attacker_pass(text, known_identifiers, max_chars=max_chars)
        result["attacker"] = attacker.to_sanitized_dict()

        if attacker.parse_error:
            result["gate_verdict"] = "FAIL"
            result["gate_error"] = "Attacker parse error"
            return result

        # Evaluate initial gate
        initial_verdict = NVIDIAGateVerdict.evaluate(attacker, known_identifiers)
        result["initial_verdict"] = initial_verdict.status

        if initial_verdict.status == "PASS":
            result["gate_verdict"] = "PASS"
            result["rewrite_applied"] = False
            return result

        # Pass 2: Rewrite (only if needed)
        if attacker.needs_rewrite and attacker.leaked_clues:
            rewritten = self.rewrite_pass(text, attacker.leaked_clues)
            result["rewrite_applied"] = True
            result["rewritten_length"] = len(rewritten)

            # Pass 3: Re-attack
            reattacker = self.reattack_pass(rewritten, known_identifiers)
            result["reattacker"] = reattacker.to_sanitized_dict()

            if reattacker.parse_error:
                result["gate_verdict"] = "FAIL"
                result["gate_error"] = "Re-attack parse error"
            else:
                final_verdict = NVIDIAGateVerdict.evaluate(reattacker, known_identifiers)
                result["gate_verdict"] = final_verdict.status
                if final_verdict.status == "FAIL":
                    result["gate_error"] = final_verdict.error
        else:
            result["rewrite_applied"] = False
            result["gate_verdict"] = initial_verdict.status
            if initial_verdict.status == "FAIL":
                result["gate_error"] = initial_verdict.error

        return result

    def review_batch(
        self,
        artifacts: list[tuple[str, str]],  # [(artifact_id, text), ...]
        known_identifiers: list[str] | None = None,
    ) -> NVIDIABatchResult:
        """Run the full 3-pass review on a batch of artifacts.

        ``artifacts`` is a list of ``(artifact_id, text)`` tuples.
        """
        batch = NVIDIABatchResult()

        for artifact_id, text in artifacts:
            if not text or not text.strip():
                continue

            try:
                result = self.review_artifact(text, artifact_id, known_identifiers)
                batch.results.append(result)
                batch.artifacts_reviewed += 1

                verdict = result.get("gate_verdict", "FAIL")
                if verdict == "PASS":
                    batch.pass_count += 1
                elif verdict == "REVIEW":
                    batch.review_count += 1
                else:
                    batch.fail_count += 1

                if result.get("rewrite_applied"):
                    batch.rewrite_count += 1

            except Exception as exc:
                logger.warning("NVIDIA review failed for %s: %s", artifact_id, exc)
                batch.errors.append(f"{artifact_id}: {exc}")
                batch.results.append(
                    {
                        "artifact_id": artifact_id,
                        "gate_verdict": "FAIL",
                        "gate_error": str(exc),
                    }
                )
                batch.fail_count += 1
                batch.artifacts_reviewed += 1

        batch.status = batch.decision
        return batch

    # ── Internal ────────────────────────────────────────────────────

    def _chat(
        self,
        system: str,
        user: str,
        response_format: dict[str, Any] | None = None,
    ) -> str | None:
        """Make a chat-completions request with retry logic.

        Returns the model's text response, or ``None`` on failure.
        Never raises — all errors are logged and return None.
        """
        if not self._api_key:
            logger.error("NVIDIA_API_KEY not configured")
            return None

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }

        if response_format:
            payload["response_format"] = response_format

        last_error: str | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                content: str = data["choices"][0]["message"]["content"]
                return content

            except httpx.HTTPStatusError as exc:
                body_snippet = ""
                try:
                    body_snippet = exc.response.text[:300]
                except Exception:
                    pass
                last_error = f"HTTP {exc.response.status_code}: {body_snippet}"
                if exc.response.status_code in (401, 403):
                    logger.error("NVIDIA API authentication failed")
                    return None
                if exc.response.status_code == 429:
                    if attempt < self._max_retries:
                        time.sleep(2**attempt)
                        continue

            except httpx.TimeoutException:
                last_error = "timeout"
                if attempt < self._max_retries:
                    time.sleep(2**attempt)
                    continue

            except Exception as exc:
                last_error = str(exc)
                if attempt < self._max_retries:
                    time.sleep(2**attempt)
                    continue

        logger.error(
            "NVIDIA API request failed after %d retries: %s", self._max_retries, last_error
        )
        return None

    def __repr__(self) -> str:
        return f"NVIDIAClient(model={self._model!r}, configured={self.is_configured})"
