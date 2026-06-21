from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from .protocol import (
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from .schemas import DiscoveryChunk, EntityDiscoveryResponse, ProviderCandidate


class FakeProviderMode:
    FIXED = "fixed"
    EMPTY = "empty"
    MALFORMED = "malformed"
    DUPLICATED = "duplicated"
    OVERLAPPING = "overlapping"
    BOUNDARY_DISAGREEMENT = "boundary_disagreement"
    LABEL_DISAGREEMENT = "label_disagreement"
    UNSUPPORTED_LABEL = "unsupported_label"
    LOW_CONFIDENCE = "low_confidence"
    HIGH_CONFIDENCE = "high_confidence"
    TIMEOUT = "timeout"
    PROVIDER_FAILURE = "provider_failure"


@dataclass
class FakeProviderConfig:
    """Synthetic-test fake-discovery-provider configuration.

    ``company_id`` is REQUIRED. Production code must NEVER silently assume
    any C001/HBAN-like identifier. Callers explicitly supply a synthetic
    fixture id (e.g. ``TEST-CO-001``).
    """

    company_id: str = ""
    mode: str = FakeProviderMode.FIXED
    fixed_candidates: list[dict] = field(default_factory=list)
    provider_name: str = "fake-local"
    model_name: str = "fake-model-v0"
    model_version: str = "1.0.0"
    latency_ms: float = 50.0
    token_count: int | None = None
    warning_messages: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.company_id or not isinstance(self.company_id, str):
            raise ValueError(
                "FakeProviderConfig.company_id is REQUIRED and must be a non-empty string. "
                "Use a synthetic fixture identifier (e.g. 'TEST-CO-001'). "
                "Production code must NEVER silently assume a real C001/HBAN id."
            )


class FakeEntityDiscoveryProvider:
    def __init__(self, config: FakeProviderConfig) -> None:
        # __post_init__ already validated company_id.
        self._config = config

    @property
    def provider_name(self) -> str:
        return self._config.provider_name

    @property
    def model_name(self) -> str:
        return self._config.model_name

    @property
    def model_version(self) -> str:
        return self._config.model_version

    def health_check(self) -> bool:
        if self._config.mode == FakeProviderMode.PROVIDER_FAILURE:
            return False
        return True

    def _build_fixed_candidates(self, chunk: DiscoveryChunk) -> list[ProviderCandidate]:
        candidates = []
        for i, fc in enumerate(self._config.fixed_candidates):
            matched_text = fc.get("text", f"DiscoveredEntity {i}")
            start = fc.get("start", chunk.start_offset)
            end = fc.get("end", start + len(matched_text))
            candidates.append(
                ProviderCandidate(
                    candidate_id=f"fake-{uuid.uuid4().hex[:8]}",
                    company_id=fc.get("company_id", self._config.company_id),
                    document_artifact_id=chunk.document_artifact_id,
                    chunk_ids=[chunk.chunk_id],
                    original_start=start,
                    original_end=end,
                    private_matched_text=matched_text,
                    matched_text_hash=hashlib.sha256(matched_text.encode()).hexdigest()[:16],
                    proposed_entity_type=fc.get("entity_type", "UNKNOWN"),
                    provider_label=fc.get("label", "MISC"),
                    provider_name=self._config.provider_name,
                    model_name=self._config.model_name,
                    model_version=self._config.model_version,
                    confidence=fc.get("confidence", 0.5),
                    context_hash=hashlib.sha256(chunk.text.encode()).hexdigest()[:16],
                    discovery_policy_hash=fc.get("policy_hash", ""),
                    chunking_policy_hash=fc.get("chunking_hash", ""),
                    duplicate_group_id=fc.get("group_id", ""),
                    overlap_status=fc.get("overlap", ""),
                    provider_evidence={"source": "fake_provider"},
                    risk_score=0.0,
                    risk_band="low",
                )
            )
        return candidates

    def discover(
        self,
        chunk: DiscoveryChunk,
        labels: list[str],
        context: dict | None = None,
    ) -> EntityDiscoveryResponse:
        if self._config.mode == FakeProviderMode.PROVIDER_FAILURE:
            raise ProviderUnavailableError("Fake provider is unavailable")

        if self._config.mode == FakeProviderMode.TIMEOUT:
            raise ProviderTimeoutError("Fake provider timed out")

        if self._config.mode == FakeProviderMode.EMPTY:
            return EntityDiscoveryResponse(
                request_id=f"req-{uuid.uuid4().hex[:8]}",
                provider_name=self._config.provider_name,
                model_name=self._config.model_name,
                model_version=self._config.model_version,
                company_id=self._config.company_id,
                document_artifact_id=chunk.document_artifact_id,
                chunk_id=chunk.chunk_id,
                input_hash=chunk.input_hash,
                labels_requested=labels,
                provider_candidates=[],
                latency_ms=self._config.latency_ms,
                usage_token_count=self._config.token_count,
                warnings=self._config.warning_messages,
                raw_response_hash="",
                provider_config_hash=self._config.provider_name,
            )

        if self._config.mode == FakeProviderMode.MALFORMED:
            raise ProviderResponseError("Malformed response from fake provider")

        if self._config.mode == FakeProviderMode.DUPLICATED:
            fixed = [
                {
                    "text": "Acme Corp",
                    "start": chunk.start_offset + 10,
                    "end": chunk.start_offset + 20,
                    "entity_type": "COMPANY",
                    "label": "COMPANY",
                    "confidence": 0.75,
                }
            ] * 3
            self._config = FakeProviderConfig(
                company_id=self._config.company_id,
                mode=FakeProviderMode.FIXED,
                fixed_candidates=fixed,
                provider_name=self._config.provider_name,
                model_name=self._config.model_name,
                model_version=self._config.model_version,
            )

        if self._config.mode == FakeProviderMode.OVERLAPPING:
            fixed = [
                {
                    "text": "Acme Corporation",
                    "start": chunk.start_offset + 10,
                    "end": chunk.start_offset + 28,
                    "entity_type": "COMPANY",
                    "label": "COMPANY",
                    "confidence": 0.7,
                },
                {
                    "text": "Acme Corp",
                    "start": chunk.start_offset + 10,
                    "end": chunk.start_offset + 20,
                    "entity_type": "COMPANY",
                    "label": "COMPANY",
                    "confidence": 0.8,
                },
            ]
            self._config = FakeProviderConfig(
                company_id=self._config.company_id,
                mode=FakeProviderMode.FIXED,
                fixed_candidates=fixed,
                provider_name=self._config.provider_name,
                model_name=self._config.model_name,
                model_version=self._config.model_version,
            )

        if self._config.mode == FakeProviderMode.BOUNDARY_DISAGREEMENT:
            fixed = [
                {
                    "text": "Acme Corporation Holdings",
                    "start": chunk.start_offset + 10,
                    "end": chunk.start_offset + 35,
                    "entity_type": "COMPANY",
                    "label": "COMPANY",
                    "confidence": 0.6,
                },
                {
                    "text": "Acme",
                    "start": chunk.start_offset + 10,
                    "end": chunk.start_offset + 15,
                    "entity_type": "COMPANY",
                    "label": "COMPANY",
                    "confidence": 0.9,
                },
            ]
            self._config = FakeProviderConfig(
                company_id=self._config.company_id,
                mode=FakeProviderMode.FIXED,
                fixed_candidates=fixed,
                provider_name=self._config.provider_name,
                model_name=self._config.model_name,
                model_version=self._config.model_version,
            )

        if self._config.mode == FakeProviderMode.LABEL_DISAGREEMENT:
            fixed = [
                {
                    "text": "Acme Holdings",
                    "start": chunk.start_offset + 10,
                    "end": chunk.start_offset + 23,
                    "entity_type": "COMPANY",
                    "label": "COMPANY",
                    "confidence": 0.6,
                },
                {
                    "text": "Acme Holdings",
                    "start": chunk.start_offset + 10,
                    "end": chunk.start_offset + 23,
                    "entity_type": "PRODUCT",
                    "label": "PRODUCT",
                    "confidence": 0.7,
                },
            ]
            self._config = FakeProviderConfig(
                company_id=self._config.company_id,
                mode=FakeProviderMode.FIXED,
                fixed_candidates=fixed,
                provider_name=self._config.provider_name,
                model_name=self._config.model_name,
                model_version=self._config.model_version,
            )

        if self._config.mode == FakeProviderMode.UNSUPPORTED_LABEL:
            fixed = [
                {
                    "text": "Something Unknown",
                    "start": chunk.start_offset + 50,
                    "end": chunk.start_offset + 65,
                    "entity_type": "UNSUPPORTED",
                    "label": "UNSUPPORTED_LABEL_TYPE",
                    "confidence": 0.5,
                }
            ]
            self._config = FakeProviderConfig(
                company_id=self._config.company_id,
                mode=FakeProviderMode.FIXED,
                fixed_candidates=fixed,
                provider_name=self._config.provider_name,
                model_name=self._config.model_name,
                model_version=self._config.model_version,
            )

        if self._config.mode == FakeProviderMode.LOW_CONFIDENCE:
            fixed = [
                {
                    "text": "Ambiguous Entity",
                    "start": chunk.start_offset + 100,
                    "end": chunk.start_offset + 116,
                    "entity_type": "MISC",
                    "label": "MISC",
                    "confidence": 0.25,
                }
            ]
            self._config = FakeProviderConfig(
                company_id=self._config.company_id,
                mode=FakeProviderMode.FIXED,
                fixed_candidates=fixed,
                provider_name=self._config.provider_name,
                model_name=self._config.model_name,
                model_version=self._config.model_version,
            )

        if self._config.mode == FakeProviderMode.HIGH_CONFIDENCE:
            fixed = [
                {
                    "text": "High Confidence Entity",
                    "start": chunk.start_offset + 200,
                    "end": chunk.start_offset + 223,
                    "entity_type": "COMPANY",
                    "label": "COMPANY",
                    "confidence": 0.98,
                }
            ]
            self._config = FakeProviderConfig(
                company_id=self._config.company_id,
                mode=FakeProviderMode.FIXED,
                fixed_candidates=fixed,
                provider_name=self._config.provider_name,
                model_name=self._config.model_name,
                model_version=self._config.model_version,
            )

        candidates = self._build_fixed_candidates(chunk)

        request_id = f"req-{uuid.uuid4().hex[:8]}"
        raw_content = str(candidates)
        raw_hash = hashlib.sha256(raw_content.encode()).hexdigest()[:16]
        config_hash = hashlib.sha256(
            f"{self._config.provider_name}:{self._config.model_name}".encode()
        ).hexdigest()[:16]

        return EntityDiscoveryResponse(
            request_id=request_id,
            provider_name=self._config.provider_name,
            model_name=self._config.model_name,
            model_version=self._config.model_version,
            company_id=self._config.company_id,
            document_artifact_id=chunk.document_artifact_id,
            chunk_id=chunk.chunk_id,
            input_hash=chunk.input_hash,
            labels_requested=labels,
            provider_candidates=candidates,
            latency_ms=self._config.latency_ms,
            usage_token_count=self._config.token_count,
            warnings=self._config.warning_messages,
            raw_response_hash=raw_hash,
            provider_config_hash=config_hash,
        )

    def dispose(self) -> None:
        pass
