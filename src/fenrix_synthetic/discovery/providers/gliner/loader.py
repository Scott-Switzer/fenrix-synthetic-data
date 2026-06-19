"""GLiNER model loader protocol and default loader.

The default loader imports the `gliner` package lazily inside the function
body — never at import time — so that the rest of the discovery package
can be imported without GLiNER installed.

The loader returns an opaque model object that exposes only
`predict_entities(text, labels, flat_ner, threshold)`. Tests inject a
fake model via the `GlinerModelLoader` protocol — no GLiNER required.
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


class GlinerModelLoader(Protocol):
    """Callable protocol: take a config, return a GlinerModelProtocol.

    Tests inject fakes; production uses default_gliner_loader.
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

    Raises:
        OptionalDependencyError: gliner is not installed in this env.
        GlinerModelLoadError: model cannot be loaded (network, invalid id, etc).
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
                "If a download is required, run `fenrix providers prepare` with "
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

    return model


def compute_config_hash(config: Any) -> str:
    """Deterministic hash of the configurable fields. Excludes paths."""
    content = json.dumps(
        config.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
