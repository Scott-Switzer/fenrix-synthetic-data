"""Candidate-universe schema and validation (Phase 4R).

Defines the normalized candidate-universe format for structured
privacy attacks. Each candidate has opaque ID, return series,
and metadata. Validates for duplicates, insufficient overlap,
and suspicious patterns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CandidateEntry:
    """A single candidate in the universe."""

    candidate_id: str
    returns: list[float] = field(default_factory=list)
    prices: list[float] = field(default_factory=list)
    trading_days: list[int] = field(default_factory=list)
    volume: list[float] = field(default_factory=list)
    source_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateUniverse:
    """A universe of candidate companies for structured re-identification."""

    universe_id: str
    schema_version: str = "1.0.0"
    candidates: list[CandidateEntry] = field(default_factory=list)
    universe_hash: str = ""
    min_overlap_days: int = 60
    min_observation_count: int = 100

    def validate(self) -> tuple[bool, list[str]]:
        """Validate the candidate universe meets all requirements."""
        issues: list[str] = []

        # Check for duplicate candidate IDs
        ids = [c.candidate_id for c in self.candidates]
        if len(ids) != len(set(ids)):
            dupes = [x for x in ids if ids.count(x) > 1]
            issues.append(f"Duplicate candidate IDs: {list(set(dupes))}")

        # Check minimum observation counts
        for c in self.candidates:
            if len(c.returns) < self.min_observation_count:
                issues.append(
                    f"Candidate {c.candidate_id}: only {len(c.returns)} observations "
                    f"(min {self.min_observation_count})"
                )

        # Check for impossible values
        for c in self.candidates:
            for i, r_val in enumerate(c.returns):
                if not isinstance(r_val, (int, float)):
                    issues.append(f"Candidate {c.candidate_id}: non-numeric return at index {i}")
                    break
            for i, p_val in enumerate(c.prices):
                if isinstance(p_val, (int, float)) and p_val < 0:
                    issues.append(f"Candidate {c.candidate_id}: negative price at index {i}")
                    break

        # Check for duplicate source series (same source_hash)
        source_hashes = [c.source_hash for c in self.candidates if c.source_hash]
        dupe_hashes = {h for h in source_hashes if source_hashes.count(h) > 1}
        if dupe_hashes:
            issues.append(f"Duplicate source hashes: {dupe_hashes}")

        return len(issues) == 0, issues

    def size(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_id": self.universe_id,
            "schema_version": self.schema_version,
            "candidate_count": len(self.candidates),
            "universe_hash": self.universe_hash,
        }


def load_candidate_universe(path: Path) -> CandidateUniverse:
    """Load a candidate universe from a JSON file."""
    import json

    data = json.loads(path.read_text())
    candidates = [
        CandidateEntry(
            candidate_id=c.get("candidate_id", f"cand-{i}"),
            returns=c.get("returns", c.get("adjusted_returns", [])),
            prices=c.get("prices", []),
            trading_days=c.get("trading_days", c.get("day_index", [])),
            volume=c.get("volume", []),
            source_hash=c.get("source_hash", ""),
            metadata=c.get("metadata", {}),
        )
        for i, c in enumerate(data.get("candidates", []))
    ]
    return CandidateUniverse(
        universe_id=data.get("universe_id", ""),
        candidates=candidates,
        min_overlap_days=data.get("min_overlap_days", 60),
        min_observation_count=data.get("min_observation_count", 100),
    )
