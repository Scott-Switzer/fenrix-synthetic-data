"""Failure classification for SEC download errors.

Adapted from Project Portfolio Engine ingestion/secedgar/reliability.py
(commit aa31d1e, file last modified af49ce0).

Provides typed failure categories and retryability decisions for
HTTP, connection, TLS, timeout, and partial-response failures.
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class FailureType(StrEnum):
    """Classification of SEC access failures."""

    SSL_RESET = "ssl_reset"
    CONNECTION_RESET = "connection_reset"
    TIMEOUT = "timeout"
    PARTIAL_FILE = "partial_file"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    HTTP_ERROR = "http_error"
    UNKNOWN = "unknown"


@dataclass
class FailureInfo:
    """Structured failure classification result."""

    failure_type: FailureType
    message: str
    is_retryable: bool
    http_status: int | None = None
    attempt: int = 0
    max_attempts: int = 0


def classify_failure(
    exc: BaseException,
    bytes_received: int = 0,
    expected_bytes: int = 0,
) -> FailureInfo:
    """Classify an exception into a typed failure category."""
    msg = str(exc)
    exc_type = type(exc).__name__

    if isinstance(exc, (ssl.SSLError, ConnectionError)):
        if isinstance(exc, ssl.SSLError):
            return FailureInfo(
                failure_type=FailureType.SSL_RESET,
                message=msg,
                is_retryable=True,
            )
        return FailureInfo(
            failure_type=FailureType.CONNECTION_RESET,
            message=msg,
            is_retryable=True,
        )

    if isinstance(exc, TimeoutError) or "timeout" in msg.lower() or "timed out" in msg.lower():
        return FailureInfo(
            failure_type=FailureType.TIMEOUT,
            message=msg,
            is_retryable=True,
        )

    if isinstance(exc, OSError):
        errno = getattr(exc, "errno", None)
        if errno in (54, 104, 10054, 10053):
            return FailureInfo(
                failure_type=FailureType.CONNECTION_RESET,
                message=msg,
                is_retryable=True,
            )

    if hasattr(exc, "status_code") or hasattr(getattr(exc, "response", None), "status_code"):
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "response", None), "status_code", None
        )
        if isinstance(status, int):
            if status in (429, 500, 502, 503, 504):
                return FailureInfo(
                    failure_type=FailureType.HTTP_ERROR,
                    message=msg,
                    is_retryable=True,
                    http_status=status,
                )
            return FailureInfo(
                failure_type=FailureType.HTTP_ERROR,
                message=msg,
                is_retryable=False,
                http_status=status,
            )

    if bytes_received > 0 and expected_bytes > 0 and bytes_received < expected_bytes:
        return FailureInfo(
            failure_type=FailureType.PARTIAL_FILE,
            message=f"Received {bytes_received}/{expected_bytes} bytes",
            is_retryable=True,
        )

    return FailureInfo(
        failure_type=FailureType.UNKNOWN,
        message=f"{exc_type}: {msg}",
        is_retryable=False,
    )
