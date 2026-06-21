"""Reanonymize-run package: orchestrator and limits parser.

Public surface::

    ReanonymizeOrchestrator     # main phase orchestrator
    parse_form_limits           # 10-K:1,10-Q:1,8-K:1 -> dict
    apply_form_limits           # filter SEC filenames per form limit

The CLI command is bound in :mod:`fenrix_synthetic.cli` as
``fenrix-synth reanonymize-run``.
"""

from .limits import apply_form_limits, parse_form_limits
from .orchestrator import (
    InvalidSourceRunError,
    ReanonymizeOrchestrator,
    RunContext,
)

__all__ = [
    "InvalidSourceRunError",
    "ReanonymizeOrchestrator",
    "RunContext",
    "apply_form_limits",
    "parse_form_limits",
]
