"""Unit tests for schema models."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from fenrix_synthetic.schemas import (
    ArtifactType,
    BronzeManifest,
    CheckpointStatus,
    CheckpointValidationResult,
    CompanyConfig,
    OutputArtifact,
    RawManifest,
    SourceManifest,
    SourceProvenanceRecord,
    StageCheckpoint,
    StageName,
    StageStatus,
)
from fenrix_synthetic.storage.hashing import hash_object


class TestArtifactEnums:
    """Test artifact and stage enums."""

    def test_artifact_type_values(self):
        assert ArtifactType.SOURCE_HTML == "source_html"
        assert ArtifactType.RAW_MANIFEST == "raw_manifest"
        assert ArtifactType.BRONZE_TEXT == "bronze_text"
        assert ArtifactType.BRONZE_MANIFEST == "bronze_manifest"

    def test_stage_name_values(self):
        assert StageName.INGEST == "ingest"
        assert StageName.EXTRACT == "extract"
        assert StageName.MANIFEST == "manifest"

    def test_stage_status_values(self):
        assert StageStatus.PENDING == "pending"
        assert StageStatus.RUNNING == "running"
        assert StageStatus.COMPLETED == "completed"
        assert StageStatus.FAILED == "failed"
        assert StageStatus.BLOCKED == "blocked"
        assert StageStatus.SKIPPED == "skipped"


class TestCompanyConfig:
    """Test CompanyConfig schema."""

    def test_valid_config(self):
        config = CompanyConfig(
            company_id="C001",
            source_identity="HBAN",
        )
        assert config.company_id == "C001"
        assert config.source_identity == "HBAN"

    def test_config_with_custom_paths(self):
        config = CompanyConfig(
            company_id="C002",
            source_identity="TEST",
            data_root="/custom/data",
            raw_dir="/custom/data/raw",
            bronze_dir="/custom/data/bronze",
        )
        assert config.data_root == Path("/custom/data").resolve()
        assert config.raw_dir == Path("/custom/data/raw").resolve()
        assert config.bronze_dir == Path("/custom/data/bronze").resolve()

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            CompanyConfig()

    def test_missing_company_id(self):
        with pytest.raises(ValidationError):
            CompanyConfig(source_identity="HBAN")


class TestSourceManifest:
    """Test SourceManifest schema."""

    def test_valid_manifest(self):
        manifest = SourceManifest(
            company_id="C001",
            accession_number="0001234567-23-000001",
            filing_date="2023-01-15",
            form_type="10-K",
            primary_document_url="https://www.sec.gov/.../filing.html",
            local_path=Path("data/raw/C001/filing.html"),
            content_hash="a" * 64,
        )
        assert manifest.company_id == "C001"
        assert manifest.source == "sec"
        assert manifest.form_type == "10-K"

    def test_manifest_with_metadata(self):
        manifest = SourceManifest(
            company_id="C001",
            accession_number="0001234567-23-000001",
            filing_date="2023-01-15",
            form_type="10-K",
            primary_document_url="https://www.sec.gov/.../filing.html",
            local_path=Path("data/raw/C001/filing.html"),
            content_hash="a" * 64,
            metadata={"cik": "0001234567"},
        )
        assert manifest.metadata["cik"] == "0001234567"


class TestRawManifest:
    """Test RawManifest schema."""

    def test_valid_manifest(self):
        manifest = RawManifest(
            artifact_id="test-123",
            company_id="C001",
            source_manifest_hash="b" * 64,
            configuration_hash="c" * 64,
            content_hash="d" * 64,
            pipeline_version="0.1.0",
            original_filename="filing.html",
        )
        assert manifest.artifact_id == "test-123"
        assert manifest.artifact_type == "raw_manifest"
        assert manifest.stage == "ingest"
        assert manifest.status == "completed"


class TestBronzeManifest:
    """Test BronzeManifest schema."""

    def test_valid_manifest(self):
        manifest = BronzeManifest(
            artifact_id="test-456",
            company_id="C001",
            source_artifact_id="test-123",
            source_manifest_hash="b" * 64,
            configuration_hash="c" * 64,
            content_hash="e" * 64,
            pipeline_version="0.1.0",
        )
        assert manifest.artifact_id == "test-456"
        assert manifest.artifact_type == "bronze_manifest"
        assert manifest.stage == "extract"
        assert manifest.extraction_method == "html_to_text"
        assert manifest.sections == []
        assert manifest.diagnostics == {}


class TestSourceProvenanceRecord:
    """Test SourceProvenanceRecord schema."""

    def test_valid_record(self):
        record = SourceProvenanceRecord(
            source_repository="Project Portfolio Engine",
            source_path="src/ppe/utils/checksums.py",
            source_commit="a" * 40,
            original_responsibility="File checksums",
            reason_for_reuse="Avoid reimplementing proven utility",
            applicable_license="MIT",
        )
        assert record.source_repository == "Project Portfolio Engine"
        assert record.applicable_license == "MIT"
        assert record.tests_added == []


class TestCheckpointSchemas:
    """Test checkpoint-related schemas."""

    def test_output_artifact(self):
        artifact = OutputArtifact(
            path=Path("data/raw/C001/filing.html"),
            hash="a" * 64,
        )
        assert artifact.path == Path("data/raw/C001/filing.html")

    def test_stage_checkpoint(self):

        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="a" * 64,
            config_hash="b" * 64,
            output_artifacts=[OutputArtifact(path=Path("x"), hash="c" * 64)],
            pipeline_version="0.1.0",
        )
        assert checkpoint.stage == StageName.INGEST
        assert checkpoint.status == StageStatus.COMPLETED

    def test_checkpoint_status_enum(self):
        assert CheckpointStatus.VALID == "valid"
        assert CheckpointStatus.INVALID_HASH == "invalid_hash"
        assert CheckpointStatus.MISSING_ARTIFACT == "missing_artifact"
        assert CheckpointStatus.CONFIG_CHANGED == "config_changed"
        assert CheckpointStatus.VERSION_CHANGED == "version_changed"
        assert CheckpointStatus.CORRUPT == "corrupt"

    def test_validation_result(self):
        result = CheckpointValidationResult(
            stage=StageName.INGEST,
            company_id="C001",
            status=CheckpointStatus.VALID,
            message="All good",
        )
        assert result.status == CheckpointStatus.VALID
        assert result.details == {}


class TestDeterministicSerialization:
    """Test that schema serialization is deterministic."""

    def test_source_manifest_deterministic(self):
        manifest = SourceManifest(
            company_id="C001",
            accession_number="0001234567-23-000001",
            filing_date="2023-01-15",
            form_type="10-K",
            primary_document_url="https://www.sec.gov/.../filing.html",
            local_path=Path("data/raw/C001/filing.html"),
            content_hash="a" * 64,
        )
        # Serialize twice
        json1 = manifest.model_dump_json()
        json2 = manifest.model_dump_json()
        assert json1 == json2

    def test_hash_object_deterministic(self):
        obj = {"b": 2, "a": 1}
        hash1 = hash_object(obj)
        hash2 = hash_object(obj)
        assert hash1 == hash2
        assert len(hash1) == 64

    def test_hash_object_key_order_independent(self):
        obj1 = {"a": 1, "b": 2}
        obj2 = {"b": 2, "a": 1}
        assert hash_object(obj1) == hash_object(obj2)
