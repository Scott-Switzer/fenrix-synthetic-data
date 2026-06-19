"""GLiNER model loader protocol and default loader.

The default loader imports the `gliner` package lazily inside the function
body — never at import time — so that the rest of the discovery package
can be imported without GLiNER installed.

The loader returns an opaque model object that exposes
``predict_entities(text, labels, flat_ner, threshold)`` PLUS an optional
``to(device)`` method. Tests inject a fake model via the
``GlinerModelLoader`` protocol — no GLiNER required.

The configuration hash is computed ONLY over the semantic reproduction
configuration (``config.to_semantic_dict()``); ``cache_dir`` and
``allow_download`` are excluded.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol


class OptionalDependencyError(ImportError):
    """Raised when an optional dependency is required but not installed.

    This is a typed error so callers can distinguish "we don't have this
    dep installed in this environment" from arbitrary imports errors.
    """


class GlinerModelError(Exception):
    """Base class for GLiNER model load/inference errors raised by the adapter."""


class GlinerModelLoadError(GlinerModelError):
    """Raised when the model cannot be loaded (missing, unreachable, invalid)."""


class GlinerModelInferenceError(GlinerModelError):
    """Raised when predict_entities fails or returns a shape we cannot handle."""


class GlinerModelProtocol(Protocol):
    """Structural typing for whatever GLiNER returns from from_pretrained."""

    def predict_entities(
        self,
        text: str,
        labels: list[str],
        flat_ner: bool = True,
        threshold: float = 0.5,
    ) -> list[dict[str, Any]]: ...

    def to(self, device: str) -> Any: ...


class GlinerModelLoader(Protocol):
    """Callable protocol: take a config, return a GlinerModelProtocol.

    Tests inject fakes; production uses ``default_gliner_loader``.
    """

    def __call__(self, config: Any) -> GlinerModelProtocol: ...


def is_gliner_available() -> bool:
    """Return True iff the gliner package can be imported in this environment."""
    try:
        import gliner  # noqa: F401

        return True
    except ImportError:
        return False


def default_gliner_loader(config: Any) -> Any:
    """Load GLiNER via the real package. Imports gliner lazily.

    The returned object is whatever GLiNER exposes. We deliberately
    type the result as ``Any`` since the real ``gliner.GLiNER`` class
    isn't imported until this function runs.

    Device policy: after ``from_pretrained``, calls ``model.to(device)``
    when supported. GLiNER does not natively expose a parameterised
    loader-time device map; this method is the documented 0.2.x
    contract for moving a loaded model onto its compute device.

    Raises:
        OptionalDependencyError: gliner is not installed in this env.
        GlinerModelLoadError: model cannot be loaded (network, invalid id, etc).
        ProviderConfigurationError: device string is unsupported for transfer.
    """
    try:
        from gliner import GLiNER  # type: ignore[import-not-found]
    except ImportError as e:
        raise OptionalDependencyError(
            "The optional 'gliner' package is required by the GLiNERLocalProvider. "
            'Install with `pip install -e ".[local-ner]"`. '
            "Default installation does not include it."
        ) from e

    if not config.allow_download:
        try:
            model = GLiNER.from_pretrained(
                config.model_id,
                revision=config.revision,
                local_files_only=True,
                cache_dir=config.cache_dir,
            )
        except Exception as e:
            raise GlinerModelLoadError(
                f"Failed to load GLiNER model {config.model_id!r} from local cache: {e}. "
                "If a download is required, run `fenrix-synth providers prepare` with "
                "`--allow-download`."
            ) from e
    else:
        try:
            model = GLiNER.from_pretrained(
                config.model_id,
                revision=config.revision,
                cache_dir=config.cache_dir,
            )
        except Exception as e:
            raise GlinerModelLoadError(
                f"Failed to load or download GLiNER model {config.model_id!r}: {e}"
            ) from e

    if config.device and config.device != "cpu":
        try:
            model = model.to(config.device)
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            raise GlinerModelLoadError(
                f"GLiNER model {config.model_id!r} could not be moved to device "
                f"{config.device!r}: {type(e).__name__}: {e}"
            ) from e

    return GlinerFacade(model)


class GlinerFacade:
    """Stable interface around whatever GLiNER.from_pretrained returns.

    gliner==0.2.27 exposes ``predict_entities`` on instances of the
    concrete subclass returned by ``from_pretrained``, NOT on the
    ``GLiNER`` class itself. ``hasattr(GLiNER, 'predict_entities')``
    returns False at class level. This facade gives our adapter a
    stable surface so the rest of the package does not branch on
    upstream API churn.
    """

    def __init__(self, model: Any) -> None:
        self._raw = model

    def predict_entities(
        self,
        text: str,
        labels: list[str],
        flat_ner: bool = True,
        threshold: float = 0.5,
    ) -> list[dict[str, Any]]:
        target: Any | None = getattr(self._raw, "predict_entities", None)
        if target is None:
            target = getattr(self._raw, "predict", None)
        if target is None:
            raise GlinerModelInferenceError(
                f"Loaded GLiNER model does not expose predict_entities or predict. Got: {type(self._raw).__name__}"
            )
        try:
            # The upstream callable is typed as Any by design; the
            # contract is documented to return ``list[dict[str, Any]]``
            # at the adapter boundary.
            return target(text, labels=labels, flat_ner=flat_ner, threshold=threshold)  # type: ignore[no-any-return]
        except TypeError:
            return target(text, labels, flat_ner, threshold)  # type: ignore[no-any-return]

    def to(self, device: str) -> GlinerFacade:
        moved = self._raw.to(device)
        if moved is not None:
            self._raw = moved
        return self

    @property
    def raw(self) -> Any:
        return self._raw


def compute_config_hash(config: Any) -> str:
    """Deterministic hash of the SEMANTIC reproducibility configuration.

    Excludes ``cache_dir`` (local execution setting) and ``allow_download``
    (operator opt-in flag) so two machines with different cache paths
    produce the same hash for equivalent semantic configuration.

    Use this hash for reproducibility records and checkpoint invalidation.
    Do NOT feed ``config.to_dict()`` directly into a hash function.
    """
    content = json.dumps(
        config.to_semantic_dict() if hasattr(config, "to_semantic_dict") else config.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
