from __future__ import annotations

from fenrix_synthetic.masking.deterministic import MatchEntry
from fenrix_synthetic.masking.overlap import OverlapResolver


def make_match(
    span_id: str,
    start: int,
    end: int,
    priority: int = 100,
    alias_id: str = "ali-001",
    match_policy: str = "literal",
) -> MatchEntry:
    return MatchEntry(
        span_id=span_id,
        document_artifact_id="doc-001",
        original_start=start,
        original_end=end,
        entity_id="ent-001",
        alias_id=alias_id,
        entity_type="company",
        match_policy=match_policy,
        priority=priority,
        matched_text="x" * (end - start),
        replacement="Company 001",
    )


class TestOverlapResolver:
    def test_no_overlap(self):
        resolver = OverlapResolver()
        matches = [
            make_match("s1", 0, 10, priority=100),
            make_match("s2", 20, 30, priority=100),
        ]
        accepted, rejected = resolver.resolve(matches)
        assert len(accepted) == 2
        assert len(rejected) == 0

    def test_exact_overlap(self):
        resolver = OverlapResolver()
        matches = [
            make_match("s1", 0, 10, priority=200),
            make_match("s2", 0, 10, priority=100),
        ]
        accepted, rejected = resolver.resolve(matches)
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert accepted[0].span_id == "s1"

    def test_partial_overlap(self):
        resolver = OverlapResolver()
        matches = [
            make_match("s1", 0, 15, priority=100),
            make_match("s2", 10, 25, priority=100),
        ]
        accepted, rejected = resolver.resolve(matches)
        assert accepted[0].span_id == "s1"
        assert rejected[0].span_id == "s2"

    def test_nested_overlap(self):
        resolver = OverlapResolver()
        matches = [
            make_match("short", 0, 10, priority=100),
            make_match("long", 0, 20, priority=100),
        ]
        accepted, rejected = resolver.resolve(matches)
        assert accepted[0].span_id == "long"
        assert rejected[0].span_id == "short"

    def test_longest_span_wins(self):
        resolver = OverlapResolver()
        matches = [
            make_match("short", 0, 10, priority=100),
            make_match("long", 0, 20, priority=100),
        ]
        accepted, rejected = resolver.resolve(matches)
        assert accepted[0].span_id == "long"
        assert rejected[0].span_id == "short"

    def test_specificity_resolves_same_length(self):
        resolver = OverlapResolver()
        matches = [
            make_match("general", 0, 10, priority=100, match_policy="literal"),
            make_match("specific", 0, 10, priority=100, match_policy="url"),
        ]
        accepted, rejected = resolver.resolve(matches)
        assert accepted[0].span_id == "specific"

    def test_priority_overrides_length(self):
        resolver = OverlapResolver()
        matches = [
            make_match("high_short", 0, 5, priority=200),
            make_match("low_long", 0, 20, priority=50),
        ]
        accepted, rejected = resolver.resolve(matches)
        assert accepted[0].span_id == "high_short"

    def test_sorted_by_start_position(self):
        resolver = OverlapResolver()
        matches = [
            make_match("s2", 15, 25, priority=100),
            make_match("s1", 0, 10, priority=100),
            make_match("s3", 30, 40, priority=100),
        ]
        accepted, rejected = resolver.resolve(matches)
        assert [a.span_id for a in accepted] == ["s1", "s2", "s3"]

    def test_stable_tiebreak(self):
        resolver = OverlapResolver()
        matches = [
            make_match("a", 0, 10, priority=100, alias_id="ali-002"),
            make_match("b", 0, 10, priority=100, alias_id="ali-001"),
        ]
        accepted, rejected = resolver.resolve(matches)
        # ali-001 < ali-002 in string sort
        assert accepted[0].alias_id == "ali-001"

    def test_three_way_conflict(self):
        resolver = OverlapResolver()
        matches = [
            make_match("s1", 0, 20, priority=100),
            make_match("s2", 5, 15, priority=100),
            make_match("s3", 10, 25, priority=100),
        ]
        accepted, rejected = resolver.resolve(matches)
        assert len(accepted) == 1
        assert len(rejected) == 2
        assert accepted[0].span_id == "s1"

    def test_non_overlapping_with_gap_second_shadows(self):
        resolver = OverlapResolver()
        matches = [
            make_match("s1", 0, 10, priority=100),
            make_match("s2", 10, 20, priority=100),  # adjacent, not overlapping
            make_match("s3", 15, 25, priority=100),  # overlaps with s2
        ]
        accepted, rejected = resolver.resolve(matches)
        assert "s1" in [a.span_id for a in accepted]
        assert "s2" in [a.span_id for a in accepted]
        assert "s3" in [r.span_id for r in rejected]
