"""Centralized Click-native exception hierarchy for the Phase 5A CLI surface.

Per the close-out spec, every command must use the same exit-code matrix:

* Invalid `evInputError        -> 2
* `PrivacyFailureError`  -> 3
* `ExecutionFailursError` -> 4
* `IneligibleVariantError` -> 5

All four are subclasses of `ClickException` via `Phase5AClickError` so
that Click's own renderer drops us out with the configured exit code
and a sanitized one-line message — *without* dumping a Python
traceback that could leak internal module names, paths, or even
hashed identifiers.

Use `raise Phase5AClickError(msg, exit_code=N)` once per command, or
subclass one of these named errors and raise that.
"""

from __future__ import annotations

import click


class InvalidInputError(click.ClickException):
    exit_code = 2


class PrivacyFailureError(click.ClickException):
    exit_code = 3


class ExecutionFailureError(click.ClickException):
    exit_code = 4


class IneligibleVariantError(click.ClickException):
    exit_code = 5


class Phase5AClickError(click.ClickException):
    """Click-native wrapper that propagates the supplied exit code.

    The constructor takes both a message and an integer exit code. Use
    the named subclasses above (InvalidInputError / PrivacyFailureError
    / ExecutionFailureError / IneligibleVariantError) at the throw site
    rather than constructing this directly.
    """

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ── Re-exports so callers can `from fenrix_synthetic.cli_errors import X`


__all__ = [
    "InvalidInputError",
    "PrivacyFailureError",
    "ExecutionFailureError",
    "IneligibleVariantError",
    "Phase5AClickError",
]
