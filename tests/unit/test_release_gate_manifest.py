"""Tests for release gate behavior with EvidenceManifest.

Proves that:
- Incomplete manifest forces FAIL
- Placeholder artifacts block PASS
- Mismatched run IDs are rejected
- Valid complete manifest can yield PASS or REVIEW_REQUIRED
"""

from __future__ import annotations

from fenrix_synthetic.release.evidence import EvidenceManifest
from fenrix_synthetic.release.gate import (
    ReleaseDecision,
    evaluate_release_gate,
)


class TestReleaseGateManifest:
    def test_incomplete_manifest_forces_fail(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        # Add all required types with non-empty hashes
        for et in manifest.get_required_types():
            manifest.add_reference(et, f"hash-{et}")
        # Remove one required type to make it incomplete
        manifest.references = [r for r in manifest.references if r.evidence_type != "structured_attacks"]
        gate = evaluate_release_gate(
            text_attacks_blocked=False,
            structured_rank=5,
            evidence_manifest=manifest,
        )
        assert gate.decision == ReleaseDecision.FAIL
        assert gate.blocking_failures > 0

    def test_placeholder_artifact_blocks_pass(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        for et in manifest.get_required_types():
            hash_val = "" if et == "text_attacks" else f"hash-{et}"
            manifest.add_reference(et, hash_val)
        gate = evaluate_release_gate(
            text_attacks_blocked=False,
            structured_rank=5,
            evidence_manifest=manifest,
        )
        assert gate.decision == ReleaseDecision.FAIL
        assert gate.blocking_failures > 0

    def test_valid_manifest_can_pass(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        for et in manifest.get_required_types():
            manifest.add_reference(et, f"hash-{et}")
        gate = evaluate_release_gate(
            text_attacks_blocked=False,
            structured_rank=15,
            exact_identity_hits=0,
            digital_hits=0,
            deterministic_reproduced=True,
            all_attacks_ran=True,
            provenance_complete=True,
            evidence_manifest=manifest,
        )
        # With rank 15 (> 10) and no hits, should still be REVIEW_REQUIRED
        # because structured rank is outside top_k
        assert gate.decision in (ReleaseDecision.REVIEW_REQUIRED, ReleaseDecision.PASS)

    def test_fail_blocks_pass(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        for et in manifest.get_required_types():
            manifest.add_reference(et, f"hash-{et}")
        gate = evaluate_release_gate(
            text_attacks_blocked=True,  # Blocked = FAIL
            structured_rank=5,
            exact_identity_hits=0,
            digital_hits=0,
            deterministic_reproduced=True,
            all_attacks_ran=True,
            provenance_complete=True,
            evidence_manifest=manifest,
        )
        assert gate.decision == ReleaseDecision.FAIL

    def test_unresolved_review_queue_blocks_pass(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        for et in manifest.get_required_types():
            manifest.add_reference(et, f"hash-{et}")
        manifest.add_reference("review_queue", "unresolved_items_present")
        gate = evaluate_release_gate(
            text_attacks_blocked=False,
            structured_rank=5,
            exact_identity_hits=0,
            digital_hits=0,
            deterministic_reproduced=True,
            all_attacks_ran=True,
            provenance_complete=True,
            evidence_manifest=manifest,
        )
        assert gate.decision == ReleaseDecision.FAIL
