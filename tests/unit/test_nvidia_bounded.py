"""Tests for bounded NVIDIA review: NVIDIABounds, RiskChunkSelector, gated flow."""

import sys

import pytest

sys.path.insert(0, "src")

from fenrix_synthetic.providers.nvidia_client import NVIDIABounds  # noqa: E402
from fenrix_synthetic.providers.nvidia_risk import RiskChunkSelector  # noqa: E402


def test_smoke_bounds_have_small_caps() -> None:
    b = NVIDIABounds.smoke()
    assert b.mode == "smoke"
    assert b.max_artifacts_per_run == 1
    assert b.max_chunks_reviewed_per_artifact == 6
    assert b.max_chunks_rewritten_per_artifact == 4


def test_final_bounds_have_larger_caps() -> None:
    b = NVIDIABounds.final_submission()
    assert b.mode == "final_submission"
    assert b.max_artifacts_per_run == 3
    assert b.max_chunks_reviewed_per_artifact == 12
    assert b.max_chunks_rewritten_per_artifact == 8


def test_risk_selector_ranks_head_chunk() -> None:
    selector = RiskChunkSelector(None)
    chunks = [
        {"chunk_id": 0, "start": 0, "end": 100, "text": "Plain header content."},
        {"chunk_id": 1, "start": 100, "end": 200, "text": "Also plain."},
        {"chunk_id": 2, "start": 200, "end": 300, "text": "Still plain."},
    ]
    report = selector.rank(chunks, leaked_clues=[], max_chunks=3)

    # Chunk 0 should be selected (structural_head has score 3)
    assert 0 in report.ranked_indices
    # At least one chunk should have risk score > 0
    assert report.risk_chunks_total >= 1
    assert any(s["chunk_index"] == 0 for s in report.scored_chunks)
    scored0 = next(s for s in report.scored_chunks if s["chunk_index"] == 0)
    assert scored0["risk_score"] >= 3.0
    assert "structural_head" in scored0["matched_reasons"]


def test_risk_selector_picks_chunks_with_leaked_clues() -> None:
    selector = RiskChunkSelector(None)
    chunks = [
        {"chunk_id": 0, "start": 0, "end": 100, "text": "Header"},
        {"chunk_id": 1, "start": 100, "end": 200, "text": "Generic body text"},
        {"chunk_id": 2, "start": 200, "end": 300, "text": "References Company XYZ product line"},
        {"chunk_id": 3, "start": 300, "end": 400, "text": "Bottom of the document"},
    ]
    report = selector.rank(chunks, leaked_clues=["Company XYZ"], max_chunks=3)
    # Chunk 0 (head) and Chunk 2 (contains leaked clue) should be selected
    assert 0 in report.ranked_indices
    assert 2 in report.ranked_indices


def test_risk_selector_respects_max_chunks() -> None:
    selector = RiskChunkSelector(None)
    chunks = [
        {"chunk_id": i, "start": i * 10, "end": (i + 1) * 10, "text": f"Header text {i}"}
        for i in range(10)
    ]
    report = selector.rank(chunks, leaked_clues=[], max_chunks=3)
    assert len(report.ranked_indices) <= 3
    assert report.chunks_skipped_due_to_cap >= 0


def test_risk_selector_detects_direct_patterns() -> None:
    selector = RiskChunkSelector(None)
    chunks = [
        {"chunk_id": 0, "start": 0, "end": 50, "text": "Header"},
        {"chunk_id": 1, "start": 50, "end": 200, "text": "CIK: 0001045810 found here"},
    ]
    report = selector.rank(chunks, leaked_clues=[], max_chunks=5)
    # Chunk 0 (head) and Chunk 1 (CIK pattern) should be selected
    assert 0 in report.ranked_indices
    assert 1 in report.ranked_indices


@pytest.mark.skip(reason="covered by integration smoke; requires populated atlas")
def test_risk_selector_with_registry_alias() -> None:
    """Covered by orchestrator smoke with populated NVDA atlas."""
    pass


def test_bounds_final_bounded_total_calls() -> None:
    """Final-submission defaults should cap API calls to a viable total.
    Worst case per artifact: 1 (attacker-summary) + max_chunks_rewritten + 1 (reattack).
    That's 10 calls per artifact - bounded amount."""
    b = NVIDIABounds.final_submission()
    per_artifact_max_calls = 1 + b.max_chunks_rewritten_per_artifact + 1
    total_max_calls = per_artifact_max_calls * b.max_artifacts_per_run
    # Should be far less than the unbounded 140+ chunks per 1.1MB filing
    assert total_max_calls < 100


def test_bounds_smoke_bounded_total_calls() -> None:
    b = NVIDIABounds.smoke()
    per_artifact_max_calls = 1 + b.max_chunks_rewritten_per_artifact + 1
    total_max_calls = per_artifact_max_calls * b.max_artifacts_per_run
    assert total_max_calls < 10  # Smoke must finish in seconds


def test_risk_selector_zero_score_excluded() -> None:
    selector = RiskChunkSelector(None)
    chunks = [
        {"chunk_id": 0, "start": 0, "end": 50, "text": "Header"},
        {"chunk_id": 1, "start": 50, "end": 100, "text": "Generic body"},  # no head, no clue
        {"chunk_id": 2, "start": 100, "end": 150, "text": "More generic"},  # no head, no clue
    ]
    report = selector.rank(chunks, leaked_clues=[], max_chunks=10)
    # Chunk 0 (head) is selected; chunks 1, 2 have zero risk score so excluded
    assert 0 in report.ranked_indices
    assert 1 not in report.ranked_indices
    assert 2 not in report.ranked_indices
    assert report.risk_chunks_total == 1


if __name__ == "__main__":
    test_smoke_bounds_have_small_caps()
    test_final_bounds_have_larger_caps()
    test_risk_selector_ranks_head_chunk()
    test_risk_selector_picks_chunks_with_leaked_clues()
    test_risk_selector_respects_max_chunks()
    test_risk_selector_detects_direct_patterns()
    test_risk_selector_with_registry_alias()
    test_bounds_final_bounded_total_calls()
    test_bounds_smoke_bounded_total_calls()
    test_risk_selector_zero_score_excluded()
    print("All bounded-review tests passed.")
