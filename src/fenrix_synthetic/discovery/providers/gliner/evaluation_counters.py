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


_CANONICAL_COUNTER_KEYS = frozenset(
    {
        "total_received",
        "accepted",
        "rejected_missing_fields",
        "rejected_invalid_offsets",
        "rejected_out_of_range",
        "rejected_text_mismatch",
        "rejected_non_numeric_score",
        "rejected_score_out_of_range",
        "rejected_missing_label",
        "quarantine_count",
    }
)


def _coerce_counters_dict(raw: dict[str, int]) -> ValidationCounters:
    """Reconstruct ValidationCounters from a dict, validating the shape.

    Raises ``TypeError`` with an explicit message if the dict does not
    exactly match the canonical counter key set. This surfaces any
    future producer drift instead of letting an extra key crash the
    evaluation pass with an opaque ``TypeError`` at ``__init__`` time.
    """
    unknown = set(raw) - _CANONICAL_COUNTER_KEYS
    missing = _CANONICAL_COUNTER_KEYS - set(raw)
    if unknown or missing:
        raise TypeError(
            "validation_counters dict shape mismatch in ensure_counters: "
            f"unknown_keys={sorted(unknown)} missing_keys={sorted(missing)}"
        )
    return ValidationCounters(**raw)


def ensure_counters(response: EntityDiscoveryResponse) -> ValidationCounters:
    """Return the validation counters attached to ``response``.

    Does NOT synthesize counters from predicted-candidate or
    false-positive counts. Returns zero counters only when the
    provider attached nothing — the caller surfaces the data gap via
    ``counters_provided``.

    Accepts either a ``ValidationCounters`` dataclass (legacy shape)
    OR a ``dict[str, int]`` (the post-part-3C serializable shape).
    For dicts, the dataclass is reconstructed via
    :func:`_coerce_counters_dict` which validates the canonical key
    set before unpacking.
    """
    raw = getattr(response, "validation_counters", None)
    if isinstance(raw, ValidationCounters):
        return raw
    if isinstance(raw, dict):
        if not raw:
            return ValidationCounters()
        return _coerce_counters_dict(raw)
    return ValidationCounters()


def counters_provided(response: EntityDiscoveryResponse) -> bool:
    """Return True iff the response carries an attached ValidationCounters.

    Accepts either a ``ValidationCounters`` dataclass or a
    non-empty ``dict[str, int]``.
    """
    raw = getattr(response, "validation_counters", None)
    if isinstance(raw, ValidationCounters):
        return True
    if isinstance(raw, dict) and bool(raw):
        return True
    return False
