"""Token-bucket rate limiter for SEC access.

Adapted from Project Portfolio Engine ingestion/secedgar/rate_limiter.py
(commit aa31d1e, file last modified 8df5619).

Thread-safe token-bucket limiter that enforces a maximum request rate
and a minimum inter-request interval.  Default is 5 requests/second.
The configured rate may never exceed 10 requests/second to honour SEC
fair-access policy.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucketRateLimiter:
    """Thread-safe token-bucket rate limiter.

    Parameters
    ----------
    max_per_second:
        Maximum sustained request rate.  Must be > 0 and <= 10.
        Defaults to 5.0.
    capacity:
        Burst capacity.  Defaults to ``max_per_second`` (no bursting).
    """

    MAX_ALLOWED_RATE = 10.0

    def __init__(
        self,
        max_per_second: float = 5.0,
        capacity: float | None = None,
    ) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        if max_per_second > self.MAX_ALLOWED_RATE:
            raise ValueError(
                f"max_per_second cannot exceed {self.MAX_ALLOWED_RATE} (SEC fair-access policy)"
            )
        self.rate = float(max_per_second)
        self.capacity = float(capacity if capacity is not None else max_per_second)
        self._tokens = self.capacity
        self._min_interval = 1.0 / self.rate
        self._last_acquire: float = -1.0  # sentinel: no previous request
        self._updated: float = 0.0
        self._lock = threading.Lock()
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
        self._updated = self._time_provider()

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until ``tokens`` are available. Returns the slept duration."""
        slept = 0.0
        with self._lock:
            while True:
                now = self._time_provider()
                if self._updated == 0.0:
                    self._updated = now
                self._tokens = min(
                    self.capacity,
                    self._tokens + (now - self._updated) * self.rate,
                )
                self._updated = now

                if self._last_acquire < 0:
                    since_last = self._min_interval * 10
                    interval_wait = 0.0
                else:
                    since_last = now - self._last_acquire
                    interval_wait = max(0.0, self._min_interval - since_last)

                if self._tokens >= tokens and interval_wait <= 0:
                    self._tokens -= tokens
                    self._last_acquire = now
                    return slept

                token_wait = 0.0 if self._tokens >= tokens else (tokens - self._tokens) / self.rate
                wait = max(interval_wait, token_wait)
                if wait > 0:
                    self._sleep_provider(wait)
                    slept += wait
