"""GLiNERLocalProvider implementation.

Conforms to the EntityDiscoveryProvider protocol. The model is loaded
through an injectable ``GlinerModelLoader``. Tests pass a fake loader;
production passes ``default_gliner_loader``.

Determinism:

* ``request_id`` is a sha256 over provider inputs (never random).
* ``labels_requested`` preserves first-seen order.
* Candidate IDs are derived in ``validation.derive_candidate_id`` from
  public fields only. The matched private text is never an input.

Privacy:

* ``raw_response_hash`` records provider-output fingerprints using only
  redacted metadata (counts, ids, hashes). It is NEVER derived from
  the produced response text, because that text contains the matched
  private span.
* Quarantine samples and raw provider output are private artifacts.
  They are not exposed in the sanitized DiscoveryReport.
* Cache paths are never written to the sanitized payload.

Timeout safety:

* The fake / theatre ``model_load_timeout_seconds`` field has been
  removed from the configuration. Python cannot safely interrupt an
  in-process torch call (no signals on worker threads; SIGALRM
  requires the main thread). The provider does not raise a phantom
  ``TimeoutError`` from a wall-clock value.
"""

from __future__ import annotations

import hashlib
import json
import time
from importlib import metadata as importlib_metadata
from typing import Any

from ...protocol import (
    EntityDiscoveryProvider,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from ...schemas import (
    DiscoveryChunk,
    EntityDiscoveryResponse,
)
from .config import GLiNERConfig
from .loader import (
    GlinerModelLoader,
    GlinerModelLoadError,
    OptionalDependencyError,
    compute_config_hash,
)
from .mapping import EntityLabelMapping, default_label_mapping
from .validation import validate_and_convert


def _safe_package_version(package_name: str) -> str | None:
    """Return installed package version, or None when not installed."""
    try:
        return importlib_metadata.version(package_name)
    except importlib_metadata.PackageNotFoundError:
        return None


def derive_request_id(
    *,
    document_artifact_id: str,
    chunk_id: str,
    config_hash: str,
    threshold: float,
    company_id: str,
    provider_name: str,
    labels_requested: list[str],
) -> str:
    """Deterministic 24-char request id derived from public inputs."""
    payload = (
        f"{document_artifact_id}|{chunk_id}|{config_hash}|"
        f"{threshold}|{company_id}|{provider_name}|"
        f"{','.join(labels_requested)}"
    )
    return "req-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _stable_dedup(items: list[str]) -> list[str]:
    """Stable first-seen order deduplication."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _raw_response_redaction_hash(
    raw_entities: list[dict[str, Any]] | None,
    *,
    document_artifact_id: str,
    chunk_id: str,
    config_hash: str,
    company_id: str,
    provider_name: str,
) -> str:
    """Hash ONLY redacted metadata about the provider output.

    The raw entities themselves can contain matched private text, so
    they are NEVER hashed directly. The fingerprint enables replay
    detection on counts and ids only.
    """
    redaction = {
        "document_artifact_id": document_artifact_id,
        "chunk_id": chunk_id,
        "config_hash": config_hash,
        "company_id": company_id,
        "provider_name": provider_name,
        "raw_output_count": len(raw_entities or []),
    }
    return hashlib.sha256(
        json.dumps(redaction, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


class GLiNERLocalProvider(EntityDiscoveryProvider):
    """Optional local GLiNER entity-discovery provider."""

    def __init__(
        self,
        config: GLiNERConfig,
        loader: GlinerModelLoader | None = None,
        label_mapping: EntityLabelMapping | None = None,
    ) -> None:
        if config.company_id == "":
            raise ValueError("GLiNERLocalProvider requires a non-empty config.company_id")
        self._config = config
        self._loader = loader
        self._model: Any = None
        self._label_mapping = label_mapping or default_label_mapping()
        self._model_identity_record: dict[str, Any] = {}

    @property
    def provider_name(self) -> str:
        return self._config.provider_name

    @property
    def model_name(self) -> str:
        return self._config.model_id

    @property
    def model_version(self) -> str:
        return self._config.revision or "unresolved"

    @property
    def config_hash(self) -> str:
        return compute_config_hash(self._config)

    @property
    def config(self) -> GLiNERConfig:
        return self._config

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_identity(self) -> dict[str, Any]:
        return dict(self._model_identity_record)

    def health_check(self) -> bool:
        try:
            model = self._ensure_loaded()
        except OptionalDependencyError:
            return False
        except GlinerModelLoadError:
            return False
        return model is not None

    def discover(
        self,
        chunk: DiscoveryChunk,
        labels: list[str],
        context: dict | None = None,
    ) -> EntityDiscoveryResponse:
        if not isinstance(chunk, DiscoveryChunk):
            raise ProviderConfigurationError(
                f"chunk must be a DiscoveryChunk, got {type(chunk).__name__}"
            )
        gliner_labels = self._resolve_gliner_labels(labels)
        if not gliner_labels:
            gliner_labels = [str(lbl) for lbl in labels]
        try:
            model = self._ensure_loaded()
        except OptionalDependencyError as e:
            raise ProviderUnavailableError(str(e)) from e
        except GlinerModelLoadError as e:
            raise ProviderUnavailableError(str(e)) from e

        if len(chunk.text) > self._config.max_input_length:
            raise ProviderConfigurationError(
                f"chunk length {len(chunk.text)} exceeds max_input_length "
                f"{self._config.max_input_length}"
            )

        start_ts = time.perf_counter()
        try:
            raw_entities = model.predict_entities(
                chunk.text,
                labels=gliner_labels,
                flat_ner=True,
                threshold=self._config.threshold,
            )
        except (OSError, ValueError, RuntimeError, TypeError) as e:
            # GLiNER / torch surface these exception types for genuine
            # model-level failures (tokenization, tensor shapes, OOM). Do
            # NOT catch AttributeError / NameError / KeyError so programming
            # bugs surface normally.
            raise ProviderResponseError(
                f"GLiNER predict_entities raised {type(e).__name__}: {e}"
            ) from e
        latency_ms = (time.perf_counter() - start_ts) * 1000.0

        result = validate_and_convert(
            raw_entities or [],
            chunk,
            company_id=self._config.company_id,
            provider_name=self.provider_name,
            model_name=self.model_name,
            model_version=self.model_version,
            config_hash=self.config_hash,
            adapter_policy_version=self._config.adapter_policy_version,
            label_mapping=self._label_mapping,
        )

        labels_requested = _stable_dedup([*labels, *gliner_labels])
        request_id = derive_request_id(
            document_artifact_id=chunk.document_artifact_id,
            chunk_id=chunk.chunk_id,
            config_hash=self.config_hash,
            threshold=self._config.threshold,
            company_id=self._config.company_id,
            provider_name=self.provider_name,
            labels_requested=labels_requested,
        )
        raw_hash = _raw_response_redaction_hash(
            raw_entities,
            document_artifact_id=chunk.document_artifact_id,
            chunk_id=chunk.chunk_id,
            config_hash=self.config_hash,
            company_id=self._config.company_id,
            provider_name=self.provider_name,
        )

        warnings = list(result.warnings)
        warnings.extend(
            [
                f"adapter_policy_version={self._config.adapter_policy_version}",
                f"config_hash={self.config_hash}",
                f"threshold={self._config.threshold}",
                f"device={self._config.device}",
            ]
        )

        return EntityDiscoveryResponse(
            request_id=request_id,
            provider_name=self.provider_name,
            model_name=self.model_name,
            model_version=self.model_version,
            company_id=self._config.company_id,
            document_artifact_id=chunk.document_artifact_id,
            chunk_id=chunk.chunk_id,
            input_hash=chunk.input_hash,
            labels_requested=labels_requested,
            provider_candidates=result.candidates,
            latency_ms=round(latency_ms, 3),
            usage_token_count=None,
            warnings=warnings,
            raw_response_hash=raw_hash,
            provider_config_hash=self.config_hash,
            validation_counters=result.counters,
        )

    def dispose(self) -> None:
        self._model = None

    def _resolve_gliner_labels(self, labels: list[str]) -> list[str]:
        """Return the user-facing descriptive labels for the requested canonical labels."""
        inverse: dict[str, str] = {}
        for raw, canon in self._label_mapping.label_mapping.items():
            inverse.setdefault(canon, raw)
        gliner_labels: list[str] = []
        for requested in labels:
            gliner_label = inverse.get(requested, requested)
            gliner_labels.append(gliner_label)
        return _stable_dedup(gliner_labels)

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model
        if self._loader is None:
            raise OptionalDependencyError(
                "No model loader provided to GLiNERLocalProvider. "
                'Pass a custom loader or install `pip install -e ".[local-ner]"`.'
            )
        self._model_identity_record = {
            "package": "gliner",
            "package_version": _safe_package_version("gliner"),
            "model_id": self._config.model_id,
            "revision": self._config.revision,
            "device": self._config.device,
            "adapter_policy_version": self._config.adapter_policy_version,
            "config_hash": self.config_hash,
            "resolved_revision": None,
            "model_load_succeeded": False,
        }
        model = self._loader(self._config)
        self._model = model
        self._model_identity_record["model_load_succeeded"] = True
        if hasattr(model, "id_to_name") and isinstance(getattr(model, "id_to_name", None), str):
            self._model_identity_record["resolved_revision"] = model.id_to_name
        return self._model
