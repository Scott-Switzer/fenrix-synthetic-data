"""GLiNERLocalProvider implementation.

Conforms to the EntityDiscoveryProvider protocol defined in
fenrix_synthetic.discovery.protocol. The model is loaded through an
injectable `GlinerModelLoader`. Tests pass a fake loader; production
passes `default_gliner_loader`.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from typing import Any

from ...protocol import (
    EntityDiscoveryProvider,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
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


class GLiNERLocalProvider(EntityDiscoveryProvider):
    """Optional local GLiNER entity-discovery provider.

    The provider does not import the gliner package at construction time.
    The package is loaded only through the explicit loader on the first
    `health_check()` call or on the first `discover()` call. Tests inject
    a fake loader that returns a fake model.
    """

    def __init__(
        self,
        config: GLiNERConfig,
        loader: GlinerModelLoader | None = None,
        label_mapping: EntityLabelMapping | None = None,
    ) -> None:
        self._config = config
        self._loader = loader
        self._model: Any = None
        self._label_mapping = label_mapping or default_label_mapping()
        self._model_identity_record: dict[str, Any] = {}
        self._load_timestamp: datetime | None = None

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
        except TimeoutError as e:
            raise ProviderTimeoutError(
                f"GLiNER predict_entities exceeded {self._config.model_load_timeout_seconds}s"
            ) from e
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
            label_mapping=self._label_mapping,
            config_hash=self.config_hash,
        )

        labels_requested = list({*labels, *gliner_labels})
        request_id = f"req-{uuid.uuid4().hex[:8]}"
        raw_hash = hashlib.sha256(
            json.dumps(raw_entities or [], sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        warnings = list(result.warnings)
        warnings.extend(
            [
                f"adapter_policy_version={self._config.adapter_policy_version}",
                f"config_hash={self.config_hash}",
                f"threshold={self._config.threshold}",
                f"device={self._config.device}",
            ]
        )
        for c in result.counters.to_dict().items():
            warnings.append(f"validation:{c[0]}={c[1]}")

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
        return list(dict.fromkeys(gliner_labels))

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
            "model_load_timestamp": None,
            "model_load_succeeded": False,
        }
        model = self._loader(self._config)
        self._model = model
        timestamp = datetime.now(UTC).isoformat()
        self._model_identity_record["model_load_timestamp"] = timestamp
        self._model_identity_record["model_load_succeeded"] = True
        self._load_timestamp = datetime.fromisoformat(timestamp)
        if hasattr(model, "id_to_name") and isinstance(model.id_to_name, str):
            self._model_identity_record["resolved_revision"] = model.id_to_name
        return self._model
