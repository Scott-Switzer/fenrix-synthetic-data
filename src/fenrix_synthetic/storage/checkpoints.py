"""Checkpoint management for stage resume behavior."""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import orjson

from ..schemas import StageName, StageStatus
from ..schemas.checkpoints import (
    CheckpointStatus,
    CheckpointValidationResult,
    OutputArtifact,
    StageCheckpoint,
)
from .hashing import hash_file


class CheckpointError(Exception):
    """Checkpoint-related errors."""

    pass


def _checkpoint_dir(base_path: Path, company_id: str) -> Path:
    """Get checkpoint directory for a company."""
    return base_path / ".checkpoints" / company_id


def _checkpoint_path(base_path: Path, company_id: str, stage: StageName) -> Path:
    """Get checkpoint file path for a stage."""
    return _checkpoint_dir(base_path, company_id) / f"{stage.value}.json"


def save_checkpoint(
    base_path: Path,
    checkpoint: StageCheckpoint,
) -> None:
    """Save stage checkpoint atomically."""
    path = _checkpoint_path(base_path, checkpoint.company_id, checkpoint.stage)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to dict for serialization
    data = {
        "stage": checkpoint.stage.value,
        "company_id": checkpoint.company_id,
        "input_hash": checkpoint.input_hash,
        "config_hash": checkpoint.config_hash,
        "output_artifacts": [
            {"path": str(a.path), "hash": a.hash} for a in checkpoint.output_artifacts
        ],
        "status": checkpoint.status.value,
        "completed_at": checkpoint.completed_at.isoformat(),
        "pipeline_version": checkpoint.pipeline_version,
        "metadata": checkpoint.metadata,
    }

    # Atomic write
    content = orjson.dumps(data, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.name + ".")
    try:
        os.write(fd, content)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_checkpoint(
    base_path: Path,
    company_id: str,
    stage: StageName,
) -> StageCheckpoint | None:
    """Load stage checkpoint if it exists."""
    path = _checkpoint_path(base_path, company_id, stage)
    if not path.exists():
        return None

    try:
        with open(path, "rb") as f:
            data = orjson.loads(f.read())
    except orjson.JSONDecodeError as e:
        raise CheckpointError(f"Corrupt checkpoint at {path}: {e}") from e

    # Reconstruct checkpoint
    return StageCheckpoint(
        stage=StageName(data["stage"]),
        company_id=data["company_id"],
        input_hash=data["input_hash"],
        config_hash=data["config_hash"],
        output_artifacts=[
            OutputArtifact(path=Path(a["path"]), hash=a["hash"]) for a in data["output_artifacts"]
        ],
        status=StageStatus(data["status"]),
        completed_at=datetime.fromisoformat(data["completed_at"]),
        pipeline_version=data["pipeline_version"],
        metadata=data.get("metadata", {}),
    )


def validate_checkpoint(
    base_path: Path,
    company_id: str,
    stage: StageName,
    expected_input_hash: str,
    expected_config_hash: str,
    expected_version: str,
) -> CheckpointValidationResult:
    """Validate a checkpoint against expected inputs.

    Returns CheckpointValidationResult with status and details.
    """
    checkpoint = load_checkpoint(base_path, company_id, stage)

    if checkpoint is None:
        return CheckpointValidationResult(
            stage=stage,
            company_id=company_id,
            status=CheckpointStatus.INVALID_HASH,
            message="No checkpoint found",
        )

    if checkpoint.status != StageStatus.COMPLETED:
        return CheckpointValidationResult(
            stage=stage,
            company_id=company_id,
            status=CheckpointStatus.INVALID_HASH,
            message=f"Checkpoint status is {checkpoint.status.value}, not completed",
        )

    if checkpoint.pipeline_version != expected_version:
        return CheckpointValidationResult(
            stage=stage,
            company_id=company_id,
            status=CheckpointStatus.VERSION_CHANGED,
            message=f"Pipeline version mismatch: {checkpoint.pipeline_version} != {expected_version}",
        )

    if checkpoint.input_hash != expected_input_hash:
        return CheckpointValidationResult(
            stage=stage,
            company_id=company_id,
            status=CheckpointStatus.INVALID_HASH,
            message="Input hash mismatch",
            details={"expected": expected_input_hash, "actual": checkpoint.input_hash},
        )

    if checkpoint.config_hash != expected_config_hash:
        return CheckpointValidationResult(
            stage=stage,
            company_id=company_id,
            status=CheckpointStatus.CONFIG_CHANGED,
            message="Configuration hash mismatch",
            details={"expected": expected_config_hash, "actual": checkpoint.config_hash},
        )

    # Verify output artifacts exist and have correct hashes
    for artifact in checkpoint.output_artifacts:
        if not artifact.path.exists():
            return CheckpointValidationResult(
                stage=stage,
                company_id=company_id,
                status=CheckpointStatus.MISSING_ARTIFACT,
                message=f"Output artifact missing: {artifact.path}",
                details={"path": str(artifact.path)},
            )

        actual_hash = hash_file(artifact.path)
        if actual_hash != artifact.hash:
            return CheckpointValidationResult(
                stage=stage,
                company_id=company_id,
                status=CheckpointStatus.INVALID_HASH,
                message=f"Output artifact hash mismatch: {artifact.path}",
                details={
                    "path": str(artifact.path),
                    "expected": artifact.hash,
                    "actual": actual_hash,
                },
            )

    return CheckpointValidationResult(
        stage=stage,
        company_id=company_id,
        status=CheckpointStatus.VALID,
        message="Checkpoint valid",
    )
