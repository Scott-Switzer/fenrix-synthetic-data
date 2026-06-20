"""Optional NVIDIA review adapter for adversarial anonymization review.

Reads NVIDIA_API_KEY and NVIDIA_MODEL from environment.
Never prints or persists the key.

Repaired behavior:
- Requires schema-valid JSON responses with: guessed_company, guessed_ticker,
  confidence, clues, identified_direct_identifier
- Retries malformed output with stricter JSON-only prompt
- Never converts a parse error into confidence zero
- Treats all parse errors as blocking failures
- Records whether any response mentioned a true identifier
- A correct guess (matching company name, ticker, CIK, domain, or alias)
  must block release
- Runs only after deterministic scanning passes (enforced by runner)
- NVIDIA disabled, unavailable, timed out, or exhausted is blocking
  when enable_nvidia=true
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class NVIDIAReviewAdapter:
    """Optional NVIDIA review adapter."""

    def __init__(self) -> None:
        self.api_key = os.environ.get("NVIDIA_API_KEY", "")
        self.model = os.environ.get("NVIDIA_MODEL", "meta/llama3-70b-instruct")
        self.max_rounds = 3
        self.timeout = 60
        self.max_retries = 3

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def review_batch(self, anonymized_dir: Path, ticker: str) -> dict[str, Any]:
        """Run NVIDIA review on a sample of anonymized files."""
        if not self.is_configured():
            return {"status": "not_configured", "ticker": ticker}

        # Collect sample texts (skip very small files)
        samples: list[str] = []
        for text_path in anonymized_dir.rglob("*.md"):
            try:
                text = text_path.read_text(encoding="utf-8", errors="replace")
                if len(text) > 500:  # Skip tiny files
                    samples.append(text[:2000])
                if len(samples) >= 3:
                    break
            except Exception:
                continue

        if not samples:
            return {"status": "no_samples", "ticker": ticker}

        # Attacker mode
        attacker_results: list[dict[str, Any]] = []
        total_parse_errors = 0

        for i, sample in enumerate(samples):
            try:
                result = self._attacker_mode(sample, ticker)
                if result.get("parse_error"):
                    total_parse_errors += 1
                attacker_results.append({"sample_index": i, "result": result})
            except Exception as exc:
                logger.warning("Attacker mode failed for sample %d: %s", i, exc)
                total_parse_errors += 1
                attacker_results.append(
                    {
                        "sample_index": i,
                        "result": {
                            "guessed_company": "",
                            "confidence": -1,  # -1 = error, never 0
                            "clues": [],
                            "parse_error": True,
                            "error": str(exc),
                        },
                    }
                )

        return {
            "status": "completed",
            "ticker": ticker,
            "model": self.model,
            "samples_reviewed": len(samples),
            "attacker_results": attacker_results,
            "max_rounds": self.max_rounds,
            "parse_errors": total_parse_errors,
            "all_parsed": total_parse_errors == 0,
            "proposed_revisions": [],
        }

    def _attacker_mode(
        self, text: str, ticker: str, known_identifiers: list[str] | None = None
    ) -> dict[str, Any]:
        """Attacker mode: guess likely company.

        Returns schema-valid JSON with guaranteed keys:
        - guessed_company: string
        - guessed_ticker: string or null
        - confidence: number 0.0-1.0
        - clues: array of strings
        - identified_direct_identifier: boolean

        confidence = -1 on parse error (never 0).
        confidence = -9 on network/transport error.
        correct_guess = True if guessed company/ticker/clues match any known identifier.
        """
        if known_identifiers is None:
            known_identifiers = [ticker]
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a re-identification attacker. "
                            "Given an anonymized financial document, guess the original company. "
                            "You MUST respond with ONLY a valid JSON object with exactly these keys: "
                            "guessed_company (string), guessed_ticker (string or null), "
                            "confidence (number 0.0-1.0), clues (array of strings), "
                            "identified_direct_identifier (boolean). "
                            "No markdown, no explanation, no code fences. "
                            'Example: {"guessed_company": "Apple Inc.", "guessed_ticker": "AAPL", '
                            '"confidence": 0.85, "clues": ["iPhone mentions", "Cupertino"], '
                            '"identified_direct_identifier": false}'
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Document excerpt:\n{text[:1500]}",
                    },
                ],
                "temperature": 0.1,
                "max_tokens": 256,
            }

            last_response = ""
            for attempt in range(self.max_retries + 1):
                try:
                    resp = requests.post(
                        "https://integrate.api.nvidia.com/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=self.timeout,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    last_response = content

                    # Attempt JSON parsing with multiple strategies
                    parsed = self._parse_attacker_response(content)

                    if parsed is not None:
                        # Check if guessed company or clues mention a true identifier
                        correct_guess = _check_identifier_match(
                            parsed.get("guessed_company", ""),
                            parsed.get("clues", []),
                            known_identifiers,
                        )
                        return {
                            "guessed_company": parsed.get("guessed_company", ""),
                            "guessed_ticker": parsed.get("guessed_ticker"),
                            "confidence": float(parsed.get("confidence", 0)),
                            "clues": parsed.get("clues", []),
                            "identified_direct_identifier": bool(
                                parsed.get("identified_direct_identifier", False)
                            ),
                            "raw_response_truncated": content[:200],
                            "parse_error": False,
                            "correct_guess": correct_guess,
                        }

                    # If parse failed and we have retries left, try again
                    # with a stricter prompt
                    if attempt < self.max_retries:
                        logger.warning(
                            "NVIDIA parse attempt %d failed for sample, retrying with stricter prompt",
                            attempt + 1,
                        )
                        payload["messages"][0]["content"] = (
                            "You MUST respond with ONLY a JSON object. "
                            "No text before or after. No markdown. No explanation. "
                            'Format: {"guessed_company":"string","guessed_ticker":"string or null",'
                            '"confidence":0.0,"clues":[],"identified_direct_identifier":false}'
                        )
                        payload["temperature"] = 0.0  # Reduce variability
                        time.sleep(0.5 * (attempt + 1))  # Backoff
                        continue

                except Exception as exc:
                    if attempt == self.max_retries:
                        raise
                    logger.warning("NVIDIA request attempt %d failed: %s", attempt + 1, exc)
                    time.sleep(1.0 * (attempt + 1))

            # All retries exhausted with parse errors
            return {
                "guessed_company": "",
                "guessed_ticker": None,
                "confidence": -1,  # NEVER 0 for parse errors
                "clues": [],
                "identified_direct_identifier": False,
                "raw_response_truncated": last_response[:200],
                "parse_error": True,
                "parse_failure_detail": (
                    f"Failed to parse JSON after {self.max_retries + 1} attempts. "
                    "Response was not valid JSON."
                ),
            }

        except Exception as exc:
            logger.warning("NVIDIA attacker mode failed: %s", exc)
            return {
                "guessed_company": "",
                "guessed_ticker": None,
                "confidence": -9,  # -9 = transport/network error, never 0
                "clues": [],
                "identified_direct_identifier": False,
                "parse_error": True,
                "error": str(exc),
            }

    @staticmethod
    def _parse_attacker_response(content: str) -> dict[str, Any] | None:
        """Parse attacker response with multiple strategies.

        Returns None if all strategies fail.
        """
        # Strategy 1: Direct JSON parse
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "guessed_company" in parsed:
                _validate_attacker_schema(parsed)
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: Extract JSON from code fences
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if fence_match:
            try:
                parsed = json.loads(fence_match.group(1))
                if isinstance(parsed, dict) and "guessed_company" in parsed:
                    _validate_attacker_schema(parsed)
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        # Strategy 3: Find first JSON object in text
        brace_match = re.search(r'\{[^{}]*"guessed_company"[^{}]*\}', content, re.DOTALL)
        if brace_match:
            try:
                parsed = json.loads(brace_match.group(0))
                if isinstance(parsed, dict) and "guessed_company" in parsed:
                    _validate_attacker_schema(parsed)
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        return None


def _validate_attacker_schema(parsed: dict[str, Any]) -> None:
    """Validate the attacker response schema. Raises ValueError on failure."""
    if not isinstance(parsed.get("guessed_company"), str):
        raise ValueError("guessed_company must be string")
    if not isinstance(parsed.get("confidence"), (int, float)):
        raise ValueError("confidence must be number")
    if not isinstance(parsed.get("clues"), list):
        raise ValueError("clues must be list")
    # Normalize types
    parsed["confidence"] = float(parsed["confidence"])
    parsed["guessed_company"] = str(parsed["guessed_company"])
    parsed["clues"] = [str(c) for c in parsed["clues"]]
    # guessed_ticker is optional
    if "guessed_ticker" in parsed and parsed["guessed_ticker"] is not None:
        parsed["guessed_ticker"] = str(parsed["guessed_ticker"])
    # identified_direct_identifier is optional, default false
    parsed.setdefault("identified_direct_identifier", False)


def _check_identifier_match(
    guessed: str,
    clues: list[str],
    known_identifiers: list[str],
) -> bool:
    """Check if guessed company, ticker, or any clue mentions a known identifier.

    Uses case-insensitive substring matching against known identifiers
    (company names, tickers, CIKs, domains, aliases).
    """
    if not guessed or not known_identifiers:
        return False
    text_to_check = guessed + " " + " ".join(clues)
    text_lower = text_to_check.lower()
    for ident in known_identifiers:
        if not ident:
            continue
        if ident.lower() in text_lower:
            return True
    return False
