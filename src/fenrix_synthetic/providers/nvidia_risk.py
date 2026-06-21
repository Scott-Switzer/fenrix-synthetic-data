"""Risk chunk selection for bounded NVIDIA adversarial review.

Picks which chunks of a large filing actually warrant rewrite.
Scoring is deterministic and bounded so the selector never pulls
more chunks than the smoke / final-submission budget permits.

Risk criteria (high-signal set)
-------------------------------
1. **Structural head** — Chunk 0 always selected (header, business
   description, ticker/CIK/legislative boilerplate).
2. **Leaked-clue match** — chunks containing string overlap with
   ``leaked_clues`` returned by the attacker pass.
3. **Registry alias match** — chunks containing values from the
   populated ``EntityRegistry`` (CIK, ticker, domain, etc.).
4. **Direct-pattern match** — chunks containing CIK / accession /
   ticker / domain regex patterns (independent of the registry).

A chunk that fails every criterion carries **zero risk score** and
is excluded from the rewrite set.  Ties are broken by chunk order
so the more text the attacker saw, the higher the priority.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..identity import EntityRegistry

logger = logging.getLogger(__name__)

# ── Direct-pattern risk markers ─────────────────────────────────────────

_CIK_PATTERN_RE = re.compile(r"(?:CIK[:#\s]+|cik=)(0\d{6,9}|\d{6,10})", re.IGNORECASE)
_ACCESSION_PATTERN_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
_TICKER_PATTERN_RE = re.compile(
    r"\b(?:NYSE|NASDAQ|NYSE\s*Arca)\s*:\s*[A-Z]{1,5}\b|"
    r"\b(?:ticker|trading)\s+symbol\s*(?::|is)?\s*[A-Z]{1,5}\b"
)
_DOMAIN_PATTERN_RE = re.compile(r"https?://(?:www\.)?[a-z0-9\-\.]+\.[a-z]{2,}[^\s]*")
_CIK_URL_RE = re.compile(r"https?://[^\s]*?(?:cik|CIK)[=/_]?\d{6,10}[^\s]*")

# ── Risk-score weights (deterministic) ──────────────────────────────────
_W_HEAD = 3.0
_W_LEAK = 5.0
_W_REGISTRY = 4.0
_W_DIRECT_PATTERN = 4.5


@dataclass
class RiskChunkReport:
    """Risk-scored ranking for a single artifact's chunks."""

    artifact_id: str
    total_chunks: int
    risk_chunks_total: int
    chunks_reviewed: int
    chunks_rewritten: int
    chunks_skipped_due_to_cap: int
    scored_chunks: list[dict[str, Any]] = field(default_factory=list)
    ranked_indices: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "total_chunks": self.total_chunks,
            "risk_chunks_total": self.risk_chunks_total,
            "chunks_reviewed": self.chunks_reviewed,
            "chunks_rewritten": self.chunks_rewritten,
            "chunks_skipped_due_to_cap": self.chunks_skipped_due_to_cap,
            "scored_chunks": self.scored_chunks,
            "ranked_indices": self.ranked_indices,
        }


