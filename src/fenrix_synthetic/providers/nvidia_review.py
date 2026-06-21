"""Optional NVIDIA review adapter for adversarial anonymization review.

Wraps the ``NVIDIAClient`` (OpenAI-compatible chat-completions API)
and provides backward-compatible ``review_batch`` entry point.

Configuration from environment: NVIDIA_API_KEY, NVIDIA_MODEL, NVIDIA_BASE_URL.
Never prints, logs, or persists the API key.

Pre-NVIDIA pipeline (when ``EntityRegistry`` is provided):
1. Deterministic scrub — remove CIK, ticker, domains, product names.
2. Precheck scan — if direct identifiers remain, skip NVIDIA.
3. Attacker pass — can the model identify the company?
4. Rewrite pass — chunked rewrite of leaked sections.
5. Re-attack pass — after rewrite, can the model still identify?

Gate rules:
* BLOCKED_PRECHECK — direct identifiers remain after deterministic scrub.
* PASS    — confidence < 0.35 AND no direct identifiers remain.
* REVIEW  — confidence 0.35–0.60 OR clues are vague.
* FAIL    — confidence > 0.60 OR correct company guess OR direct identifiers remain.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..identity import EntityRegistry
from .nvidia_client import NVIDIABounds, NVIDIAClient
from .nvidia_risk import RiskChunkSelector
from .nvidia_scrub import PreNVIDIAScrubber

logger = logging.getLogger(__name__)


class NVIDIAReviewAdapter:
    """Backward-compatible adapter wrapping the 3-pass NVIDIAClient.

    When *registry* is provided, deterministic scrubbing and
    precheck gating run before any NVIDIA API call.
    """

    def __init__(
        self,
        registry: EntityRegistry | None = None,
        bounds: NVIDIABounds | None = None,
    ) -> None:
        self._client = NVIDIAClient()
        self._scrubber: PreNVIDIAScrubber | None = PreNVIDIAScrubber(registry) if registry else None
        self._risk_selector = RiskChunkSelector(registry)
        self._bounds = bounds or NVIDIABounds.final_submission()

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
        """Run pre-scrub + precheck + 3-pass NVIDIA review on sampled files.

        Collects up to 3 .md files from the anonymized directory.
        Each file is deterministically scrubbed and prechecked before
        being sent to NVIDIA.  Artifacts that fail the precheck are
        recorded as ``BLOCKED_PRECHECK`` and never sent to the API.
        """
        if not self.is_configured():
            return {"status": "not_configured", "ticker": ticker}

        if known_identifiers is None:
            known_identifiers = [ticker]

        # ── Collect sample texts ────────────────────────────────────
        raw_artifacts: list[tuple[str, str]] = []
        for text_path in sorted(anonymized_dir.rglob("*.md")):
            try:
                text = text_path.read_text(encoding="utf-8", errors="replace")
                if len(text) > 500:
                    artifact_id = str(text_path.relative_to(anonymized_dir))
                    raw_artifacts.append((artifact_id, text))
                if len(raw_artifacts) >= 3:
                    break
            except Exception:
                continue

        if not raw_artifacts:
            return {
                "status": "no_samples",
                "ticker": ticker,
                "model": self._client.model,
            }

        # ── Scrub + precheck each artifact ──────────────────────────
        precheck_results: list[dict[str, Any]] = []
        nvidia_artifacts: list[tuple[str, str]] = []
        blocked_count = 0

        for artifact_id, raw_text in raw_artifacts:
            if self._scrubber:
                scrubbed, precheck = self._scrubber.scrub_and_precheck(raw_text)
                precheck_results.append(
                    {
                        "artifact_id": artifact_id,
                        "precheck_passed": precheck.passed,
                        "precheck_status": precheck.status,
                        "blocking_hits": precheck.blocking_hits,
                        "total_hits": precheck.total_hits,
                        "hit_types": precheck.hit_types,
                        "hit_summary": precheck.hit_summary,
                        "original_length": len(raw_text),
                        "scrubbed_length": len(scrubbed),
                    }
                )

                if precheck.passed:
                    nvidia_artifacts.append((artifact_id, scrubbed))
                else:
                    blocked_count += 1
                    logger.warning(
                        "Pre-NVIDIA precheck blocked %s: %s",
                        artifact_id,
                        precheck.hit_summary,
                    )
            else:
                # No scrubber → pass raw text straight through
                nvidia_artifacts.append((artifact_id, raw_text))

        # ── Run NVIDIA only on artifacts that passed precheck ────────
        if nvidia_artifacts:
            bounded_results: list = []
            # Cap the number of artifacts by bounds.max_artifacts_per_run.
            artifact_limit = self._bounds.max_artifacts_per_run
            for _art_idx, (artifact_id, scrubbed_text) in enumerate(
                nvidia_artifacts[:artifact_limit]
            ):
                try:
                    bounded = self._client.review_artifact_bounded(
                        scrubbed_text,
                        artifact_id,
                        known_identifiers,
                        bounds=self._bounds,
                        risk_selector=self._risk_selector,
                    )
                except Exception as exc:
                    logger.warning("Bounded review failed for %s: %s", artifact_id, exc)
                    bounded = {
                        "artifact_id": artifact_id,
                        "gate_verdict": "FAIL",
                        "gate_error": str(exc),
                    }
                bounded_results.append(bounded)
            batch = self._aggregate_bounded(bounded_results)
        else:
            # All artifacts blocked
            batch = type(
                "BatchResult",
                (),
                {
                    "status": "BLOCKED_PRECHECK",
                    "decision": "BLOCKED_PRECHECK",
                    "artifacts_reviewed": 0,
                    "pass_count": 0,
                    "review_count": 0,
                    "fail_count": blocked_count,
                    "rewrite_count": 0,
                    "results": [],
                    "errors": [f"All {blocked_count} artifacts blocked by precheck"],
                },
            )()

        return {
            "status": batch.status,
            "decision": batch.decision,
            "ticker": ticker,
            "model": self._client.model,
            "samples_reviewed": batch.artifacts_reviewed,
            "samples_blocked": blocked_count,
            "pass_count": batch.pass_count,
            "review_count": batch.review_count,
            "fail_count": batch.fail_count,
            "rewrite_count": batch.rewrite_count,
            "attacker_results": batch.results,
            "precheck_results": precheck_results,
            "parse_errors": sum(
                1 for r in batch.results if r.get("attacker", {}).get("parse_error")
            ),
            "all_parsed": all(not r.get("attacker", {}).get("parse_error") for r in batch.results),
            "errors": batch.errors,
        }

    def _aggregate_bounded(self, bounded_results: list[dict]) -> Any:
        """Aggregate bounded per-artifact results into NVIDIABatchResult."""
        from .nvidia_client import NVIDIABatchResult

        batch = NVIDIABatchResult()
        for r in bounded_results:
            batch.results.append(r)
            batch.artifacts_reviewed += 1
            verdict = r.get("gate_verdict", "FAIL")
            if verdict == "PASS":
                batch.pass_count += 1
            elif verdict == "REVIEW":
                batch.review_count += 1
            else:
                batch.fail_count += 1
            batch.total_chunks += int(r.get("total_chunks", 0))
            batch.risk_chunks_total += int(r.get("risk_chunks_total", 0))
            batch.chunks_reviewed += int(r.get("chunks_reviewed", 0))
            batch.chunks_rewritten += int(r.get("chunks_rewritten", 0))
            batch.chunks_failed += int(r.get("chunks_failed", 0))
            batch.chunks_skipped_due_to_cap += int(r.get("chunks_skipped_due_to_cap", 0))
            batch.max_confidence_before = max(
                batch.max_confidence_before, float(r.get("max_confidence_before", 0))
            )
            batch.max_confidence_after = max(
                batch.max_confidence_after, float(r.get("max_confidence_after", 0))
            )
            batch.direct_residual_count_before += int(r.get("direct_residual_count_before", 0))
            batch.direct_residual_count_after += int(r.get("direct_residual_count_after", 0))
            if r.get("gate_verdict") in ("FAIL", "BLOCKED_PRECHECK"):
                for cond in r.get("blocking_conditions", []) or [r.get("gate_error", "")]:
                    if cond:
                        batch.blocking_conditions.append(cond)
        batch.status = batch.decision
        return batch

    def review_artifact(
        self,
        text: str,
        artifact_id: str,
        known_identifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run pre-scrub + precheck + 3-pass review on a single artifact."""
        if not self.is_configured():
            return {"gate_verdict": "NOT_RUN", "error": "not configured"}

        # ── Scrub + precheck ────────────────────────────────────────
        scrubbed = text
        if self._scrubber:
            scrubbed, precheck = self._scrubber.scrub_and_precheck(text)
            if not precheck.passed:
                return {
                    "artifact_id": artifact_id,
                    "text_length": len(text),
                    "gate_verdict": "BLOCKED_PRECHECK",
                    "gate_error": f"Precheck failed: {precheck.hit_summary}",
                    "precheck": precheck.to_dict(),
                }

        result = self._client.review_artifact(scrubbed, artifact_id, known_identifiers)
        if self._scrubber:
            result["precheck_passed"] = True
            result["original_length"] = len(text)
        return result
