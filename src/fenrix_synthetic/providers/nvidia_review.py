"""Optional NVIDIA review adapter for adversarial anonymization review.

Reads NVIDIA_API_KEY and NVIDIA_MODEL from environment.
Never prints or persists the key.
"""

from __future__ import annotations

import logging
import os
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
        self.retries = 2

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def review_batch(self, anonymized_dir: Path, ticker: str) -> dict[str, Any]:
        """Run NVIDIA review on a sample of anonymized files."""
        if not self.is_configured():
            return {"status": "not_configured", "ticker": ticker}

        # Collect sample texts
        samples: list[str] = []
        for text_path in anonymized_dir.rglob("*.md"):
            try:
                text = text_path.read_text(encoding="utf-8", errors="replace")
                samples.append(text[:2000])  # Limit sample size
                if len(samples) >= 3:
                    break
            except Exception:
                continue

        if not samples:
            return {"status": "no_samples", "ticker": ticker}

        # Attacker mode
        attacker_results: list[dict[str, Any]] = []
        for i, sample in enumerate(samples):
            try:
                result = self._attacker_mode(sample, ticker)
                attacker_results.append({"sample_index": i, "result": result})
            except Exception as exc:
                logger.warning("Attacker mode failed for sample %d: %s", i, exc)
                attacker_results.append({"sample_index": i, "error": str(exc)})

        return {
            "status": "completed",
            "ticker": ticker,
            "model": self.model,
            "samples_reviewed": len(samples),
            "attacker_results": attacker_results,
            "max_rounds": self.max_rounds,
            "proposed_revisions": [],  # No automatic overwrite
        }

    def _attacker_mode(self, text: str, ticker: str) -> dict[str, Any]:
        """Attacker mode: guess likely company."""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a re-identification attacker. "
                            "Given an anonymized financial document, guess the original company. "
                            "Respond ONLY with a JSON object: "
                            '{"guessed_company": "...", "confidence": 0.0-1.0, "clues": ["..."]}'
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

            for attempt in range(self.retries + 1):
                try:
                    resp = requests.post(
                        "https://integrate.api.nvidia.com/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=self.timeout,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    # Try to parse JSON
                    import json

                    try:
                        parsed = json.loads(content)
                        return {
                            "guessed_company": parsed.get("guessed_company", ""),
                            "confidence": float(parsed.get("confidence", 0)),
                            "clues": parsed.get("clues", []),
                            "raw_response_truncated": content[:200],
                        }
                    except json.JSONDecodeError:
                        return {
                            "guessed_company": "",
                            "confidence": 0,
                            "clues": [],
                            "raw_response_truncated": content[:200],
                            "parse_error": True,
                        }
                except Exception as exc:
                    if attempt == self.retries:
                        raise
                    logger.warning("NVIDIA request attempt %d failed: %s", attempt + 1, exc)

        except Exception as exc:
            logger.warning("NVIDIA attacker mode failed: %s", exc)
            return {"error": str(exc), "guessed_company": "", "confidence": 0, "clues": []}

        return {"guessed_company": "", "confidence": 0, "clues": []}
