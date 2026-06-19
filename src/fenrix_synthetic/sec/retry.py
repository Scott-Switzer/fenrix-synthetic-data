"""Retry policy for SEC access.

Adapted from Zion Terminal agents/retrieval/retry.py (commit e75ae57).

Provides a configurable retry policy with exponential backoff,
deterministic test control, and integration with the Fenrix failure
classification system.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

from .reliability import FailureType, classify_failure

T = TypeVar("T")

_RETRYABLE_FAILURES = {
    FailureType.SSL_RESET,
    FailureType.CONNECTION_RESET,
    FailureType.TIMEOUT,
    FailureType.PARTIAL_FILE,
    FailureType.HTTP_ERROR,
}


class SECRetryPolicy:
    """Configurable retry policy for SEC requests.

    Parameters
    ----------
    max_attempts:
        Maximum number of attempts (default 3).
    base_delay:
        Base delay in seconds for exponential backoff (default 1.0).
    max_delay:
        Maximum delay in seconds (default 30.0).
    jitter_factor:
        Random jitter as fraction of delay (default 0.25).
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter_factor: float = 0.25,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if base_delay <= 0:
            raise ValueError("base_delay must be positive")
        if max_delay < base_delay:
            raise ValueError("max_delay must be >= base_delay")
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter_factor = jitter_factor
        self._time_provider: Callable[[], float] = time.monotonic
        self._sleep_provider: Callable[[float], None] = time.sleep

    def set_time_providers(
        self,
        time_provider: Callable[[], float],
        sleep_provider: Callable[[float], None],
    ) -> None:
        """Override time/sleep for deterministic testing."""
        self._time_provider = time_provider
        self._sleep_provider = sleep_provider

    def is_retryable(self, exc: BaseException) -> bool:
        """Determine if an exception is retryable."""
        info = classify_failure(exc)
        return info.is_retryable

    def delay_for_attempt(
        self, attempt: int, response_headers: dict[str, str] | None = None
    ) -> float:
        """Compute delay for a given attempt number (1-indexed).

        Honors Retry-After header if present.
        """
        if response_headers:
            retry_after = response_headers.get("Retry-After") or response_headers.get("retry-after")
            if retry_after:
                try:
                    return float(retry_after)
                except (ValueError, TypeError):
                    pass

        delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        jitter = random.uniform(0, self.jitter_factor * delay)
        return delay + jitter  # type: ignore[no-any-return]

    def call(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Call ``func`` with retry logic. Raises last exception on exhaustion."""
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except BaseException as exc:
                last_exc = exc
                if attempt >= self.max_attempts or not self.is_retryable(exc):
                    raise
                delay = self.delay_for_attempt(attempt)
                self._sleep_provider(delay)
        # Should not reach here, but satisfy type checker
        if last_exc:
            raise last_exc
        raise RuntimeError("Unreachable")
