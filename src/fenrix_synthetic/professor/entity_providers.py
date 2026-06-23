"""Entity provider protocol and adapters for the professor pipeline.

Defines a narrow protocol that connects the existing GLiNERLocalProvider
(which uses DiscoveryChunk-based discovery) to the professor pipeline's
ENTITY_DETECT_GLINER stage (which expects a discover_entities method).

Two adapters implement this protocol:
- MockGLiNERProfessorAdapter: wraps MockGLiNERProvider for fixture/local_dev
- LocalGLiNERProfessorAdapter: wraps GLiNERLocalProvider for production
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from .evidence import DetectedEntity, build_provenance_key, compute_opaque_id


class EntityDiscoveryError(Exception):
    """Raised when entity discovery fails irrecoverably."""


class ProfessorEntityProvider(Protocol):
    """Narrow protocol for professor-stage entity discovery.

    All adapters must implement this method. The orchestrator calls only
    discover_entities; adapter internals translate to the underlying provider.
    """

    def discover_entities(
        self,
        text: str,
        labels: Sequence[str],
        *,
        artifact_path: str,
        section_name: str,
        provenance_key: str,
        threshold: float = 0.5,
    ) -> list[DetectedEntity]:
        """Discover entities in text.

        Args:
            text: The raw text to scan.
            labels: Entity type labels to search for (e.g., company, executive).
            artifact_path: Path within the public output tree.
            section_name: Name of the section being scanned.
            provenance_key: Public provenance key for generated entities.
            threshold: Confidence threshold (0-1).

        Returns:
            List of DetectedEntity objects.
        """
        ...

    def health_check(self) -> bool:
        """Return True if the provider is operational."""
        ...

    @property
    def provider_name(self) -> str:
        """Human-readable provider name for auditing."""
        ...

    @property
    def model_name(self) -> str:
        """Model identifier for auditing."""
        ...

    @property
    def model_version(self) -> str:
        """Model version string for auditing."""
        ...


class MockGLiNERProfessorAdapter:
    """Adapter wrapping MockGLiNERProvider into ProfessorEntityProvider protocol.

    Used in fixture and local_dev modes.
    """

    PROVIDER_NAME = "mock_gliner"
    MODEL_NAME = "mock-gliner-finance-v1"
    MODEL_VERSION = "1.0.0-mock"

    def __init__(self) -> None:
        from .providers import MockGLiNERProvider

        self._inner = MockGLiNERProvider()
        self._scan_count = 0
        self._entity_count = 0
        self._failure_count = 0

    def discover_entities(
        self,
        text: str,
        labels: Sequence[str],
        *,
        artifact_path: str,
        section_name: str,
        provenance_key: str,
        threshold: float = 0.5,
    ) -> list[DetectedEntity]:
        """Discover entities using the mock provider, normalized to protocol."""
        raw = self._inner.discover_entities(
            text=text,
            company_id="COMPANY_001",
            source_artifact_id=artifact_path,
            labels=list(labels),
        )
        self._scan_count += 1
        self._entity_count += len(raw)
        return raw

    def health_check(self) -> bool:
        return self._inner.health_check()

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def model_name(self) -> str:
        return self.MODEL_NAME

    @property
    def model_version(self) -> str:
        return self.MODEL_VERSION

    def get_audit_report(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "provider_kind": "fake",
            "model_id": self.MODEL_NAME,
            "model_version": self.MODEL_VERSION,
            "threshold": 0.5,
            "labels_configured": ["company", "executive", "product", "ticker", "domain"],
            "labels_requested": [],
            "artifacts_scanned": self._scan_count,
            "entity_count_total": self._entity_count,
            "entity_discovery_count": self._entity_count,
            "failure_count": self._failure_count,
            "total_inference_time_seconds": 0.0,
            "discovery_succeeded": True,
            "model_loaded": True,
        }


class LocalGLiNERProfessorAdapter:
    """Adapter wrapping GLiNERLocalProvider into ProfessorEntityProvider protocol.

    Translates:
    - professor stage inputs (text, labels) -> DiscoveryChunk-based call
    - local provider output (EntityDiscoveryResponse) -> list[DetectedEntity]
    - local errors -> structured EntityDiscoveryError

    Used in production mode.
    """

    PROVIDER_NAME = "gliner_local"
    MODEL_NAME = ""
    MODEL_VERSION = ""

    def __init__(
        self,
        model_id: str = "urchade/gliner_multi-v2.1",
        threshold: float = 0.5,
        labels: list[str] | None = None,
        company_id: str = "COMPANY_001",
    ) -> None:
        self._model_id = model_id
        self._threshold = threshold
        self._configured_labels = labels or [
            "issuer",
            "subsidiary",
            "executive",
            "director",
            "customer",
            "supplier",
            "counterparty",
            "product",
            "brand",
            "geography",
            "facility",
            "regulator",
            "auditor",
            "lender",
            "litigation party",
            "merger/acquisition party",
            "business segment",
            "risk factor",
            "financial metric",
            "exchange",
            "ticker",
            "CIK",
            "accession number",
            "domain",
            "email",
            "phone",
            "address",
            "date",
            "money",
            "percentage",
        ]
        self._company_id = company_id
        self._inner: Any = None
        self._load_error: str | None = None
        self._scan_count = 0
        self._empty_count = 0
        self._failed_count = 0
        self._spans_by_label: dict[str, int] = {}
        self._provenance_keys: list[str] = []

    def _ensure_loaded(self) -> Any:
        """Lazy-load the GLiNER provider, trapping OptionalDependencyError."""
        if self._inner is not None:
            return self._inner
        if self._load_error is not None:
            raise EntityDiscoveryError(self._load_error)

        try:
            from ..discovery.providers.gliner import GLiNERConfig, GLiNERLocalProvider

            config = GLiNERConfig(
                model_id=self._model_id,
                company_id=self._company_id,
                provider_name="gliner_local",
                threshold=self._threshold,
            )
            self._inner = GLiNERLocalProvider(config=config)
            self.MODEL_NAME = self._inner.model_name
            self.MODEL_VERSION = self._inner.model_version
            return self._inner
        except ImportError as e:
            self._load_error = (
                f"GLiNER not installed. Install: pip install -e '.[local-ner]'. Error: {e}"
            )
            raise EntityDiscoveryError(self._load_error) from e
        except Exception as e:
            self._load_error = f"GLiNER failed to load: {e}"
            raise EntityDiscoveryError(self._load_error) from e

    def discover_entities(
        self,
        text: str,
        labels: Sequence[str],
        *,
        artifact_path: str,
        section_name: str,
        provenance_key: str,
        threshold: float = 0.5,
    ) -> list[DetectedEntity]:
        """Discover entities using the real GLiNER provider, normalized to protocol."""
        provider = self._ensure_loaded()

        from ..discovery.schemas import DiscoveryChunk

        chunk = DiscoveryChunk(
            chunk_id=section_name,
            document_artifact_id=artifact_path,
            chunk_index=0,
            start_offset=0,
            end_offset=len(text),
            text=text,
            section_hint=section_name,
        )

        try:
            response = provider.discover(
                chunk=chunk,
                labels=list(labels),
            )
        except Exception as e:
            self._failed_count += 1
            raise EntityDiscoveryError(f"GLiNER discovery failed: {e}") from e

        self._scan_count += 1
        entities: list[DetectedEntity] = []

        for candidate in response.candidates:
            entity_id = f"gliner-{compute_opaque_id(artifact_path, str(candidate.start), candidate.entity_type)}"
            entity = DetectedEntity(
                entity_id=entity_id,
                company_id=self._company_id,
                entity_type=candidate.entity_type,
                detected_text=text[candidate.start : candidate.end],
                detection_method="gliner",
                confidence=candidate.confidence,
                start_offset=candidate.start,
                end_offset=candidate.end,
                source_artifact_id=artifact_path,
                provenance_key=build_provenance_key(
                    self._company_id, "ENTITY", "GLINER", "", entity_id[:8]
                ),
            )
            entities.append(entity)
            label = candidate.entity_type
            self._spans_by_label[label] = self._spans_by_label.get(label, 0) + 1

        if not entities:
            self._empty_count += 1

        self._provenance_keys.append(provenance_key)
        return entities

    def health_check(self) -> bool:
        try:
            self._ensure_loaded()
            return True
        except EntityDiscoveryError:
            return False

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def model_name(self) -> str:
        return self.MODEL_NAME

    @property
    def model_version(self) -> str:
        return self.MODEL_VERSION

    def get_audit_report(self) -> dict[str, Any]:
        """Return structured audit report for QA."""
        return {
            "provider_name": self.provider_name,
            "provider_kind": "real",
            "model_id": self._model_id,
            "model_version": self.model_version or "unavailable",
            "threshold": self._threshold,
            "labels_requested": list(self._configured_labels),
            "artifacts_scanned": self._scan_count,
            "empty_artifact_count": self._empty_count,
            "failed_artifact_count": self._failed_count,
            "spans_detected_by_label": dict(self._spans_by_label),
            "coverage_summary": (
                f"{self._scan_count - self._empty_count}/{self._scan_count} artifacts with entities"
                if self._scan_count > 0
                else "no artifacts scanned"
            ),
            "provenance_keys": list(self._provenance_keys),
            "warnings": ([self._load_error] if self._load_error else []),
        }


def create_gliner_provider(
    provider_type: str,
    config: dict[str, Any],
) -> ProfessorEntityProvider:
    """Factory function to create GLiNER provider from config."""
    if provider_type == "mock":
        return MockGLiNERProfessorAdapter()
    elif provider_type == "local":
        return LocalGLiNERProfessorAdapter(
            model_id=config.get("model_id", "urchade/gliner_multi-v2.1"),
            threshold=config.get("threshold", 0.5),
            labels=config.get("labels"),
            company_id=config.get("company_id", "COMPANY_001"),
        )
    else:
        raise ValueError(f"Unknown GLiNER provider type: {provider_type}")


def validate_gliner_config(config: dict[str, Any]) -> list[str]:
    """Validate GLiNER configuration.

    Returns list of violations (empty = valid).
    """
    violations: list[str] = []
    provider = config.get("provider", "")
    if provider not in ("mock", "local"):
        violations.append(f"GLiNER provider must be 'mock' or 'local', got {provider!r}")
    threshold = config.get("threshold", 0.5)
    if not (0 < threshold <= 1.0):
        violations.append(f"threshold must be in (0, 1], got {threshold}")
    return violations
