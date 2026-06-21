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
    NewsSurrogateGenerator,
    NewsSurrogateResult,
)
from .residual_scanner import ResidualScanner
from .structured_anonymizer import StructuredAnonymizer
from .surrogate_generator import SyntheticSurrogateGenerator
from .text_anonymizer import TextAnonymizer

__all__ = [
    "ClassroomNumericPackage",
    "ClassroomNumericWriter",
    "IdentityAtlasBuilder",
    "NewsSurrogateGenerator",
    "NewsSurrogateResult",
    "RatioBucket",
    "RegimeLabel",
    "ResidualScanner",
    "StructuredAnonymizer",
    "SyntheticAnnualStatement",
    "SyntheticSurrogateGenerator",
    "TextAnonymizer",
]
