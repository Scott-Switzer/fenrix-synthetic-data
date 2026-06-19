"""Private data boundary subsystem.

Enforces that all real source data, identity mappings, and private
intermediate artifacts live under FENRIX_PRIVATE_ROOT outside the
Git repository.
"""

from .private_root import (
    PrivateBoundaryError,
    ensure_private_root,
    is_in_repo,
    private_path,
    redacted_diagnostic_command,
    resolve_private_root,
    sanitize_exception_message,
    sanitize_path_for_log,
    validate_no_private_data_in_snapshot,
)

__all__ = [
    "PrivateBoundaryError",
    "ensure_private_root",
    "is_in_repo",
    "private_path",
    "redacted_diagnostic_command",
    "resolve_private_root",
    "sanitize_exception_message",
    "sanitize_path_for_log",
    "validate_no_private_data_in_snapshot",
]
