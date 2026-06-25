"""Professor-bundle pipeline stage registry.

Defines the 23 mandatory stages, their status records, and the registry
that validates all stages ran before a bundle can be marked professor-ready.

Hard contract:
- In production mode, PROVIDER_NOT_RUN is a blocking failure.
- In production mode, any mock/fixture/skipped provider is a blocking failure.
- professor_ready=true is impossible if any mandatory stage is missing,
  FAIL, PROVIDER_NOT_RUN, has evidence_count=0 where evidence is required,
  or uses a non-production provider.
- Fixture mode can produce strict_fixture_ready=true but never professor_ready=true.
- Local-dev mode can skip providers but cannot claim professor readiness.
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
    """The 23 mandatory professor-bundle pipeline stages."""

    SOURCE_INGESTION = "SOURCE_INGESTION"
    SEC_PARSE = "SEC_PARSE"
    SECTION_EXTRACT = "SECTION_EXTRACT"
    ENTITY_DETECT_GLINER = "ENTITY_DETECT_GLINER"
    ENTITY_DETECT_RULES = "ENTITY_DETECT_RULES"
    ENTITY_RESOLVE = "ENTITY_RESOLVE"
    DEIDENTIFY = "DEIDENTIFY"
    PRIVATE_EVIDENCE_BUILD = "PRIVATE_EVIDENCE_BUILD"
    SYNTHETIC_PROFILE_BUILD = "SYNTHETIC_PROFILE_BUILD"
    PEER_ARCHETYPE = "PEER_ARCHETYPE"
    FILING_RECONSTRUCT = "FILING_RECONSTRUCT"
    METRIC_SYNTHESIS = "METRIC_SYNTHESIS"
    METRIC_EVALUATION = "METRIC_EVALUATION"
    NEWS_RECONSTRUCT = "NEWS_RECONSTRUCT"
    CROSSLINK_BUILD = "CROSSLINK_BUILD"
    PEDAGOGY_BUILD = "PEDAGOGY_BUILD"
    RAG_INDEX_BUILD = "RAG_INDEX_BUILD"
    ADVERSARIAL_QA = "ADVERSARIAL_QA"
    RELEASE_GATE = "RELEASE_GATE"
    LLM_BLIND_GUESS = "LLM_BLIND_GUESS"
    UTILITY_PRESERVATION = "UTILITY_PRESERVATION"
    ZIP_EXPORT = "ZIP_EXPORT"


class StageStatus(StrEnum):
    """Status of a single pipeline stage."""

    PASS = "PASS"
    FAIL = "FAIL"
    PROVIDER_NOT_RUN = "PROVIDER_NOT_RUN"
    SKIPPED = "SKIPPED"
    RUNNING = "RUNNING"


class BuildMode(StrEnum):
    """Build mode determines what providers are allowed and what readiness
    statuses are achievable.

    - fixture: mock/fixture providers allowed; professor_ready always False.
    - local_dev: providers may be skipped; professor_ready always False.
    - production: only real providers; professor_ready achievable.
    """

    FIXTURE = "fixture"
    LOCAL_DEV = "local_dev"
    PRODUCTION = "production"


class ProviderKind(StrEnum):
    """Kind of provider used by a stage."""

    REAL = "real"
    MOCK = "mock"
    FIXTURE = "fixture"
    SKIPPED = "skipped"


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
        ProfessorStage.SYNTHETIC_PROFILE_BUILD,
        ProfessorStage.PEER_ARCHETYPE,
        ProfessorStage.FILING_RECONSTRUCT,
        ProfessorStage.METRIC_SYNTHESIS,
        ProfessorStage.METRIC_EVALUATION,
        ProfessorStage.NEWS_RECONSTRUCT,
        ProfessorStage.LLM_BLIND_GUESS,
        ProfessorStage.UTILITY_PRESERVATION,
        ProfessorStage.CROSSLINK_BUILD,
        ProfessorStage.PEDAGOGY_BUILD,
        ProfessorStage.RAG_INDEX_BUILD,
        ProfessorStage.ADVERSARIAL_QA,
        ProfessorStage.ZIP_EXPORT,
    }
)

ALL_MANDATORY_STAGES: tuple[ProfessorStage, ...] = tuple(ProfessorStage)

# Provider kinds that are NOT production-grade
NON_PRODUCTION_PROVIDER_KINDS: frozenset[ProviderKind] = frozenset(
    {
        ProviderKind.MOCK,
        ProviderKind.FIXTURE,
        ProviderKind.SKIPPED,
    }
)


class StageStatusRecord(BaseModel):
    """Status record for a single pipeline stage execution.

    Includes provider provenance so the gate can distinguish real
    production providers from mock/fixture/skipped providers.
    """

    stage: ProfessorStage
    status: StageStatus
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)

    # Provider provenance
    provider_name: str = ""
    provider_kind: ProviderKind = ProviderKind.REAL
    provider_version: str = ""
    provider_config_hash: str = ""
    is_production_provider: bool = True

    model_config = {"use_enum_values": True}


class StageRegistry:
    """Registry of stage status records for a professor-bundle run.

    Validates that all mandatory stages ran and produced evidence before
    the bundle can be marked professor-ready.

    Readiness semantics:
    - professor_ready: True only in production mode with all real providers
      passing with evidence.
    - strict_fixture_ready: True in fixture mode when all stages pass (with
      mock providers allowed).
    - fixture_ready: True in fixture mode when the bundle is structurally
      complete (may have skipped providers).
    """

    def __init__(self, build_mode: BuildMode = BuildMode.PRODUCTION) -> None:
        self._records: dict[ProfessorStage, StageStatusRecord] = {}
        self._build_mode = build_mode

    @property
    def build_mode(self) -> BuildMode:
        return self._build_mode

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
            (rec := self._records.get(s)) is not None and rec.status == StageStatus.PROVIDER_NOT_RUN
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
    def has_mock_providers(self) -> bool:
        """Check if any stage used a mock or fixture provider."""
        return any(
            (rec := self._records.get(s)) is not None
            and rec.provider_kind in NON_PRODUCTION_PROVIDER_KINDS
            for s in ALL_MANDATORY_STAGES
        )

    @property
    def has_missing_provider_provenance(self) -> bool:
        """Check if any stage is missing provider provenance fields."""
        return any(
            (rec := self._records.get(s)) is not None
            and (not rec.provider_name or not rec.provider_kind)
            for s in ALL_MANDATORY_STAGES
        )

    @property
    def non_production_conditions(self) -> list[str]:
        """List of non-production conditions present in this registry."""
        conditions: list[str] = []
        if self._build_mode == BuildMode.FIXTURE:
            conditions.append("build_mode_is_fixture")
        if self._build_mode == BuildMode.LOCAL_DEV:
            conditions.append("build_mode_is_local_dev")
        if self.has_mock_providers:
            conditions.append("mock_provider_used")
        if self.has_provider_not_run:
            conditions.append("provider_not_run")
        if self.has_missing_provider_provenance:
            conditions.append("missing_provider_provenance")
        return conditions

    @property
    def professor_ready(self) -> bool:
        """Determine if the bundle is professor-ready (production mode only).

        professor_ready is true ONLY when:
        1. Build mode is production
        2. All mandatory stages are present
        3. All mandatory stages have PASS status
        4. No stage has PROVIDER_NOT_RUN
        5. All evidence-requiring stages have evidence_count > 0
        6. No mock/fixture/skipped providers used
        7. No missing provider provenance
        """
        return (
            self._build_mode == BuildMode.PRODUCTION
            and self.all_stages_present
            and self.all_stages_pass
            and not self.has_provider_not_run
            and not self.has_evidence_gaps
            and not self.has_mock_providers
            and not self.has_missing_provider_provenance
        )

    @property
    def release_safe(self) -> bool:
        """Release safe mirrors professor_ready."""
        return self.professor_ready

    @property
    def strict_fixture_ready(self) -> bool:
        """True in fixture mode when all stages pass with evidence.

        Mock providers are allowed; the bundle proves the architecture
        works but is NOT production-ready.
        """
        return (
            self._build_mode == BuildMode.FIXTURE
            and self.all_stages_present
            and self.all_stages_pass
            and not self.has_evidence_gaps
        )

    @property
    def fixture_ready(self) -> bool:
        """True in fixture mode when the bundle is structurally complete."""
        return self._build_mode == BuildMode.FIXTURE and self.all_stages_present

    @property
    def beta_status(self) -> str:
        """Determine beta status string based on build mode and readiness."""
        if self.professor_ready:
            return "PROFESSOR_READY"
        if self.strict_fixture_ready:
            return "STRICT_FIXTURE_READY"
        if self.fixture_ready:
            return "FIXTURE_READY"
        if self._build_mode == BuildMode.LOCAL_DEV:
            return "LOCAL_DEV_NOT_READY"
        return "NOT_PROFESSOR_READY"

    def to_dict(self) -> dict[str, Any]:
        """Serialize registry to dict for JSON output."""
        return {
            "build_mode": self._build_mode.value,
            "professor_ready": self.professor_ready,
            "release_safe": self.release_safe,
            "fixture_ready": self.fixture_ready,
            "strict_fixture_ready": self.strict_fixture_ready,
            "beta_status": self.beta_status,
            "all_stages_present": self.all_stages_present,
            "all_stages_pass": self.all_stages_pass,
            "has_provider_not_run": self.has_provider_not_run,
            "has_evidence_gaps": self.has_evidence_gaps,
            "has_mock_providers": self.has_mock_providers,
            "has_missing_provider_provenance": self.has_missing_provider_provenance,
            "non_production_conditions": self.non_production_conditions,
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
