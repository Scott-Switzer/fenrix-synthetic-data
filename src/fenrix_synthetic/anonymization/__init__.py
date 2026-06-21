"""Anonymization modules for deterministic text and structured data masking."""

from .atlas_builder import IdentityAtlasBuilder
from .classroom_numeric_writer import (
    ClassroomNumericPackage,
    ClassroomNumericWriter,
    RatioBucket,
    RegimeLabel,
    SyntheticAnnualStatement,
)
from .news_surrogate_generator import (
    FingerprintEntry,
    LightweightFingerprint,
    NewsSurrogateGenerator,
    NewsSurrogateResult,
)
from .residual_scanner import ResidualScanner
from .structured_anonymizer import StructuredAnonymizer
from .text_anonymizer import TextAnonymizer

__all__ = [
    "ClassroomNumericPackage",
    "ClassroomNumericWriter",
    "FingerprintEntry",
    "IdentityAtlasBuilder",
    "LightweightFingerprint",
    "NewsSurrogateGenerator",
    "NewsSurrogateResult",
    "RatioBucket",
    "RegimeLabel",
    "ResidualScanner",
    "StructuredAnonymizer",
    "SyntheticAnnualStatement",
    "TextAnonymizer",
]


def __getattr__(name: str) -> object:  # PEP 562 — lazy optional export.
    """Lazily import the experimental ``SyntheticSurrogateGenerator``.

    The companion ``surrogate_generator.py`` module is uncommitted and may
    be absent from the working tree. Resolving the attribute on first
    access means the import-clean contract of this package never breaks,
    yet a caller that actually uses ``SyntheticSurrogateGenerator`` still
    receives a loud ``ImportError`` if the experimental file is missing —
    no silent ``None`` placeholder.
    """
    if name == "SyntheticSurrogateGenerator":
        from .surrogate_generator import SyntheticSurrogateGenerator

        # Cache the symbol so subsequent `from ... import` lookups do not
        # pay the import cost again.
        globals()[name] = SyntheticSurrogateGenerator
        return SyntheticSurrogateGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Make ``SyntheticSurrogateGenerator`` discoverable via tab completion."""
    return sorted({*globals().keys(), "SyntheticSurrogateGenerator"})
