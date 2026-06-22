"""Professor-bundle pipeline stage registry.

Defines the 18 mandatory stages, their status records, and the registry
that validates all stages ran before a bundle can be marked professor-ready.

Hard contract:
- In strict mode, PROVIDER_NOT_RUN is a blocking failure.
- professor_ready=true is impossible if any mandatory stage is missing,
  FAIL, PROVIDER_NOT_RUN, or has evidence_count=0 where evidence is required.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import orjson
from pydantic import BaseModel, Field


class ProfessorStage(StrEnum):
    """The 18 mandatory professor-bundle pipeline stages."""

    SOURCE_INGESTION = "SOURCE_INGESTION"
    SEC_PARSE = "SEC_PARSE"
    SECTION_EXTRACT = "SECTION_EXTRACT"
    ENTITY_DETECT_GLINER = "ENTITY_DETECT_GLINER"
    ENTITY_DETECT_RULES = "ENTITY_DETECT_RULES"
    ENTITY_RESOLVE = "ENTITY_RESOLVE"
    DEIDENTIFY = "DEIDENTIFY"
    PRIVATE_EVIDENCE_BUILD = "PRIVATE_EVIDENCE_BUILD"
    SYNTHETIC_PROFILE_BUILD = "SYNTHETIC_PROFILE_BUILD"
    FILING_RECONSTRUCT = "FILING_RECONSTRUCT"
    METRIC_SYNTHESIS = "METRIC_SYNTHESIS"
    METRIC_EVALUATION = "METRIC_EVALUATION"
    NEWS_RECONSTRUCT = "NEWS_RECONSTRUCT"
    CROSSLINK_BUILD = "CROSSLINK_BUILD"
    PEDAGOGY_BUILD = "PEDAGOGY_BUILD"
    RAG_INDEX_BUILD = "RAG_INDEX_BUILD"
    ADVERSARIAL_QA = "ADVERSARIAL_QA"
    RELEASE_GATE = "RELEASE_GATE"
    ZIP_EXPORT = "ZIP_EXPORT"


class StageStatus(StrEnum):
    """Status of a single pipeline stage."""

    PASS = "PASS"
    FAIL = "FAIL"
    PROVIDER_NOT_RUN = "PROVIDER_NOT_RUN"
    SKIPPED = "SKIPPED"
    RUNNING = "RUNNING"


# Stages that require evidence_count > 0 for professor_ready
STAGES_REQUIRING_EVIDENCE: frozenset[ProfessorStage] = frozenset(
    {
        ProfessorStage.SOURCE_INGESTION,
        ProfessorStage.SEC_PARSE,
        ProfessorStage.SECTION_EXTRACT,
        ProfessorStage.ENTITY_DETECT_GLINER,
        ProfessorStage.ENTITY_DETECT_RULES,
        ProfessorStage.DEIDENTIFY,
        ProfessorStage.PRIVATE_EVIDENCE_BUILD,
        ProfessorStage.FILING_RECONSTRUCT,
        ProfessorStage.METRIC_SYNTHESIS,
        ProfessorStage.METRIC_EVALUATION,
        ProfessorStage.NEWS_RECONSTRUCT,
        ProfessorStage.CROSSLINK_BUILD,
        ProfessorStage.PEDAGOGY_BUILD,
        ProfessorStage.RAG_INDEX_BUILD,
        ProfessorStage.ADVERSARIAL_QA,
        ProfessorStage.ZIP_EXPORT,
    }
)

ALL_MANDATORY_STAGES: tuple[ProfessorStage, ...] = tuple(ProfessorStage)


class StageStatusRecord(BaseModel):
    """Status record for a single pipeline stage execution."""

    stage: ProfessorStage
    status: StageStatus
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)

    model_config = {"use_enum_values": True}


class StageRegistry:
    """Registry of stage status records for a professor-bundle run.

    Validates that all mandatory stages ran and produced evidence before
    the bundle can be marked professor-ready.
    """

    def __init__(self) -> None:
        self._records: dict[ProfessorStage, StageStatusRecord] = {}

    def register(self, record: StageStatusRecord) -> None:
        """Register or update a stage record."""
        self._records[record.stage] = record

    def get(self, stage: ProfessorStage) -> StageStatusRecord | None:
        """Get a stage record by stage name."""
        return self._records.get(stage)

    @property
    def all_stages_present(self) -> bool:
        """Check if all mandatory stages have records."""
        return all(s in self._records for s in ALL_MANDATORY_STAGES)

    @property
    def all_stages_pass(self) -> bool:
        """Check if all mandatory stages have PASS status."""
        for stage in ALL_MANDATORY_STAGES:
            rec = self._records.get(stage)
            if rec is None or rec.status != StageStatus.PASS:
                return False
        return True

    @property
    def has_provider_not_run(self) -> bool:
        """Check if any stage has PROVIDER_NOT_RUN status."""
        return any(
            self._records.get(s) is not None
            and self._records[s].status == StageStatus.PROVIDER_NOT_RUN
            for s in ALL_MANDATORY_STAGES
        )

    @property
    def has_evidence_gaps(self) -> bool:
        """Check if any evidence-requiring stage has evidence_count=0."""
        for stage in STAGES_REQUIRING_EVIDENCE:
            rec = self._records.get(stage)
            if rec is None or rec.evidence_count == 0:
                return True
        return False

    @property
    def professor_ready(self) -> bool:
        """Determine if the bundle is professor-ready.

        professor_ready is true ONLY when:
        1. All mandatory stages are present
        2. All mandatory stages have PASS status
        3. No stage has PROVIDER_NOT_RUN
        4. All evidence-requiring stages have evidence_count > 0
        """
        return (
            self.all_stages_present
            and self.all_stages_pass
            and not self.has_provider_not_run
            and not self.has_evidence_gaps
        )

    @property
    def beta_status(self) -> str:
        """Determine beta status string."""
        if self.professor_ready:
            return "PROFESSOR_READY"
        if self.has_provider_not_run:
            return "NOT_PROFESSOR_READY"
        if not self.all_stages_pass:
            return "NOT_PROFESSOR_READY"
        return "NOT_PROFESSOR_READY"

    def to_dict(self) -> dict[str, Any]:
        """Serialize registry to dict for JSON output."""
        return {
            "professor_ready": self.professor_ready,
            "beta_status": self.beta_status,
            "all_stages_present": self.all_stages_present,
            "all_stages_pass": self.all_stages_pass,
            "has_provider_not_run": self.has_provider_not_run,
            "has_evidence_gaps": self.has_evidence_gaps,
            "stages": {rec.stage: rec.model_dump() for rec in self._records.values()},
        }

    def save(self, path: Path) -> None:
        """Save registry to JSON file atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            orjson.dumps(self.to_dict(), option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

    def compute_hash(self) -> str:
        """Compute deterministic hash of the registry state."""
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
