"""Tests for extraction checkpoint and resume behavior."""

from pathlib import Path

from fenrix_synthetic.schemas import StageName, StageStatus
from fenrix_synthetic.schemas.checkpoints import (
    CheckpointStatus,
    OutputArtifact,
    StageCheckpoint,
)
from fenrix_synthetic.storage.checkpoints import (
    save_checkpoint,
    validate_checkpoint,
)
from fenrix_synthetic.storage.checksums import compute_file_hash

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sec"
FIXTURE_CONFIG = Path(__file__).parent.parent / "fixtures" / "test_company.yaml"


class TestCheckpointExtraction:
    """Test checkpoint behavior with extraction pipeline."""

    def test_first_execution_performs_work(self, temp_dir: Path):
        """First run: no checkpoint, so it should need to run."""
        # Simulate running the extract pipeline
        raw_path = temp_dir / "raw" / "C001" / "test.html"
        raw_path.parent.mkdir(parents=True)
        raw_path.write_text("<html><body><p>test</p></body></html>")
        sha256 = compute_file_hash(raw_path)

        cp = StageCheckpoint(
            stage=StageName.EXTRACT,
            company_id="C001",
            input_hash="test_input_hash",
            config_hash="test_config_hash",
            output_artifacts=[OutputArtifact(path=raw_path, hash=sha256)],
            status=StageStatus.COMPLETED,
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, cp)

        result = validate_checkpoint(
            temp_dir,
            "C001",
            StageName.EXTRACT,
            expected_input_hash="test_input_hash",
            expected_config_hash="test_config_hash",
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.VALID

    def test_second_execution_reuses_valid_artifacts(self, temp_dir: Path):
        """Second run: checkpoint valid → should validate."""
        raw_path = temp_dir / "raw" / "C002" / "test.html"
        raw_path.parent.mkdir(parents=True)
        raw_path.write_text("content")
        sha256 = compute_file_hash(raw_path)

        cp = StageCheckpoint(
            stage=StageName.EXTRACT,
            company_id="C002",
            input_hash="hash_v1",
            config_hash="config_v1",
            output_artifacts=[OutputArtifact(path=raw_path, hash=sha256)],
            status=StageStatus.COMPLETED,
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, cp)

        # Same inputs → valid
        result = validate_checkpoint(
            temp_dir,
            "C002",
            StageName.EXTRACT,
            expected_input_hash="hash_v1",
            expected_config_hash="config_v1",
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.VALID

    def test_changed_source_hash_forces_rerun(self, temp_dir: Path):
        raw_path = temp_dir / "raw" / "C003" / "test.html"
        raw_path.parent.mkdir(parents=True)
        raw_path.write_text("old content")
        sha256 = compute_file_hash(raw_path)

        cp = StageCheckpoint(
            stage=StageName.EXTRACT,
            company_id="C003",
            input_hash="old_hash",
            config_hash="config_v1",
            output_artifacts=[OutputArtifact(path=raw_path, hash=sha256)],
            status=StageStatus.COMPLETED,
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, cp)

        # Different input hash → invalid
        result = validate_checkpoint(
            temp_dir,
            "C003",
            StageName.EXTRACT,
            expected_input_hash="new_hash",
            expected_config_hash="config_v1",
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.INVALID_HASH

    def test_changed_config_forces_rerun(self, temp_dir: Path):
        raw_path = temp_dir / "raw" / "C004" / "test.html"
        raw_path.parent.mkdir(parents=True)
        raw_path.write_text("content")
        sha256 = compute_file_hash(raw_path)

        cp = StageCheckpoint(
            stage=StageName.EXTRACT,
            company_id="C004",
            input_hash="hash_v1",
            config_hash="old_config",
            output_artifacts=[OutputArtifact(path=raw_path, hash=sha256)],
            status=StageStatus.COMPLETED,
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, cp)

        # Config changed
        result = validate_checkpoint(
            temp_dir,
            "C004",
            StageName.EXTRACT,
            expected_input_hash="hash_v1",
            expected_config_hash="new_config",
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.CONFIG_CHANGED

    def test_missing_output_forces_rerun(self, temp_dir: Path):
        cp = StageCheckpoint(
            stage=StageName.EXTRACT,
            company_id="C005",
            input_hash="hash_v1",
            config_hash="config_v1",
            output_artifacts=[OutputArtifact(path=temp_dir / "nonexistent.html", hash="x" * 64)],
            status=StageStatus.COMPLETED,
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, cp)

        result = validate_checkpoint(
            temp_dir,
            "C005",
            StageName.EXTRACT,
            expected_input_hash="hash_v1",
            expected_config_hash="config_v1",
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.MISSING_ARTIFACT

    def test_corrupted_output_prevents_reuse(self, temp_dir: Path):
        raw_path = temp_dir / "raw" / "C006" / "test.html"
        raw_path.parent.mkdir(parents=True)
        raw_path.write_text("original content")
        wrong_hash = "a" * 64

        cp = StageCheckpoint(
            stage=StageName.EXTRACT,
            company_id="C006",
            input_hash="hash_v1",
            config_hash="config_v1",
            output_artifacts=[OutputArtifact(path=raw_path, hash=wrong_hash)],
            status=StageStatus.COMPLETED,
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, cp)

        # Artifact exists but hash doesn't match
        result = validate_checkpoint(
            temp_dir,
            "C006",
            StageName.EXTRACT,
            expected_input_hash="hash_v1",
            expected_config_hash="config_v1",
            expected_version="0.1.0",
        )
        assert result.status == CheckpointStatus.INVALID_HASH

    def test_failed_stage_not_completed(self, temp_dir: Path):
        cp = StageCheckpoint(
            stage=StageName.EXTRACT,
            company_id="C007",
            input_hash="hash_v1",
            config_hash="config_v1",
            output_artifacts=[],
            status=StageStatus.FAILED,
            pipeline_version="0.1.0",
        )
        save_checkpoint(temp_dir, cp)

        result = validate_checkpoint(
            temp_dir,
            "C007",
            StageName.EXTRACT,
            expected_input_hash="hash_v1",
            expected_config_hash="config_v1",
            expected_version="0.1.0",
        )
        assert result.status != CheckpointStatus.VALID
        assert "not completed" in result.message
