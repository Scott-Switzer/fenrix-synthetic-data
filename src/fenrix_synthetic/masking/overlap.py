from __future__ import annotations

from .deterministic import MatchEntry


class OverlapResolver:
    def resolve(
        self,
        matches: list[MatchEntry],
    ) -> tuple[list[MatchEntry], list[MatchEntry]]:
        sorted_matches = sorted(
            matches,
            key=lambda m: (
                -m.priority,
                -(m.original_end - m.original_start),
                -self._specificity(m.match_policy),
                m.original_start,
                m.alias_id,
            ),
        )

        accepted: list[MatchEntry] = []
        rejected: list[MatchEntry] = []

        used_ranges: list[tuple[int, int]] = []

        for m in sorted_matches:
            if self._overlaps(m.original_start, m.original_end, used_ranges):
                rejected.append(m)
            else:
                accepted.append(m)
                used_ranges.append((m.original_start, m.original_end))

        # Sort accepted by start position
        accepted.sort(key=lambda m: m.original_start)
        return accepted, rejected

    def _overlaps(
        self,
        start: int,
        end: int,
        used: list[tuple[int, int]],
    ) -> bool:
        for us, ue in used:
            if start < ue and end > us:
                return True
        return False

    @staticmethod
    def _specificity(match_policy: str) -> int:
        order = {
            "url": 10,
            "email": 9,
            "ticker_exchange": 8,
            "ticker_parenthesized": 7,
            "ticker": 6,
            "cik_padded": 6,
            "accession": 6,
            "domain": 5,
            "possessive": 4,
            "dash_variant": 3,
            "space_variant": 3,
            "literal": 2,
        }
        return order.get(match_policy, 0)
