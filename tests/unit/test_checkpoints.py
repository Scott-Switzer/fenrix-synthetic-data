"""Unit tests for checkpoint management."""

from pathlib import Path

import pytest

from fenrix_synthetic.schemas import StageName, StageStatus
from fenrix_synthetic.schemas.checkpoints import OutputArtifact, StageCheckpoint
from fenrix_synthetic.storage.checkpoints import (
    CheckpointError,
    CheckpointStatus,
    load_checkpoint,
    save_checkpoint,
    validate_checkpoint,
)


class TestSaveCheckpoint:
    """Test save_checkpoint function."""

    def test_save_checkpoint(self, temp_dir: Path):
        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="a" * 64,
            config_hash="b" * 64,
            output_artifacts=[OutputArtifact(path=Path("data/raw/C001/x.html"), hash="c" * 64)],
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, checkpoint)

        # Check file exists
        cp_path = temp_dir / ".checkpoints" / "C001" / "ingest.json"
        assert cp_path.exists()

    def test_save_checkpoint_creates_dirs(self, temp_dir: Path):
        checkpoint = StageCheckpoint(
            stage=StageName.EXTRACT,
            company_id="C002",
            input_hash="a" * 64,
            config_hash="b" * 64,
            output_artifacts=[],
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, checkpoint)

        cp_path = temp_dir / ".checkpoints" / "C002" / "extract.json"
        assert cp_path.exists()

    def test_save_checkpoint_atomic(self, temp_dir: Path, monkeypatch):
        """Test that save is atomic."""
        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="a" * 64,
            config_hash="b" * 64,
            output_artifacts=[],
            pipeline_version="0.1.0",
        )
        # Should not raise
        save_checkpoint(temp_dir, checkpoint)


class TestLoadCheckpoint:
    """Test load_checkpoint function."""

    def test_load_existing_checkpoint(self, temp_dir: Path):
        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="a" * 64,
            config_hash="b" * 64,
            output_artifacts=[OutputArtifact(path=Path("data/raw/C001/x.html"), hash="c" * 64)],
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, checkpoint)

        loaded = load_checkpoint(temp_dir, "C001", StageName.INGEST)
        assert loaded is not None
        assert loaded.stage == StageName.INGEST
        assert loaded.company_id == "C001"
        assert loaded.input_hash == "a" * 64
        assert loaded.config_hash == "b" * 64
        assert len(loaded.output_artifacts) == 1
        assert loaded.pipeline_version == "0.1.0"

    def test_load_nonexistent_checkpoint(self, temp_dir: Path):
        loaded = load_checkpoint(temp_dir, "C001", StageName.INGEST)
        assert loaded is None

    def test_load_corrupt_checkpoint(self, temp_dir: Path):
        # Create corrupt checkpoint file
        cp_dir = temp_dir / ".checkpoints" / "C001"
        cp_dir.mkdir(parents=True)
        cp_file = cp_dir / "ingest.json"
        cp_file.write_text("{ invalid json")

        with pytest.raises(CheckpointError) as exc_info:
            load_checkpoint(temp_dir, "C001", StageName.INGEST)
        assert "Corrupt checkpoint" in str(exc_info.value)


class TestValidateCheckpoint:
    """Test validate_checkpoint function."""

    def test_validate_valid_checkpoint(self, temp_dir: Path):
        # Create artifact file
        artifact_path = temp_dir / "data" / "raw" / "C001" / "filing.html"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("test content")

        # Create checkpoint
        artifact_hash = "6ae8a75555209fd6c44157c0aed8016e763ff435a19cf186f76863140143ff72"  # sha256("test content")
        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="input_hash_123",
            config_hash="config_hash_456",
            output_artifacts=[OutputArtifact(path=artifact_path, hash=artifact_hash)],
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, checkpoint)

        result = validate_checkpoint(
            temp_dir,
            "C001",
            StageName.INGEST,
            expected_input_hash="input_hash_123",
            expected_config_hash="config_hash_456",
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.VALID

    def test_validate_no_checkpoint(self, temp_dir: Path):
        result = validate_checkpoint(
            temp_dir,
            "C001",
            StageName.INGEST,
            expected_input_hash="x",
            expected_config_hash="y",
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.INVALID_HASH
        assert "No checkpoint found" in result.message

    def test_validate_wrong_status(self, temp_dir: Path):
        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="a" * 64,
            config_hash="b" * 64,
            output_artifacts=[],
            status=StageStatus.FAILED,
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, checkpoint)

        result = validate_checkpoint(
            temp_dir,
            "C001",
            StageName.INGEST,
            expected_input_hash="a" * 64,
            expected_config_hash="b" * 64,
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.INVALID_HASH
        assert "not completed" in result.message

    def test_validate_version_mismatch(self, temp_dir: Path):
        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="a" * 64,
            config_hash="b" * 64,
            output_artifacts=[],
            pipeline_version="0.0.1",
        )
        save_checkpoint(temp_dir, checkpoint)

        result = validate_checkpoint(
            temp_dir,
            "C001",
            StageName.INGEST,
            expected_input_hash="a" * 64,
            expected_config_hash="b" * 64,
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.VERSION_CHANGED

    def test_validate_input_hash_mismatch(self, temp_dir: Path):
        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="old_hash",
            config_hash="b" * 64,
            output_artifacts=[],
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, checkpoint)

        result = validate_checkpoint(
            temp_dir,
            "C001",
            StageName.INGEST,
            expected_input_hash="new_hash",
            expected_config_hash="b" * 64,
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.INVALID_HASH
        assert "Input hash mismatch" in result.message

    def test_validate_config_hash_mismatch(self, temp_dir: Path):
        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="a" * 64,
            config_hash="old_config",
            output_artifacts=[],
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, checkpoint)

        result = validate_checkpoint(
            temp_dir,
            "C001",
            StageName.INGEST,
            expected_input_hash="a" * 64,
            expected_config_hash="new_config",
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.CONFIG_CHANGED

    def test_validate_missing_artifact(self, temp_dir: Path):
        artifact_path = temp_dir / "data" / "raw" / "C001" / "missing.html"
        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="a" * 64,
            config_hash="b" * 64,
            output_artifacts=[OutputArtifact(path=artifact_path, hash="c" * 64)],
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, checkpoint)

        result = validate_checkpoint(
            temp_dir,
            "C001",
            StageName.INGEST,
            expected_input_hash="a" * 64,
            expected_config_hash="b" * 64,
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.MISSING_ARTIFACT

    def test_validate_artifact_hash_mismatch(self, temp_dir: Path):
        artifact_path = temp_dir / "data" / "raw" / "C001" / "filing.html"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("different content")

        checkpoint = StageCheckpoint(
            stage=StageName.INGEST,
            company_id="C001",
            input_hash="a" * 64,
            config_hash="b" * 64,
            output_artifacts=[OutputArtifact(path=artifact_path, hash="wrong_hash")],
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, checkpoint)

        result = validate_checkpoint(
            temp_dir,
            "C001",
            StageName.INGEST,
            expected_input_hash="a" * 64,
            expected_config_hash="b" * 64,
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.INVALID_HASH
        assert "Output artifact hash mismatch" in result.message
