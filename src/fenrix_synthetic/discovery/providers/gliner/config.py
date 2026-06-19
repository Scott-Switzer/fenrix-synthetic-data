"""Configuration schema for the optional GLiNER adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_ADAPTER_POLICY_VERSION = "1.0.0"
DEFAULT_THRESHOLD = 0.50

DEVICE_CPU = "cpu"
DEVICE_MPS = "mps"
DEVICE_CUDA = "cuda"
DEVICE_AUTO = "auto"

SUPPORTED_DEVICES = {DEVICE_CPU, DEVICE_MPS, DEVICE_CUDA, DEVICE_AUTO}


def is_supported_device(value: str) -> bool:
    return value in SUPPORTED_DEVICES


@dataclass
class GLiNERConfig:
    """Configuration for one GLiNER adapter instance.

    All fields default to safe, explicit values. `allow_download` is
    deliberately False by default — model acquisition requires an
    explicit opt-in command. The adapter is reproducible: the
    configuration hash is computed and recorded alongside every
    discovery response.
    """

    model_id: str
    revision: str | None = None
    threshold: float = DEFAULT_THRESHOLD
    device: str = DEVICE_CPU
    batch_size: int = 1
    cache_dir: str | None = None
    allow_download: bool = False
    label_mapping: dict[str, str] = field(default_factory=dict)
    max_input_length: int = 2048
    model_load_timeout_seconds: float = 60.0
    adapter_policy_version: str = DEFAULT_ADAPTER_POLICY_VERSION
    company_id: str = "C001"
    provider_name: str = "gliner_local"

    def __post_init__(self) -> None:
        if not self.model_id or not isinstance(self.model_id, str):
            raise ValueError("GLiNERConfig.model_id must be a non-empty string")
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
        if self.model_load_timeout_seconds <= 0:
            raise ValueError("GLiNERConfig.model_load_timeout_seconds must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "revision": self.revision,
            "threshold": self.threshold,
            "device": self.device,
            "batch_size": self.batch_size,
            "cache_dir": self.cache_dir,
            "allow_download": self.allow_download,
            "label_mapping": dict(self.label_mapping),
            "max_input_length": self.max_input_length,
            "model_load_timeout_seconds": self.model_load_timeout_seconds,
            "adapter_policy_version": self.adapter_policy_version,
            "company_id": self.company_id,
            "provider_name": self.provider_name,
        }
