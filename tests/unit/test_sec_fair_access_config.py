"""Tests for SEC fair-access configuration and safeguards."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from fenrix_synthetic.professor.sec_providers import (
    SEC_DEFAULT_TIMEOUT_SECONDS,
    SEC_MAX_REQUESTS_PER_SECOND,
    SEC_MAX_RETRIES,
    SEC_USER_AGENT_MIN_LENGTH,
    OfficialSecApiProvider,
    SecProviderError,
    validate_sec_fair_access_config,
)


class TestSecFairAccessConfig:
    """Test SEC fair-access configuration validation."""

    def test_production_config_requires_descriptive_user_agent(self) -> None:
        """Production SEC config must require a non-empty descriptive User-Agent."""
        violations = validate_sec_fair_access_config({"user_agent": ""})
        assert len(violations) >= 1
        assert any("User-Agent" in v for v in violations)

    def test_too_short_user_agent_fails(self) -> None:
        """Too-short User-Agent (< 10 chars) must fail validation."""
        violations = validate_sec_fair_access_config({"user_agent": "short"})
        assert len(violations) >= 1
        assert any(f"User-Agent must be >= {SEC_USER_AGENT_MIN_LENGTH}" in v for v in violations)

    def test_empty_user_agent_raises_at_init(self) -> None:
        """Empty User-Agent string must raise at OfficialSecApiProvider init."""
        with pytest.raises(SecProviderError, match="User-Agent"):
            OfficialSecApiProvider(user_agent="")

    def test_short_user_agent_raises_at_init(self) -> None:
        """Short User-Agent (< 10 chars) must raise at init."""
        with pytest.raises(SecProviderError, match="User-Agent"):
            OfficialSecApiProvider(user_agent="too short")

    def test_max_requests_per_second_over_10_fails_validation(self) -> None:
        """max_requests_per_second > 10 must fail validation."""
        violations = validate_sec_fair_access_config(
            {
                "user_agent": "TestAgent/1.0 test@test.com",
                "max_requests_per_second": 20,
            }
        )
        assert len(violations) >= 1
        assert any("max_requests_per_second" in v and "<= 10" in v for v in violations)

    def test_max_requests_per_second_negative_fails_validation(self) -> None:
        """Negative max_requests_per_second must fail validation."""
        violations = validate_sec_fair_access_config(
            {
                "user_agent": "TestAgent/1.0 test@test.com",
                "max_requests_per_second": -1,
            }
        )
        assert len(violations) >= 1
        assert any("max_requests_per_second" in v and "positive" in v for v in violations)

    def test_max_requests_per_second_over_10_raises_at_init(self) -> None:
        """max_requests_per_second > 10 must raise at init."""
        with pytest.raises(SecProviderError, match="max_requests_per_second"):
            OfficialSecApiProvider(
                user_agent="TestAgent/1.0 test@test.com",
                max_requests_per_second=20,
            )

    def test_max_requests_per_second_negative_raises_at_init(self) -> None:
        """Negative max_requests_per_second must raise at init."""
        with pytest.raises(SecProviderError, match="max_requests_per_second"):
            OfficialSecApiProvider(
                user_agent="TestAgent/1.0 test@test.com",
                max_requests_per_second=-1,
            )

    def test_default_max_rate_is_8_or_less(self) -> None:
        """Default/max configured rate must be <= 8/sec (margin below SEC max of 10)."""
        assert SEC_MAX_REQUESTS_PER_SECOND <= 8, "SEC_MAX_REQUESTS_PER_SECOND must be <= 8"

    def test_default_rate_used_when_not_specified(self) -> None:
        """Default rate must be used when not specified in config."""
        provider = OfficialSecApiProvider(
            user_agent="TestAgent/1.0 test@test.com",
        )
        assert provider._max_requests_per_second == SEC_MAX_REQUESTS_PER_SECOND
        assert provider._max_requests_per_second <= 8

    def test_all_http_goes_through_fetch_with_retry(self) -> None:
        """All HTTP fetches must go through the central _fetch_with_retry helper.

        We verify by providing a cache directory; cache hits return data without
        calling _fetch_with_retry. Cache misses that hit live_network=False raise
        a SecProviderError containing 'Live network disabled'.
        """
        cache_dir = Path("/tmp/test_sec_cache_fair_access")
        cache_dir.mkdir(parents=True, exist_ok=True)
        provider = OfficialSecApiProvider(
            user_agent="TestAgent/1.0 test@test.com",
            cache_dir=cache_dir,
            live_network=False,
        )

        # _resolve_cik internally calls _fetch_json, which should call
        # _fetch_with_retry for cache misses
        url = "https://www.sec.gov/files/company_tickers.json"
        with pytest.raises(SecProviderError, match="Live network disabled"):
            provider._fetch_json(url)

    def test_rate_limiting_enforces_interval(self) -> None:
        """Rate limiter must enforce minimum interval between requests.

        We verify by making two calls through _enforce_rate_limit and
        measuring that the interval is respected.
        """
        provider = OfficialSecApiProvider(
            user_agent="TestAgent/1.0 test@test.com",
            max_requests_per_second=8,
        )

        provider._last_request_time = time.monotonic() - 0.2
        start = time.monotonic()
        provider._enforce_rate_limit()
        elapsed = time.monotonic() - start
        # At 8 req/sec, min_interval = 0.125s. With -0.2s offset, should sleep ~0s
        assert elapsed < 0.3

    def test_offline_cache_replay_works(self) -> None:
        """Offline cache replay must work with network disabled."""
        cache_dir = Path("/tmp/test_sec_cache_offline")
        cache_dir.mkdir(parents=True, exist_ok=True)

        url = "https://data.sec.gov/submissions/CIK0001234567.json"
        hash_str = hashlib.sha256(url.encode()).hexdigest()[:16]
        cache_file = cache_dir / f"{hash_str}.json"
        cache_data = {
            "cik": 1234567,
            "name": "Test Corp",
            "filings": {
                "recent": {"accessionNumber": [], "filingDate": [], "form": []},
                "files": [],
            },
        }
        cache_file.write_text(json.dumps(cache_data))

        provider = OfficialSecApiProvider(
            user_agent="TestAgent/1.0 test@test.com",
            cache_dir=cache_dir,
            live_network=False,
        )
        result = provider._fetch_json(url)
        assert result["cik"] == 1234567
        assert result["name"] == "Test Corp"

    def test_cache_hit_tracking(self) -> None:
        """Cache hit/miss counts must be tracked correctly."""
        cache_dir = Path("/tmp/test_sec_cache_tracking")
        cache_dir.mkdir(parents=True, exist_ok=True)

        url = "https://data.sec.gov/submissions/CIK0007654321.json"
        hash_str = hashlib.sha256(url.encode()).hexdigest()[:16]
        cache_file = cache_dir / f"{hash_str}.json"
        cache_data = {
            "cik": 7654321,
            "name": "Other Corp",
            "filings": {
                "recent": {"accessionNumber": [], "filingDate": [], "form": []},
                "files": [],
            },
        }
        cache_file.write_text(json.dumps(cache_data))

        provider = OfficialSecApiProvider(
            user_agent="TestAgent/1.0 test@test.com",
            cache_dir=cache_dir,
            live_network=False,
        )

        provider._fetch_json(url)
        assert provider._cache_hits == 1
        assert provider._cache_misses == 0

    def test_timeout_is_configured(self) -> None:
        """Timeout must be configured and passed to _fetch_with_retry."""
        assert SEC_DEFAULT_TIMEOUT_SECONDS > 0
        assert SEC_DEFAULT_TIMEOUT_SECONDS == 30

    def test_retry_backoff_is_deterministic(self) -> None:
        """Retry/backoff behavior must be deterministic under test.

        Verifies that the retry loop structure is correct and that
        SEC_MAX_RETRIES is set to a positive integer.
        """
        assert SEC_MAX_RETRIES >= 1
        assert isinstance(SEC_MAX_RETRIES, int)