class RiskChunkSelector:
    """Pick which chunks of a filing need rewriting.

    Parameters
    ----------
    registry:
        Populated ``EntityRegistry``.  When ``None``, the
        registry-criterion is skipped and only structural + leaked
        + direct-pattern criteria contribute.
    """

    def __init__(self, registry: EntityRegistry | None = None) -> None:
        self._registry = registry
        self._registry_values: list[str] = self._build_registry_values()

    def rank(
        self,
        chunks: list[dict[str, Any]],
        leaked_clues: list[str],
        max_chunks: int,
    ) -> RiskChunkReport:
        """Score and rank chunks; return up to ``max_chunks`` indices.

        Chunks with risk score > 0 are eligible.  Ties are broken by
        chunk order (smaller index = earlier in the document).
        Over-cap chunks are counted but excluded.
        """
        scored: list[dict[str, Any]] = []
        for i, chunk_info in enumerate(chunks):
            chunk_text: str = chunk_info.get("text", "")
            score, reasons = self._score_chunk(chunk_text, i, leaked_clues)
            entry = {
                "chunk_index": i,
                "chunk_text_length": len(chunk_text),
                "risk_score": score,
                "matched_reasons": reasons,
            }
            scored.append(entry)

        # Sort by score desc, then index asc (deterministic tie-break)
        eligible = [s for s in scored if s["risk_score"] > 0]
        eligible_sorted = sorted(eligible, key=lambda s: (-s["risk_score"], s["chunk_index"]))
        ranked_indices = [s["chunk_index"] for s in eligible_sorted]
        chunks_skipped_due_to_cap = max(0, len(ranked_indices) - max_chunks)
        ranked_indices = ranked_indices[:max_chunks]

        artifact_id = chunks[0].get("start_doc_id", "") if chunks else ""
        return RiskChunkReport(
            artifact_id=artifact_id,
            total_chunks=len(chunks),
            risk_chunks_total=len(eligible),
            chunks_reviewed=min(len(ranked_indices), max_chunks),
            chunks_rewritten=min(len(ranked_indices), max_chunks),
            chunks_skipped_due_to_cap=chunks_skipped_due_to_cap,
            scored_chunks=scored,
            ranked_indices=ranked_indices,
        )

    def contains_clue(self, chunk_text: str, clue: str) -> bool:
        """Loose substring check for a leaked clue inside a chunk."""
        if not clue or len(clue) < 3:
            return False
        clean_clue = clue.strip().strip("'\".,;:!?")
        if not clean_clue:
            return False
        lower_chunk = chunk_text.lower()
        return clean_clue.lower() in lower_chunk

    def contains_registry_value(self, chunk_text: str) -> bool:
        """Cheap substring check for any registry private value."""
        if not self._registry_values:
            return False
        for v in self._registry_values:
            if v and v in chunk_text:
                return True
        return False

    # ── Internal ────────────────────────────────────────────────────

    def _score_chunk(
        self,
        chunk_text: str,
        chunk_index: int,
        leaked_clues: list[str],
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        # Criterion 1: structural head
        if chunk_index == 0:
            score += _W_HEAD
            reasons.append("structural_head")

        # Criterion 2: leaked-clue match
        for clue in leaked_clues or []:
            if self.contains_clue(chunk_text, clue):
                score += _W_LEAK
                reasons.append(f"leaked_clue:{clue[:40]}")
                break  # one match is enough signal

        # Criterion 3: registry alias match
        if self.contains_registry_value(chunk_text):
            score += _W_REGISTRY
            reasons.append("registry_alias")

        # Criterion 4: direct-pattern match
        if _CIK_PATTERN_RE.search(chunk_text):
            score += _W_DIRECT_PATTERN
            reasons.append("cik_pattern")
        if _ACCESSION_PATTERN_RE.search(chunk_text):
            score += _W_DIRECT_PATTERN
            reasons.append("accession_pattern")
        if _TICKER_PATTERN_RE.search(chunk_text):
            score += _W_DIRECT_PATTERN
            reasons.append("ticker_pattern")
        if _DOMAIN_PATTERN_RE.search(chunk_text) or _CIK_URL_RE.search(chunk_text):
            score += _W_DIRECT_PATTERN
            reasons.append("domain_pattern")

        return score, reasons

    def _build_registry_values(self) -> list[str]:
        """Build registry values list for cheap substring matching."""
        if not self._registry:
            return []
        values: list[str] = []
        for entity in self._registry.all_entities():
            v = entity.canonical_private_value
            if v and len(v) >= 3:
                values.append(v)
        for alias in self._registry.all_aliases():
            v = alias.private_alias_value
            if v and len(v) >= 3 and v not in values:
                values.append(v)
        return values
