"""Optional local GLiNER adapter.

This package is the implementation of the local GLiNER discovery provider.
Importing this package must NOT import the `gliner` package itself \u2014 the
underlying model library is loaded only through explicit loader injection
or via `health_check`/`discover` calls. Tests inject a fake loader to avoid
the dependency entirely.
"""

from .config import (
    DEFAULT_ADAPTER_POLICY_VERSION,
    DEFAULT_THRESHOLD,
    DEVICE_AUTO,
    DEVICE_CPU,
    DEVICE_MPS,
    GLiNERConfig,
    is_supported_device,
)
from .loader import (
    GlinerModelError,
    GlinerModelInferenceError,
    GlinerModelLoader,
    GlinerModelLoadError,
    GlinerModelProtocol,
    OptionalDependencyError,
    compute_config_hash,
    default_gliner_loader,
    is_gliner_available,
)
from .provider import GLiNERLocalProvider

__all__ = [
    "DEFAULT_ADAPTER_POLICY_VERSION",
    "DEFAULT_THRESHOLD",
    "DEVICE_AUTO",
    "DEVICE_CPU",
    "DEVICE_MPS",
    "GLiNERConfig",
    "GLiNERLocalProvider",
    "GlinerModelError",
    "GlinerModelInferenceError",
    "GlinerModelLoadError",
    "GlinerModelLoader",
    "GlinerModelProtocol",
    "OptionalDependencyError",
    "compute_config_hash",
    "default_gliner_loader",
    "is_gliner_available",
    "is_supported_device",
]
