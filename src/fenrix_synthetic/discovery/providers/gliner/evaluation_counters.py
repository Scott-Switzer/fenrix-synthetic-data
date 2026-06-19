"""Validation counters accessor for GLiNER evaluation.

``ensure_counters`` returns the actual ``ValidationCounters`` attached
to a provider response. It does NOT synthesize counters from
predicted / false-positive counts; if the provider did not surface
counters, it returns zero counters. Strict semantics: evaluation
cannot credit hits from missing telemetry.

Callers should use ``counters_provided`` to surface the data gap when
running evaluation against older provider responses that did not
attach counters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .validation import ValidationCounters

if TYPE_CHECKING:
    from fenrix_synthetic.discovery.schemas import EntityDiscoveryResponse


def ensure_counters(response: EntityDiscoveryResponse) -> ValidationCounters:
    """Return the validation counters attached to ``response``.

    Does NOT synthesize counters from predicted-candidate or
    false-positive counts. Returns zero counters if the provider did
    not attach ``validation_counters`` — the caller is expected to
    surface the gap via ``counters_provided``.
    """
    raw = getattr(response, "validation_counters", None)
    if isinstance(raw, ValidationCounters):
        return raw
    return ValidationCounters()


def counters_provided(response: EntityDiscoveryResponse) -> bool:
    """Return True iff the response carries an attached ValidationCounters."""
    return isinstance(getattr(response, "validation_counters", None), ValidationCounters)
