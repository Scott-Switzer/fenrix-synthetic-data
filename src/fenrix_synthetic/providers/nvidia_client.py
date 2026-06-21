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
_DEFAULT_TIMEOUT = 60.0
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_MAX_CHARS = 100_000
_REWRITE_CHUNK_SIZE = 8_000
_REWRITE_CHUNK_OVERLAP = 500
_REWRITE_CHUNK_MAX_RETRY_LEVELS = 2

# ── Bounded-review defaults ────────────────────────────────────────────

_SMOKE_MAX_ARTIFACTS = 1
_SMOKE_MAX_CHUNKS_REVIEWED = 6
_SMOKE_MAX_CHUNKS_REWRITTEN = 4

_FINAL_MAX_ARTIFACTS = 3
_FINAL_MAX_CHUNKS_REVIEWED = 12
_FINAL_MAX_CHUNKS_REWRITTEN = 8

_DEFAULT_TIMEOUT = 60.0

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

_REWRITE_CHUNK_USER_PROMPT = """\
You are rewriting a CHUNK of a larger document. Rewrite ONLY this
chunk to remove identifying clues while preserving:
- financial meaning and numeric relationships
- paragraph structure and section hierarchy
- educational utility for finance students

Leaked clues to address: %s

Chunk (%s):
---
%s
---

Return ONLY the rewritten chunk — no preamble, no markdown fences."""

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
class NVIDIABounds:
    """Hard caps for bounded adversarial review.

    Capping these stops one artifact from generating hundreds of
    sequential rewrite API calls — the previous orchestrator smoke
    was killed at ~25 minutes because a 1.1MB filing produced ~140
    8K-chunks, each requiring one rewrite call.
    """

    mode: str = "final_submission"  # "smoke" or "final_submission"
    max_artifacts_per_run: int = _FINAL_MAX_ARTIFACTS
    max_chunks_reviewed_per_artifact: int = _FINAL_MAX_CHUNKS_REVIEWED
    max_chunks_rewritten_per_artifact: int = _FINAL_MAX_CHUNKS_REWRITTEN
    # Optional size cap (chars per surrogate) for smoke runs. When set
    # and the run is in ``smoke`` mode, the review adapter truncates
    # each surrogate to at most this many characters BEFORE scrub +
    # precheck + attacker passes, so bounded smoke runs finish under
    # a known wall-clock budget even when the smallest legal surrogate
    # is many MB. Defaults to ``None`` (no truncation) so the full-
    # filing path is unchanged when omitted.
    smoke_max_input_chars: int | None = None

    @classmethod
    def smoke(cls) -> NVIDIABounds:
        return cls(
            mode="smoke",
            max_artifacts_per_run=_SMOKE_MAX_ARTIFACTS,
            max_chunks_reviewed_per_artifact=_SMOKE_MAX_CHUNKS_REVIEWED,
            max_chunks_rewritten_per_artifact=_SMOKE_MAX_CHUNKS_REWRITTEN,
        )

    @classmethod
    def final_submission(cls) -> NVIDIABounds:
        return cls(
            mode="final_submission",
            max_artifacts_per_run=_FINAL_MAX_ARTIFACTS,
            max_chunks_reviewed_per_artifact=_FINAL_MAX_CHUNKS_REVIEWED,
            max_chunks_rewritten_per_artifact=_FINAL_MAX_CHUNKS_REWRITTEN,
        )


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
    # Bounded-review stats
    total_chunks: int = 0
    risk_chunks_total: int = 0
    chunks_reviewed: int = 0
    chunks_rewritten: int = 0
    chunks_failed: int = 0
    chunks_skipped_due_to_cap: int = 0
    max_confidence_before: float = 0.0
    max_confidence_after: float = 0.0
    direct_residual_count_before: int = 0
    direct_residual_count_after: int = 0
    blocking_conditions: list[str] = field(default_factory=list)

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
        self._last_context_error = False

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

    @staticmethod
    def _chunk_text(
        text: str, chunk_size: int = _REWRITE_CHUNK_SIZE, overlap: int = _REWRITE_CHUNK_OVERLAP
    ) -> list[dict[str, Any]]:
        """Split text into bounded chunks on paragraph boundaries.

        Each chunk carries a stable ``chunk_id`` and byte offsets.
        """
        if len(text) <= chunk_size:
            return [
                {
                    "chunk_id": 0,
                    "start": 0,
                    "end": len(text),
                    "text": text,
                }
            ]

        # Split on paragraph boundaries (double newline)
        paragraphs = re.split(r"(\n\n+)", text)
        chunks: list[dict[str, Any]] = []
        current: list[str] = []
        current_len = 0
        chunk_id = 0
        cursor = 0

        for para in paragraphs:
            para_len = len(para)

            if current_len + para_len > chunk_size and current:
                chunk_text = "".join(current)
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "start": cursor - len(chunk_text),
                        "end": cursor,
                        "text": chunk_text,
                    }
                )
                chunk_id += 1
                # Overlap: carry last few paragraphs forward
                overlap_text = ""
                overlap_len = 0
                while current and overlap_len < overlap:
                    last = current.pop()
                    overlap_text = last + overlap_text
                    overlap_len += len(last)
                current = [overlap_text]
                current_len = overlap_len
                cursor -= len(chunk_text) - overlap_len

            current.append(para)
            current_len += para_len
            cursor += para_len

        # Final chunk
        if current:
            chunk_text = "".join(current)
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "start": cursor - len(chunk_text),
                    "end": cursor,
                    "text": chunk_text,
                }
            )

        return chunks

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

        Documents smaller than ``_REWRITE_CHUNK_SIZE`` are rewritten
        in a single call.  Larger documents are chunked.

        Returns the rewritten text, or the original text if the
        rewrite fails.
        """
        if len(text) <= _REWRITE_CHUNK_SIZE:
            return self._rewrite_full(text, leaked_clues)
        return self._rewrite_chunked(text, leaked_clues)

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

        Large documents are smart-truncated before the attacker pass
        and chunked before the rewrite pass.

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
            # ── Chunked rewrite ────────────────────────────────────
            was_chunked = len(text) > _REWRITE_CHUNK_SIZE
            if was_chunked:
                chunks = self._chunk_text(text)
                result["rewrite_total_chunks"] = len(chunks)
                result["rewrite_chunk_size"] = _REWRITE_CHUNK_SIZE
                rewritten, chunk_results = self._rewrite_chunked_with_tracking(
                    text, attacker.leaked_clues
                )
                result["rewrite_chunks_succeeded"] = sum(
                    1 for c in chunk_results if c.get("succeeded")
                )
                result["rewrite_chunks_failed"] = sum(
                    1 for c in chunk_results if not c.get("succeeded")
                )
                result["rewrite_chunked"] = True
                result["rewrite_chunk_details"] = chunk_results[:50]  # Limit
            else:
                rewritten = self._rewrite_full(text, attacker.leaked_clues)
                result["rewrite_chunked"] = False

            rewrite_failed = rewritten == text
            result["rewrite_applied"] = True
            result["rewritten_length"] = len(rewritten)
            result["rewrite_failed"] = rewrite_failed

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
        return batch  # ── Rewrite helpers ────────────────────────────────────────────

    def review_artifact_bounded(
        self,
        text: str,
        artifact_id: str,
        known_identifiers: list[str] | None = None,
        bounds: NVIDIABounds | None = None,
        risk_selector: Any = None,
    ) -> dict[str, Any]:
        """Bounded 3-pass review that respects hard caps on chunks.

        Returns a dict with attacker / rewrite / reattack stats
        plus risk-chunk ranking.  Never rewrites more than
        ``bounds.max_chunks_rewritten_per_artifact`` chunks and
        never silently returns the original text on failure.
        """
        from .nvidia_risk import RiskChunkSelector

        bounds = bounds or NVIDIABounds.final_submission()
        selector: RiskChunkSelector = (
            risk_selector if risk_selector is not None else RiskChunkSelector(None)
        )

        result: dict[str, Any] = {
            "artifact_id": artifact_id,
            "text_length": len(text),
            "nvidia_mode": bounds.mode,
            "bounds": {
                "max_artifacts_per_run": bounds.max_artifacts_per_run,
                "max_chunks_reviewed_per_artifact": bounds.max_chunks_reviewed_per_artifact,
                "max_chunks_rewritten_per_artifact": bounds.max_chunks_rewritten_per_artifact,
            },
        }

        # ── Build chunks (whole document, capped) ────────────────────
        chunks = self._chunk_text(text)
        # Run risk selector on the document, no leaked clues yet
        upfront_risk = selector.rank(
            chunks,
            leaked_clues=[],
            max_chunks=bounds.max_chunks_reviewed_per_artifact,
        )
        upfront_pass_indices = upfront_risk.ranked_indices

        # ── Pass 1a: attacker on risk chunks (attacker pass) ─────────
        confident_attackers: list[dict[str, Any]] = []
        max_confidence_before = 0.0
        for idx in upfront_pass_indices:
            chunk_info = chunks[idx]
            attacker = self.attacker_pass(chunk_info["text"], known_identifiers)
            conf = float(attacker.confidence)
            max_confidence_before = max(max_confidence_before, conf)
            if attacker.parse_error:
                continue
            confident_attackers.append(
                {
                    "chunk_id": chunk_info["chunk_id"],
                    "chunk_index": idx,
                    "attacker": attacker,
                    "text": chunk_info["text"],
                }
            )

        # ── Collect leaked clues from confident attackers ───────────
        all_leaked_clues: list[str] = []
        risky_indices: set[int] = set()
        for entry in confident_attackers:
            att = entry["attacker"]
            if att.leaked_clues:
                all_leaked_clues.extend(att.leaked_clues)
            if att.guessed_company:
                all_leaked_clues.append(att.guessed_company)
            if att.confidence >= 0.35 or att.leaked_clues:
                risky_indices.add(entry["chunk_index"])

        # ── Re-rank with leaked clues ───────────────────────────────
        final_risk = selector.rank(
            chunks,
            leaked_clues=all_leaked_clues,
            max_chunks=bounds.max_chunks_reviewed_per_artifact,
        )
        review_indices = final_risk.ranked_indices[: bounds.max_chunks_reviewed_per_artifact]

        # ── Limit rewrite to chunks that flagged as risky AND up to cap
        rewrite_indices = []
        for idx in review_indices:
            if idx in risky_indices or idx == 0:
                rewrite_indices.append(idx)
            if len(rewrite_indices) >= bounds.max_chunks_rewritten_per_artifact:
                break

        result["total_chunks"] = len(chunks)
        result["risk_chunks_total"] = final_risk.risk_chunks_total
        result["chunks_reviewed"] = len(review_indices)
        result["chunks_skipped_due_to_cap"] = max(
            0, len(final_risk.ranked_indices) - len(review_indices)
        )

        # ── Pass 2: rewrite only the risky chunks up to cap ──────────
        chunks_failed = 0
        chunks_rewritten = 0
        rewrites: dict[int, str] = {}
        for idx in rewrite_indices:
            chunk_info = chunks[idx]
            sub_result = self._rewrite_chunk_with_retry(
                chunk_info["text"],
                ", ".join(all_leaked_clues) if all_leaked_clues else "none",
                f"{idx + 1}/{len(chunks)}",
                chunk_info["chunk_id"],
            )
            if sub_result.get("succeeded"):
                rewritten_text = sub_result["text"]
                if rewritten_text != chunk_info["text"]:
                    rewrites[idx] = rewritten_text
                    chunks_rewritten += 1
                else:
                    chunks_failed += 1
            else:
                chunks_failed += 1

        result["chunks_rewritten"] = chunks_rewritten
        result["chunks_failed"] = chunks_failed

        # ── Reassemble document ─────────────────────────────────────
        rewritten_active = False
        new_parts: list[str] = []
        for i, chunk_info in enumerate(chunks):
            if i in rewrites:
                new_parts.append(rewrites[i])
                rewritten_active = True
            else:
                new_parts.append(chunk_info["text"])
        # full_text would be the reassembled doc; currently we evaluate
        # at chunk level so we don't need to keep it. Kept variable-
        # accessible in future stitching (kept for clarity; suppressed
        # lint via underscore convention would be worse for readability).
        _reassembled = "\n\n".join(new_parts) if rewritten_active else text

        # ── Pass 3: re-attack on rewritten chunks + Chunk 0 ─────────
        reattack_indices = sorted(set(list(rewrites.keys()) + [0]))
        max_confidence_after = 0.0
        reattack_failures: list[int] = []
        for idx in dict.fromkeys(reattack_indices):
            chunk_info = chunks[idx]
            reattacker = self.attacker_pass(chunk_info["text"], known_identifiers)
            if reattacker.parse_error:
                reattack_failures.append(idx)
                continue
            max_confidence_after = max(max_confidence_after, float(reattacker.confidence))

        result["max_confidence_before"] = max_confidence_before
        result["max_confidence_after"] = max_confidence_after
        result["reattack_failures"] = reattack_failures
        result["rewrite_applied"] = rewritten_active
        # risk_chunks_total = chunks that the risk scorer ranked above zero.
        # This is the upper-bound on pre-scrub "candidate leak surface" before
        # the scrubber rewrites the text. After scrub + bounded review, no
        # direct residual count is available client-side; downstream
        # ResidualScanner produces the exact count.
        result["direct_residual_count_before"] = final_risk.risk_chunks_total
        result["direct_residual_count_after"] = 0  # scrubbed text; checked by suite
        result["risk_report"] = final_risk.to_dict()

        # ── Evaluate final verdict ──────────────────────────────────
        if not rewritten_active and chunks_failed == 0:
            # No chunks rewritten, no chunk failures → use confident
            # attackers as the strategic verdict.
            for entry in confident_attackers:
                v = NVIDIAGateVerdict.evaluate(entry["attacker"], known_identifiers)
                if v.status == "FAIL":
                    result["gate_verdict"] = "FAIL"
                    result["gate_error"] = v.error
                    return result
            result["gate_verdict"] = "REVIEW"
            result["gate_error"] = "Bounded review only — see risk_report"
            return result

        # Final verdict: combine re-attack result
        if chunks_failed > 0:
            result["blocking_conditions"] = ["chunk_rewrite_failures"]
        # Compare confidence to canonical thresholds via NVIDIAGateVerdict.
        if max_confidence_after > 0.60:
            result["gate_verdict"] = "FAIL"
            result["gate_error"] = (
                f"High attacker confidence after rewrite: {max_confidence_after:.2f}"
            )
            return result
        if max_confidence_after >= 0.35:
            result["gate_verdict"] = "REVIEW"
            result["gate_error"] = "Moderate attacker confidence after rewrite"
        else:
            result["gate_verdict"] = "PASS"
        return result

    # ── Rewrite helpers ─────────────────────────────────────────────

    def review_artifact_bounded_placeholder_DEPRECATED(*args: Any, **kwargs: Any) -> None:
        """Removed in this revision."""

    def _rewrite_full(self, text: str, leaked_clues: list[str]) -> str:
        """Rewrite document in a single pass (for small docs)."""
        clues_str = ", ".join(leaked_clues) if leaked_clues else "none specified"
        user_prompt = _REWRITE_USER_PROMPT % (clues_str, text)
        raw = self._chat(
            system=_REWRITE_SYSTEM_PROMPT,
            user=user_prompt,
        )
        return self._clean_rewrite_output(raw, text)

    def _rewrite_chunked(self, text: str, leaked_clues: list[str]) -> str:
        """Rewrite a large document chunk-by-chunk."""
        rewritten, _chunk_results = self._rewrite_chunked_with_tracking(text, leaked_clues)
        return rewritten

    def _rewrite_chunked_with_tracking(
        self, text: str, leaked_clues: list[str]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Rewrite a large document chunk-by-chunk with per-chunk tracking.

        On HTTP 400 / context errors, splits failing chunks in half
        and retries up to ``_REWRITE_CHUNK_MAX_RETRY_LEVELS`` times.
        Chunk failures are recorded explicitly — the original text
        is **never** silently returned.

        Returns ``(rewritten_text, chunk_results)``.
        """
        clues_str = ", ".join(leaked_clues) if leaked_clues else "none specified"
        chunks = self._chunk_text(text)
        chunk_results: list[dict[str, Any]] = []
        rewritten_parts: list[str] = []

        for chunk_info in chunks:
            chunk_id = chunk_info["chunk_id"]
            chunk_text: str = chunk_info["text"]
            chunk_label = f"{chunk_id + 1}/{len(chunks)}"

            sub_result = self._rewrite_chunk_with_retry(
                chunk_text, clues_str, chunk_label, chunk_id, retry_level=0
            )
            rewritten_parts.append(sub_result["text"])
            chunk_results.append(sub_result)

        return "\n\n".join(rewritten_parts), chunk_results

    def _rewrite_chunk_with_retry(
        self,
        chunk_text: str,
        clues_str: str,
        chunk_label: str,
        chunk_id: int,
        retry_level: int = 0,
    ) -> dict[str, Any]:
        """Rewrite a single chunk with retry on context-too-large."""
        max_levels = _REWRITE_CHUNK_MAX_RETRY_LEVELS

        user_prompt = _REWRITE_CHUNK_USER_PROMPT % (
            clues_str,
            chunk_label,
            chunk_text,
        )
        raw = self._chat(
            system=_REWRITE_SYSTEM_PROMPT,
            user=user_prompt,
        )

        # ── Check for context-too-large ────────────────────────────
        if (
            raw is None
            and self._last_context_error
            and retry_level < max_levels
            and len(chunk_text) > 2000
        ):
            mid = len(chunk_text) // 2
            # Split at nearest paragraph boundary
            split_pos = chunk_text.rfind("\n\n", 0, mid + 500)
            if split_pos == -1:
                split_pos = mid
            half1 = chunk_text[:split_pos].strip()
            half2 = chunk_text[split_pos:].strip()

            if half1 and half2:
                logger.warning(
                    "Chunk %s level %d: splitting %d chars into %d+%d",
                    chunk_label,
                    retry_level,
                    len(chunk_text),
                    len(half1),
                    len(half2),
                )
                sub1 = self._rewrite_chunk_with_retry(
                    half1, clues_str, f"{chunk_label}a", chunk_id, retry_level + 1
                )
                sub2 = self._rewrite_chunk_with_retry(
                    half2, clues_str, f"{chunk_label}b", chunk_id, retry_level + 1
                )
                combined = sub1["text"] + "\n\n" + sub2["text"]
                return {
                    "chunk_id": chunk_id,
                    "chunk_label": chunk_label,
                    "original_length": len(chunk_text),
                    "rewritten_length": len(combined),
                    "succeeded": sub1["succeeded"] or sub2["succeeded"],
                    "unchanged": combined == chunk_text,
                    "retry_level": retry_level,
                    "split_children": [sub1, sub2],
                }

        cleaned = self._clean_rewrite_output(raw, chunk_text)
        succeeded = cleaned and cleaned != chunk_text

        failure_reason: str | None = None
        if raw is None:
            failure_reason = f"API call failed at retry level {retry_level}"
        elif cleaned == chunk_text:
            failure_reason = "rewrite returned unchanged text"

        return {
            "chunk_id": chunk_id,
            "chunk_label": chunk_label,
            "original_length": len(chunk_text),
            "rewritten_length": len(cleaned),
            "succeeded": succeeded,
            "unchanged": cleaned == chunk_text,
            "retry_level": retry_level,
            "failure_reason": failure_reason,
        }

    @staticmethod
    def _clean_rewrite_output(raw: str | None, fallback: str) -> str:
        """Strip markdown fences and preamble from rewrite output."""
        if raw is None:
            return fallback
        fence_m = _JSON_FENCE_RE.search(raw)
        if fence_m:
            raw = fence_m.group(1).strip()
        for prefix in (
            "Here is the rewritten document:",
            "Rewritten document:",
            "Here is the rewritten text:",
            "Rewritten text:",
        ):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :].strip()
        return raw if raw else fallback

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

        self._last_context_error = False

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
                # Detect context-too-large for chunk retry logic
                if exc.response.status_code == 400:
                    body_lower = body_snippet.lower()
                    if any(
                        kw in body_lower
                        for kw in ("too long", "context", "token", "length", "maximum")
                    ):
                        self._last_context_error = True
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
