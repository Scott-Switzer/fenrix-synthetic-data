"""Configuration schema for the optional GLiNER adapter.

The configuration has TWO distinct parts:

1. **Semantic provider configuration** (reproducibility, hashed for record)
   Includes the values that determine what entities the adapter would
   produce given identical input: model_id, requested revision, threshold,
   device policy, label mapping, max_input_length, adapter_policy_version,
   provider_name, company_id.

2. **Local execution settings** (NOT hashed; environment-specific)
   ``cache_dir`` points at a per-machine Hugging Face cache directory.
   Two machines with different cache directories but identical semantic
   configuration MUST produce identical reproducibility hashes.

`to_dict()` returns both parts (for handing to the model loader); the
``compute_config_hash`` helper uses ``to_semantic_dict()`` to ensure
cross-machine reproducibility.

Unimplemented v1 fields ``batch_size`` and
``model_load_timeout_seconds`` have been removed. The real GLiNER
``predict_entities`` API does not take a batch-size argument at this
layer (chunking is external), and Python cannot safely interrupt an
in-process torch call — see Decision 028.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_ADAPTER_POLICY_VERSION = "2.0.0"
DEFAULT_THRESHOLD = 0.50

DEVICE_CPU = "cpu"
DEVICE_MPS = "mps"
DEVICE_CUDA = "cuda"
DEVICE_AUTO = "auto"

SUPPORTED_DEVICES = {DEVICE_CPU, DEVICE_MPS, DEVICE_CUDA, DEVICE_AUTO}

SEMANTIC_CONFIG_VERSION = "2.0.0"
EXECUTION_CONFIG_VERSION = "1.0.0"
"""Versioned so future modifications to either half bump the hash
deterministically. Bumping ``SEMANTIC_CONFIG_VERSION`` invalidates all
prior recorded hashes; bumping ``EXECUTION_CONFIG_VERSION`` does not."""

_LOCAL_EXECUTION_KEYS = frozenset({"cache_dir", "allow_download"})


def is_supported_device(value: str) -> bool:
    return value in SUPPORTED_DEVICES


@dataclass(frozen=True)
class GLiNERConfig:
    """Configuration for one GLiNER adapter instance.

    ``company_id``, ``model_id``, and ``provider_name`` are required —
    no default is permitted so that no adapter, validator, evaluator, or
    CLI command can silently default to a particular company.

    ``cache_dir`` is a local execution setting; it is excluded from the
    reproducibility hash. Two environments with different cache
    directories will produce the same ``config_hash`` if their semantic
    configuration matches.
    """

    model_id: str
    company_id: str
    provider_name: str = "gliner_local"
    revision: str | None = None
    threshold: float = DEFAULT_THRESHOLD
    device: str = DEVICE_CPU
    cache_dir: str | None = None
    allow_download: bool = False
    label_mapping: dict[str, str] = field(default_factory=dict)
    max_input_length: int = 2048
    adapter_policy_version: str = DEFAULT_ADAPTER_POLICY_VERSION

    def __post_init__(self) -> None:
        # ``frozen=True`` forbids plain attribute assignment; use object.__setattr__
        # only inside __post_init__ for any normalization that did not already
        # come from the constructor.
        if not self.model_id or not isinstance(self.model_id, str):
            raise ValueError("GLiNERConfig.model_id must be a non-empty string")
        if not self.company_id or not isinstance(self.company_id, str):
            raise ValueError(
                "GLiNERConfig.company_id must be a non-empty string (no C001 default permitted)"
            )
        if not self.provider_name or not isinstance(self.provider_name, str):
            raise ValueError("GLiNERConfig.provider_name must be a non-empty string")
        if not is_supported_device(self.device):
            raise ValueError(
                f"GLiNERConfig.device must be one of {sorted(SUPPORTED_DEVICES)}, got {self.device!r}"
            )
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(
                f"GLiNERConfig.threshold must be between 0.0 and 1.0, got {self.threshold!r}"
            )
        if self.max_input_length <= 0:
            raise ValueError("GLiNERConfig.max_input_length must be positive")

    def to_semantic_dict(self) -> dict[str, Any]:
        """Repro reproducibility hash input — EXCLUDES local cache_dir
        and allow_download so two machines with different cache paths
        produce the same hash for equivalent semantic configuration."""
        return {
            "semantic_config_version": SEMANTIC_CONFIG_VERSION,
            "model_id": self.model_id,
            "revision": self.revision,
            "threshold": self.threshold,
            "device": self.device,
            "label_mapping": dict(self.label_mapping),
            "max_input_length": self.max_input_length,
            "adapter_policy_version": self.adapter_policy_version,
            "company_id": self.company_id,
            "provider_name": self.provider_name,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return all fields, including execution-only settings.

        Suitable only for handing to a model loader; never feed to a
        reproducibility hash.
        """
        out = self.to_semantic_dict()
        out["execution_config_version"] = EXECUTION_CONFIG_VERSION
        out["cache_dir"] = self.cache_dir
        out["allow_download"] = self.allow_download
        return out

    def to_execution_dict(self) -> dict[str, Any]:
        """Return only the local execution settings (cache_dir, allow_download).

        Useful when piping to a model loader without recomputing the
        full dict.
        """
        return {k: getattr(self, k) for k in _LOCAL_EXECUTION_KEYS}
