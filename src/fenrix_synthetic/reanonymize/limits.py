"""Limits parsing for ``fenrix-synth reanonymize-run``.

Pure functions. No side-effects. Easy to unit test.

Supported ``--limit-forms`` syntax::

    "10-K:1,10-Q:1,8-K:1"
    "10-K:2"
    "10-K" (means ``10-K:1``)
    "" or None (means ``{}`` — no limit applied)

Returns :class:`dict` mapping uppercased form names → max count.
The orchestrator applies this to discovered SEC filenames.
"""

from __future__ import annotations

import re
from pathlib import Path

_FORM_TOKEN_PATTERN = re.compile(r"^[0-9A-Za-z\-]+$")


def parse_form_limits(raw: str | None) -> dict[str, int]:
    """Parse the ``--limit-forms`` CLI argument into ``{FORM: max_count}``.

    Recognised token grammar: ``<FORM>[:<count>]``
    - Whitespace is tolerated around tokens.
    - Empty / None input yields ``{}`` (no per-form limit).
    - Unparseable tokens raise ``ValueError`` so the CLI surfaces a
      clear ``UsageError`` instead of silently swallowing bad input.
    """
    if raw is None:
        return {}
    text = raw.strip()
    if not text:
        return {}

    limits: dict[str, int] = {}
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            form_part, count_part = token.split(":", 1)
            form = form_part.strip().upper()
            count_raw = count_part.strip()
        else:
            form = token.strip().upper()
            count_raw = "1"
        if not _FORM_TOKEN_PATTERN.match(form):
            raise ValueError(f"Invalid form token: {token!r}")
        if not count_raw.isdigit():
            raise ValueError(f"Invalid count for form {form!r}: {count_raw!r}")
        count = int(count_raw)
        if count < 1:
            raise ValueError(f"Count must be >= 1 for form {form!r}")
        # If a form appears twice, the larger count wins (lenient).
        limits[form] = max(limits.get(form, 0), count)
    return limits


_SEC_FORM_PATTERNS: dict[str, re.Pattern[str]] = {
    "10-K": re.compile(r"(?:^|[_\-])10[-_]?K(?:[_\-]|$)", re.IGNORECASE),
    "10-Q": re.compile(r"(?:^|[_\-])10[-_]?Q(?:[_\-]|$)", re.IGNORECASE),
    "8-K": re.compile(r"(?:^|[_\-])8[-_]?K(?:[_\-]|$)", re.IGNORECASE),
}


def infer_form(filename: str) -> str | None:
    """Best-effort form inference from a SEC filename.

    The orchestrator uses this as a fallback when no manifest is shipped.
    Returns ``None`` when no form can be inferred.
    """
    base = Path(filename).stem.upper()
    for form, pattern in _SEC_FORM_PATTERNS.items():
        if pattern.search(base):
            return form
    return None


def apply_form_limits(
    candidates: list[Path],
    limits: dict[str, int],
) -> list[tuple[str | None, Path]]:
    """Filter ``candidates`` per ``limits``, returning ``(form, path)`` pairs.

    Files whose form cannot be inferred are tagged ``None`` and bypass the
    per-form caps (unknown forms pass through). Form tags let downstream
    phases report per-form counts even when the output filenames are
    pseudonymised (e.g. ``filing_<hash>.md``).

    Sort order is alphabetical on the filename to keep behaviour
    deterministic across runs (no timestamp dependency).
    """
    # Group by inferred form. "Unknown" forms (None) bypass per-form limits.
    grouped: dict[str | None, list[Path]] = {}
    for c in candidates:
        grouped.setdefault(infer_form(c.name), []).append(c)

    keep: list[tuple[str | None, Path]] = []
    for form, items in grouped.items():
        items_sorted = sorted(items)
        if form is None:
            keep.extend((None, p) for p in items_sorted)
            continue
        cap = limits.get(form, len(items_sorted))
        keep.extend((form, p) for p in items_sorted[:cap])
    keep.sort(key=lambda fp: fp[1].name)
    return keep
