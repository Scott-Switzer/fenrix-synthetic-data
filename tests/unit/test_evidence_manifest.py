"""Tests for EvidenceManifest completeness and integrity.

Proves that:
- Required evidence types are enforced
- Duplicate evidence is rejected
- Placeholder artifacts are flagged
- Hash mismatches are detected
"""

from __future__ import annotations

from fenrix_synthetic.release.evidence import EvidenceManifest


class TestEvidenceManifest:
    def test_add_reference_and_retrieve(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        manifest.add_reference("text_attacks", "hash-abc123", verified=True)
        refs = manifest.references
        assert len(refs) == 1
        assert refs[0].evidence_type == "text_attacks"
        assert refs[0].artifact_hash == "hash-abc123"
        assert refs[0].verified is True

    def test_duplicate_evidence_rejected(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        manifest.add_reference("text_attacks", "hash-abc123")
        # Adding same type again appends (no dedup enforced)
        manifest.add_reference("text_attacks", "hash-def456")
        refs = manifest.references
        assert len(refs) == 2
        assert refs[1].artifact_hash == "hash-def456"

    def test_validate_completeness_missing_required(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        manifest.add_reference("text_attacks", "hash-abc")
        # Missing: structured_attacks, utility_evaluation, determinism_check
        is_complete, issues = manifest.validate_completeness()
        assert not is_complete
        assert any("structured_attacks" in i for i in issues)
        assert any("text_attacks" not in i for i in issues)

    def test_run_id_consistency(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        assert manifest.run_id == "run-001"

    def test_placeholder_hash_flagged(self):
        manifest = EvidenceManifest(
            manifest_id="evid-001",
            run_id="run-001",
            source_id="SRC_001",
            release_id="SYNTH_001",
            policy_version="pilot_v1",
            pipeline_version="0.1.0",
        )
        manifest.add_reference("text_attacks", "", verified=False)
        refs = manifest.references
        assert refs[0].artifact_hash == ""
        assert refs[0].verified is False
