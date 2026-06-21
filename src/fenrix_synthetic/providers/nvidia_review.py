"""Optional NVIDIA review adapter for adversarial anonymization review.

Wraps the ``NVIDIAClient`` (OpenAI-compatible chat-completions API)
and provides backward-compatible ``review_batch`` entry point.

Configuration from environment: NVIDIA_API_KEY, NVIDIA_MODEL, NVIDIA_BASE_URL.
Never prints, logs, or persists the API key.

3-pass architecture:
1. Attacker pass — can the model identify the company?
2. Rewrite pass — generalize leaked clues while preserving utility.
3. Re-attack pass — after rewrite, can the model still identify?

Gate rules:
* PASS    — confidence < 0.35 AND no direct identifiers remain.
* REVIEW  — confidence 0.35–0.60 OR clues are vague.
* FAIL    — confidence > 0.60 OR correct company guess OR direct identifiers remain.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .nvidia_client import NVIDIAClient

logger = logging.getLogger(__name__)


class NVIDIAReviewAdapter:
    """Backward-compatible adapter wrapping the 3-pass NVIDIAClient."""

    def __init__(self) -> None:
        self._client = NVIDIAClient()

    def is_configured(self) -> bool:
        return self._client.is_configured

    @property
    def model(self) -> str:
        return self._client.model

    def review_batch(
        self,
        anonymized_dir: Path,
        ticker: str,
        known_identifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run 3-pass NVIDIA review on a sample of anonymized files.

        Collects up to 3 .md files from the anonymized directory,
        runs the full attacker→rewrite→re-attack pipeline on each,
        and returns aggregated results.
        """
        if not self.is_configured():
            return {"status": "not_configured", "ticker": ticker}

        if known_identifiers is None:
            known_identifiers = [ticker]

        # Collect sample texts (skip very small files, limit to 3)
        artifacts: list[tuple[str, str]] = []
        for text_path in sorted(anonymized_dir.rglob("*.md")):
            try:
                text = text_path.read_text(encoding="utf-8", errors="replace")
                if len(text) > 500:
                    artifact_id = str(text_path.relative_to(anonymized_dir))
                    artifacts.append((artifact_id, text))
                if len(artifacts) >= 3:
                    break
            except Exception:
                continue

        if not artifacts:
            return {
                "status": "no_samples",
                "ticker": ticker,
                "model": self._client.model,
            }

        # Run full 3-pass review
        batch = self._client.review_batch(artifacts, known_identifiers)

        return {
            "status": batch.status,
            "decision": batch.decision,
            "ticker": ticker,
            "model": self._client.model,
            "samples_reviewed": batch.artifacts_reviewed,
            "pass_count": batch.pass_count,
            "review_count": batch.review_count,
            "fail_count": batch.fail_count,
            "rewrite_count": batch.rewrite_count,
            "attacker_results": batch.results,
            "parse_errors": sum(
                1 for r in batch.results if r.get("attacker", {}).get("parse_error")
            ),
            "all_parsed": all(not r.get("attacker", {}).get("parse_error") for r in batch.results),
            "errors": batch.errors,
        }

    def review_artifact(
        self,
        text: str,
        artifact_id: str,
        known_identifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run 3-pass review on a single artifact."""
        if not self.is_configured():
            return {"gate_verdict": "NOT_RUN", "error": "not configured"}
        return self._client.review_artifact(text, artifact_id, known_identifiers)
