"""Identity atlas subsystem.

Versioned private identity-atlas schema that supports:
- issuer, people, organizations, products, locations, digital, semantic_fingerprints
- typed pseudonyms: [ISSUER], [EXECUTIVE_01], [SUBSIDIARY_01], etc.
- deterministic replacement plan compilation
- coreference preservation
- provenance for each approved identity item
"""

from .compiler import (
    AtlasCompiler,
    CompiledReplacement,
    ReplacementPlan,
    compile_atlas,
    compute_replacement_hash,
)
from .schemas import (
    AtlasCategory,
    CasePolicy,
    IdentityAtlas,
    IdentityEntry,
    IdentitySubType,
    MatchPolicy,
)

__all__ = [
    "AtlasCategory",
    "AtlasCompiler",
    "CasePolicy",
    "CompiledReplacement",
    "IdentityAtlas",
    "IdentityEntry",
    "IdentitySubType",
    "MatchPolicy",
    "ReplacementPlan",
    "compile_atlas",
    "compute_replacement_hash",
]
