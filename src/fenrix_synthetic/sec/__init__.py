"""SEC EDGAR access package.

Provides SEC transport abstraction, filing discovery, rate limiting,
failure classification, and retry for SEC filing retrieval.
"""

from .client import SECClient
from .rate_limiter import TokenBucketRateLimiter
from .reliability import FailureInfo, FailureType, classify_failure
from .retry import SECRetryPolicy
from .transport import FixtureTransport, LiveTransport, SecTransport

__all__ = [
    "SecTransport",
    "LiveTransport",
    "FixtureTransport",
    "SECClient",
    "TokenBucketRateLimiter",
    "FailureInfo",
    "FailureType",
    "classify_failure",
    "SECRetryPolicy",
]
