"""Release eligibility guard (Phase 5A defect repair B).

S0, S1, S2 and S3A MUST NOT be capable of receiving a releasable
status, no matter which code path sets the marker. This module
implements defense-in-depth:

1. `enforce_eligibility_for_export` — called at every export boundary.
2. Importing this module raises on construction of any release artifact
   tagged with an ineligible variant.

The guard returns or raises BEFORE the data is written to disk.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fenrix_synthetic.transforms.feature_only import (
    NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE,
)

# Canonical re-export for consumers that expect this exact name
# (orchestrator, evidence manifest, dossier).
NOT_RELEASABLE_VARIANTS: frozenset[str] = NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE


class IneligibleVariantError(Exception):
    """Raised when an ineligible variant is treated as releasable.

    Ineligible variants: S0_CONTROL, S1_BASIC, S2_PRIVACY, S2_INCOMPLETE,
    and S3A_DAILY_BUCKETED (which is a NON_RELEASABLE_DIAGNOSTIC by spec).
    """

    def __init__(
        self,
        variant: str,
        release_marker: str,
        attempted_action: str,
    ) -> None:
        self.variant = variant
        self.release_marker = release_marker
        self.attempted_action = attempted_action
        self.timestamp = datetime.now(UTC).isoformat()
        super().__init__(
            f"Refusing to treat ineligible variant {variant!r} as "
            f"releasable (marker={release_marker!r}) during "
            f"{attempted_action!r}. S0, S1, S2 and S3A can never be "
            "released, regardless of caller intent."
        )


def enforce_eligibility_for_export(
    variant: str,
    release_marker: str,
    attempted_action: str = "export",
) -> None:
    """Raise IneligibleVariantError if the variant is not releasable.

    Defense-in-depth: Called at every export boundary. S3A is also
    blocked here because its spec marker is NON_RELEASABLE_DIAGNOSTIC;
    a tampered release_marker cannot bypass the guard.
    """
    if variant in NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE:
        raise IneligibleVariantError(variant, release_marker, attempted_action)

    # Even if the variant is not strictly in the ineligible set, if its
    # explicit release_marker carries a NON_RELEASABLE_DIAGNOSTIC tag,
    # refuse to release.
    if release_marker == "non_releasable_diagnostic":
        raise IneligibleVariantError(variant, release_marker, attempted_action)


def assert_releasable_variant(variant: str) -> None:
    """Lower-stakes variant check for use in evidence manifest / gate.

    Unlike `enforce_eligibility_for_export`, this DOES require the
    caller to be actively making a release decision. It is idempotent
    and side-effect-free outside the exception path.
    """
    if variant in NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE:
        raise IneligibleVariantError(variant, "any", "release_assessment")


def collect_ineligible_block_summary(
    variants_in_run: list[str],
) -> dict[str, Any]:
    """Helper for the evidence manifest to summarize which variants
    in a run are ineligible and therefore cannot be referenced in a
    release decision."""
    ineligibles = [v for v in variants_in_run if v in NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE]
    return {
        "ineligible_variants_in_run": ineligibles,
        "ineligible_count": len(ineligibles),
        "policy_note": (
            "These variants are documented in NOT_ELIGIBLE_FOR_STRUCTURED_RELEASE "
            "and cannot be referenced in any release decision."
        ),
    }
