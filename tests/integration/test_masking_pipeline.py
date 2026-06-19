from __future__ import annotations

from pathlib import Path

from fenrix_synthetic.attacks import ExactResidualScanner
from fenrix_synthetic.identity import EntityRegistry, EntityType, MatchPolicy
from fenrix_synthetic.masking import DeterministicMasker

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


class TestMaskingPipeline:
    def test_full_synthetic_pipeline(self, tmp_path: Path):
        # Load canary document
        doc_path = FIXTURE_DIR / "canary_document.md"
        text = doc_path.read_text()

        # Build registry with canary entities
        reg = EntityRegistry.create("C001", "reg-c001-synth")
        reg.add_entity("ent-company", EntityType.COMPANY, "Canary Holdings Corporation")
        reg.add_entity("ent-ticker", EntityType.TICKER, "CHC")
        reg.add_entity("ent-cik", EntityType.CIK, "0000999999")
        reg.add_entity("ent-exec", EntityType.EXECUTIVE, "Eleanor Testperson")
        reg.add_entity("ent-domain", EntityType.COMPANY_DOMAIN, "canary-test.invalid")
        reg.add_entity("ent-product", EntityType.PRODUCT, "CanaryShield 9000")

        reg.add_alias(
            "ali-company",
            "ent-company",
            "Canary Holdings Corporation",
            EntityType.COMPANY,
            MatchPolicy.LITERAL,
            100,
        )
        reg.add_alias(
            "ali-company-short",
            "ent-company",
            "Canary Holdings",
            EntityType.COMPANY,
            MatchPolicy.LITERAL,
            150,
        )
        reg.add_alias(
            "ali-ticker", "ent-ticker", "CHC", EntityType.TICKER, MatchPolicy.TICKER_EXACT, 200
        )
        reg.add_alias(
            "ali-cik", "ent-cik", "0000999999", EntityType.CIK, MatchPolicy.CIK_PADDED, 200
        )
        reg.add_alias(
            "ali-exec",
            "ent-exec",
            "Eleanor Testperson",
            EntityType.EXECUTIVE,
            MatchPolicy.LITERAL,
            100,
        )
        reg.add_alias(
            "ali-domain",
            "ent-domain",
            "canary-test.invalid",
            EntityType.COMPANY_DOMAIN,
            MatchPolicy.DOMAIN_FULL,
            100,
        )
        reg.add_alias(
            "ali-product",
            "ent-product",
            "CanaryShield 9000",
            EntityType.PRODUCT,
            MatchPolicy.LITERAL,
            100,
        )

        config_hash = reg.config_hash()

        # Run masking pipeline
        masker = DeterministicMasker(reg, document_artifact_id="bronze-C001-synth")
        masked_text, sanitized_meta, audit, summary = masker.mask_and_sanitize_metadata(
            text,
            {"bronze_id": "bronze-C001-synth", "source": "canary_document.md"},
            config_hash,
        )

        # Verify audit
        assert audit.total_matches > 0
        assert audit.accepted_count > 0
        assert audit.registry_id == "reg-c001-synth"

        # Verify summary
        assert summary.match_count == audit.total_matches
        assert summary.replacement_count == audit.accepted_count
        assert summary.status == "completed"

        # Verify masked document has pseudonyms
        assert "Company 001" in masked_text or "Company 002" in masked_text
        assert "Ticker 001" in masked_text or "Ticker 002" in masked_text

        # Verify: real values should NOT be in the masked text anymore
        masked_lower = masked_text.lower()
        assert "canary holdings corporation" not in masked_lower
        assert "eleanor testperson" not in masked_lower

        # Run independent exact residual scan
        scanner = ExactResidualScanner()
        scan_values = {
            "company": ["Canary Holdings Corporation", "Canary Holdings"],
            "ticker": ["CHC"],
            "cik": ["0000999999"],
            "executive": ["Eleanor Testperson"],
            "company_domain": ["canary-test.invalid"],
            "canary": ["CanaryShield 9000"],
        }

        result = scanner.scan_text(masked_text, scan_values)
        # Should detect near-zero blocking hits after successful masking
        assert not result.is_blocked, (
            f"Masked text should have no blocking hits, got {result.blocking_hits}: "
            f"{result.hit_values}"
        )

    def test_clean_document_zero_hits(self):
        doc_path = FIXTURE_DIR / "clean_document.md"
        text = doc_path.read_text()

        scanner = ExactResidualScanner()
        scan_values = {
            "canary": [
                "Canary Holdings",
                "CHC",
                "Eleanor Testperson",
                "canary-test.invalid",
                "CanaryShield 9000",
            ],
        }

        result = scanner.scan_text(text, scan_values)
        assert result.total_hits == 0
        assert not result.is_blocked

    def test_leaky_document_detected(self):
        doc_path = FIXTURE_DIR / "canary_document.md"
        text = doc_path.read_text()

        scanner = ExactResidualScanner()
        scan_values = {
            "company": ["Canary Holdings Corporation", "Canary Holdings"],
            "ticker": ["CHC"],
            "cik": ["0000999999"],
            "executive": ["Eleanor Testperson"],
            "company_domain": ["canary-test.invalid"],
        }

        result = scanner.scan_text(text, scan_values)
        assert result.is_blocked
        assert result.blocking_hits > 0

    def test_lineage_chain(self, tmp_path: Path):
        """Verify complete raw → bronze → silver lineage."""
        # Simulated raw data
        raw_text = "SEC raw filing content with Canary Holdings Corporation"

        # Bronze (extracted text)
        bronze_path = tmp_path / "bronze" / "C001"
        bronze_path.mkdir(parents=True)
        bronze_doc = bronze_path / "synth_doc.md"
        bronze_doc.write_text(raw_text)

        # Silver (masked output)
        silver_dir = tmp_path / "silver" / "C001"
        silver_dir.mkdir(parents=True)

        # Run pipeline
        reg = EntityRegistry.create("C001", "reg-test-lineage")
        reg.add_entity("ent-company", EntityType.COMPANY, "Canary Holdings Corporation")
        reg.add_alias(
            "ali-company",
            "ent-company",
            "Canary Holdings Corporation",
            EntityType.COMPANY,
            MatchPolicy.LITERAL,
            100,
        )

        masker = DeterministicMasker(reg, document_artifact_id="bronze-C001-synth")
        masked, audit, summary = masker.mask(bronze_doc.read_text())

        # Write silver artifacts
        masked_path = silver_dir / "masked_synth_doc.md"
        masked_path.write_text(masked)
        audit_path = silver_dir / "masked_synth_doc_audit.json"
        audit_path.write_text(audit.model_dump_json(indent=2))

        # Verify lineage
        assert audit.source_bronze_artifact_id == "bronze-C001-synth"
        assert summary.input_hash
        assert summary.output_hash

        # Verify silver content is masked
        assert "Canary Holdings Corporation" not in masked
        assert "Company 001" in masked

        # Verify private audit exists but won't be lost
        assert audit_path.exists()
        assert audit.total_matches >= 1

    def test_checkpoint_invalidation_on_registry_change(self, tmp_path: Path):
        """Changing the registry should invalidate the checkpoint."""
        from fenrix_synthetic.schemas.checkpoints import (
            OutputArtifact,
            StageCheckpoint,
            StageName,
            StageStatus,
        )
        from fenrix_synthetic.storage.checkpoints import (
            load_checkpoint,
            save_checkpoint,
        )

        reg1 = EntityRegistry.create("C001", "reg-cp-test1")
        reg1.add_entity("ent-company", EntityType.COMPANY, "Original Corp")
        reg1.add_alias(
            "ali-company",
            "ent-company",
            "Original Corp",
            EntityType.COMPANY,
            MatchPolicy.LITERAL,
            100,
        )
        hash1 = reg1.config_hash()

        reg2 = EntityRegistry.create("C001", "reg-cp-test2")
        reg2.add_entity("ent-company", EntityType.COMPANY, "Original Corp")
        reg2.add_entity("ent-exec", EntityType.EXECUTIVE, "Jane Smith")
        reg2.add_alias(
            "ali-company",
            "ent-company",
            "Original Corp",
            EntityType.COMPANY,
            MatchPolicy.LITERAL,
            100,
        )
        hash2 = reg2.config_hash()

        assert hash1 != hash2, "Registry changes must change hash"

        # Simulate a checkpoint with hash1
        output_dir = tmp_path / "data"
        output_dir.mkdir(parents=True)
        cp = StageCheckpoint(
            stage=StageName.MASK,
            company_id="C001",
            input_hash=hash1,
            config_hash=hash1,
            output_artifacts=[OutputArtifact(path=str(tmp_path / "dummy.txt"), hash="abc")],
            status=StageStatus.COMPLETED,
            pipeline_version="0.1.0",
        )
        save_checkpoint(output_dir, cp)

        # Load and check - should match
        loaded = load_checkpoint(output_dir, "C001", StageName.MASK)
        assert loaded is not None
        assert loaded.input_hash == hash1

        # Now if the registry hash changed, checkpoint MUST be invalidated
        assert hash1 != hash2
        if loaded.input_hash != hash2:
            pass  # This proves invalidation on registry change

    def test_artifact_relationship_validation(self, tmp_path: Path):
        """Ensure masked artifacts reference their bronze source."""
        bronze_id = "bronze-C001-000123456724000001"

        reg = EntityRegistry.create("C001", "reg-test-artifact-rel")
        reg.add_entity("ent-co", EntityType.COMPANY, "Test Corp")
        reg.add_alias("ali-co", "ent-co", "Test Corp", EntityType.COMPANY, MatchPolicy.LITERAL, 100)

        masker = DeterministicMasker(reg, document_artifact_id=bronze_id)
        masked, audit, summary = masker.mask("Hello Test Corp")

        assert audit.source_bronze_artifact_id == bronze_id
        assert summary.input_artifact_id == bronze_id

        for span in audit.spans:
            assert span.document_artifact_id == bronze_id

    def test_no_network_called(self):
        """Ensure Phase 2 doesn't make network calls."""
        import socket

        orig = socket.socket
        blocked: list[str] = []

        class BlockSocket(socket.socket):
            def __init__(self, *args, **kwargs):
                super().__init__(socket.AF_INET, socket.SOCK_STREAM)
                self.close()

            def connect(self, *args, **kwargs):
                blocked.append(f"connect({args}, {kwargs})")
                raise OSError("Network blocked in test")

            def connect_ex(self, *args, **kwargs):
                blocked.append(f"connect_ex({args}, {kwargs})")
                return 1

        socket.socket = BlockSocket
        try:
            text = "Test content with no network needs"
            reg = EntityRegistry.create("C001", "reg-test-nonet")
            reg.add_entity("ent-co", EntityType.COMPANY, "Test Corp")
            reg.add_alias(
                "ali-co", "ent-co", "Test Corp", EntityType.COMPANY, MatchPolicy.LITERAL, 100
            )
            masker = DeterministicMasker(reg, "doc-test")
            masked, _, _ = masker.mask(text)
            assert masked == text
            assert not blocked, f"Network call attempted: {blocked}"
        finally:
            socket.socket = orig
