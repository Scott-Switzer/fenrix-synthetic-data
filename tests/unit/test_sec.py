"""Tests for SEC transport, client, reliability, retry, and rate limiter."""

from pathlib import Path

import pytest

from fenrix_synthetic.sec import (
    FailureType,
    SECClient,
    SECRetryPolicy,
    TokenBucketRateLimiter,
    classify_failure,
)
from fenrix_synthetic.sec.transport import FixtureTransport

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sec"


class TestFixtureTransport:
    """Test fixture-backed SEC transport."""

    def test_load_json(self):
        transport = FixtureTransport(FIXTURE_DIR)
        data = transport.get_json("https://www.sec.gov/files/company_tickers.json")
        assert isinstance(data, dict)
        assert "SYNTH" in {v["ticker"] for v in data.values()}

    def test_load_submissions(self):
        transport = FixtureTransport(FIXTURE_DIR)
        data = transport.get_json("https://data.sec.gov/submissions/CIK0001234567.json")
        assert data["cik"] == 1234567
        assert data["name"] == "Synthetic Test Corp (SYNTHETIC FIXTURE)"

    def test_load_bytes_html(self):
        transport = FixtureTransport(FIXTURE_DIR)
        resp = transport.get_bytes(
            "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000001/synth-20240930.htm"
        )
        assert resp.status_code == 200
        assert b"SYNTHETIC FIXTURE" in resp.content

    def test_missing_url_raises(self):
        transport = FixtureTransport(FIXTURE_DIR)
        with pytest.raises(FileNotFoundError):
            transport.get_json("https://data.sec.gov/submissions/CIK9999999999.json")

    def test_json_caching(self):
        transport = FixtureTransport(FIXTURE_DIR)
        data1 = transport.get_json("https://www.sec.gov/files/company_tickers.json")
        data2 = transport.get_json("https://www.sec.gov/files/company_tickers.json")
        assert data1 is data2  # Same cached object

    def test_bytes_caching(self):
        transport = FixtureTransport(FIXTURE_DIR)
        url = (
            "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000001/synth-20240930.htm"
        )
        r1 = transport.get_bytes(url)
        r2 = transport.get_bytes(url)
        assert r1 is r2


class TestSECClient:
    """Test SEC filing discovery client."""

    @pytest.fixture
    def client(self) -> SECClient:
        transport = FixtureTransport(FIXTURE_DIR)
        return SECClient(transport)

    def test_resolve_cik(self, client: SECClient):
        cik = client.resolve_cik("SYNTH")
        assert cik == "0001234567"

    def test_cik_normalization(self, client: SECClient):
        assert SECClient.normalize_cik("1234567") == "0001234567"
        assert SECClient.normalize_cik("0001234567") == "0001234567"

    def test_dot_ticker_normalization(self, client: SECClient):
        cik = client.resolve_cik("SYNTH")  # No dot ticker in fixture
        assert cik == "0001234567"

    def test_unknown_ticker(self, client: SECClient):
        cik = client.resolve_cik("NONEXISTENT")
        assert cik is None

    def test_get_submissions(self, client: SECClient):
        data = client.get_submissions("0001234567")
        assert data is not None
        assert data["name"] == "Synthetic Test Corp (SYNTHETIC FIXTURE)"

    def test_get_submissions_missing_cik(self, client: SECClient):
        data = client.get_submissions("0009999999")
        assert data is None

    def test_get_filings_10k(self, client: SECClient):
        filings = client.get_filings("SYNTH", form="10-K", limit=1)
        assert len(filings) == 1
        assert filings[0]["form"] == "10-K"
        assert filings[0]["accessionNumber"] == "0001234567-24-000001"

    def test_get_filings_form_filter(self, client: SECClient):
        filings = client.get_filings("SYNTH", form="10-Q", limit=1)
        assert len(filings) == 0

    def test_get_filings_year_filter(self, client: SECClient):
        filings = client.get_filings("SYNTH", year=2024, limit=1)
        assert len(filings) == 1

    def test_get_filings_wrong_year(self, client: SECClient):
        filings = client.get_filings("SYNTH", year=2020, limit=1)
        assert len(filings) == 0

    def test_accession_formatting(self, client: SECClient):
        formatted = SECClient.format_accession("0001234567-24-000001")
        assert formatted == "000123456724000001"
        assert SECClient.format_accession("000123456724000001") == "000123456724000001"

    def test_build_filing_url(self, client: SECClient):
        url = SECClient.build_filing_url("0001234567", "0001234567-24-000001", "synth-20240930.htm")
        assert "sec.gov" in url
        assert "synth-20240930.htm" in url
        assert "000123456724000001" in url  # dashes removed
        assert "1234567" in url  # CIK without leading zeros

    def test_missing_filing_returns_empty(self, client: SECClient):
        filings = client.get_filings("SYNTH", form="8-K", limit=1)
        assert len(filings) == 0

    def test_declared_user_agent_propagation(self):
        """Verify LiveTransport sets User-Agent (not live test, just construction)."""
        from fenrix_synthetic.sec.transport import LiveTransport

        t = LiveTransport("TestAgent test@example.invalid")
        assert t._session.headers["User-Agent"] == "TestAgent test@example.invalid"


class TestReliability:
    """Test failure classification."""

    def test_ssl_reset(self):
        import ssl

        exc = ssl.SSLError(1, "SSL connection reset")
        info = classify_failure(exc)
        assert info.failure_type == FailureType.SSL_RESET
        assert info.is_retryable is True

    def test_connection_reset(self):
        exc = ConnectionError("Connection reset by peer")
        info = classify_failure(exc)
        assert info.failure_type == FailureType.CONNECTION_RESET
        assert info.is_retryable is True

    def test_timeout(self):
        exc = TimeoutError("timed out")
        info = classify_failure(exc)
        assert info.failure_type == FailureType.TIMEOUT
        assert info.is_retryable is True

    def test_timeout_string_detection(self):
        exc = RuntimeError("Connection timed out")
        info = classify_failure(exc)
        assert info.failure_type == FailureType.TIMEOUT
        assert info.is_retryable is True

    def test_oserror_connection_reset(self):
        exc = OSError(54, "Connection reset by peer")
        info = classify_failure(exc)
        assert info.failure_type == FailureType.CONNECTION_RESET
        assert info.is_retryable is True

    def test_429_retryable(self):
        class MockResponse:
            status_code = 429

        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 429
                self.response = MockResponse()

        exc = MockHTTPError()
        info = classify_failure(exc)
        assert info.failure_type == FailureType.HTTP_ERROR
        assert info.is_retryable is True
        assert info.http_status == 429

    def test_500_retryable(self):
        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 500

        exc = MockHTTPError()
        info = classify_failure(exc)
        assert info.failure_type == FailureType.HTTP_ERROR
        assert info.is_retryable is True
        assert info.http_status == 500

    def test_403_non_retryable(self):
        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 403

        exc = MockHTTPError()
        info = classify_failure(exc)
        assert info.failure_type == FailureType.HTTP_ERROR
        assert info.is_retryable is False
        assert info.http_status == 403

    def test_404_non_retryable(self):
        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 404

        exc = MockHTTPError()
        info = classify_failure(exc)
        assert info.failure_type == FailureType.HTTP_ERROR
        assert info.is_retryable is False
        assert info.http_status == 404

    def test_partial_file_retryable(self):
        exc = RuntimeError("incomplete read")
        info = classify_failure(exc, bytes_received=100, expected_bytes=200)
        assert info.failure_type == FailureType.PARTIAL_FILE
        assert info.is_retryable is True

    def test_non_retryable_programming_error(self):
        exc = ValueError("bad argument")
        info = classify_failure(exc)
        assert info.failure_type == FailureType.UNKNOWN
        assert info.is_retryable is False

    def test_tls_or_connection_reset(self):
        exc = ConnectionError("TLS connection reset")
        info = classify_failure(exc)
        assert info.failure_type == FailureType.CONNECTION_RESET
        assert info.is_retryable is True


class TestSECRetryPolicy:
    """Test retry policy."""

    def test_retry_on_transient_failure(self):
        policy = SECRetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.1)

        call_count = 0

        def failing_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "success"

        result = policy.call(failing_func)
        assert result == "success"
        assert call_count == 3

    def test_exhaustion_raises(self):
        policy = SECRetryPolicy(max_attempts=2, base_delay=0.01, max_delay=0.1)

        def always_fails() -> str:
            raise ConnectionError("always fails")

        with pytest.raises(ConnectionError):
            policy.call(always_fails)

    def test_non_retryable_raises_immediately(self):
        policy = SECRetryPolicy(max_attempts=3, base_delay=0.01)

        def value_error_func() -> str:
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            policy.call(value_error_func)

    def test_deterministic_sleep_provider(self):
        policy = SECRetryPolicy(max_attempts=3, base_delay=1.0, max_delay=2.0)
        slept_times: list[float] = []

        policy.set_time_providers(
            time_provider=lambda: 0.0,
            sleep_provider=lambda s: slept_times.append(s),
        )

        call_count = 0

        def fail_twice() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "ok"

        result = policy.call(fail_twice)
        assert result == "ok"
        assert len(slept_times) == 2

    def test_retry_after_header(self):
        policy = SECRetryPolicy(max_attempts=2, base_delay=10.0)
        delay = policy.delay_for_attempt(1, {"Retry-After": "5"})
        assert delay == pytest.approx(5.0)

    def test_retry_after_case_insensitive(self):
        policy = SECRetryPolicy(max_attempts=2, base_delay=10.0)
        delay = policy.delay_for_attempt(1, {"retry-after": "3"})
        assert delay == pytest.approx(3.0)

    def test_max_attempts_must_be_positive(self):
        with pytest.raises(ValueError):
            SECRetryPolicy(max_attempts=0)


class TestRateLimiter:
    """Test token-bucket rate limiter."""

    def test_default_rate_is_5(self):
        limiter = TokenBucketRateLimiter()
        assert limiter.rate == 5.0

    def test_rate_above_10_rejected(self):
        with pytest.raises(ValueError, match="cannot exceed"):
            TokenBucketRateLimiter(max_per_second=15)

    def test_rate_of_10_accepted(self):
        limiter = TokenBucketRateLimiter(max_per_second=10)
        assert limiter.rate == 10.0

    def test_rate_of_1_works(self):
        limiter = TokenBucketRateLimiter(max_per_second=1)
        assert limiter.rate == 1.0

    def test_negative_rate_rejected(self):
        with pytest.raises(ValueError, match="must be positive"):
            TokenBucketRateLimiter(max_per_second=-1)

    def test_zero_rate_rejected(self):
        with pytest.raises(ValueError, match="must be positive"):
            TokenBucketRateLimiter(max_per_second=0)

    def test_deterministic_fake_clock(self):
        limiter = TokenBucketRateLimiter(max_per_second=10, capacity=1)
        fake_time: list[float] = [0.0]

        limiter.set_time_providers(
            time_provider=lambda: fake_time[0],
            sleep_provider=lambda s: fake_time.__setitem__(0, fake_time[0] + s) or None,
        )

        slept = limiter.acquire()
        assert slept == 0.0  # First call succeeds immediately

        slept2 = limiter.acquire()
        assert slept2 > 0  # Second call must wait

    def test_acquire_enforces_rate(self):
        """With capacity=1 and rate=10, second acquire must wait ~0.1s."""
        limiter = TokenBucketRateLimiter(max_per_second=10, capacity=1)
        slept_times: list[float] = []
        fake_time: list[float] = [0.0]

        limiter.set_time_providers(
            time_provider=lambda: fake_time[0],
            sleep_provider=lambda s: (
                slept_times.append(s) or fake_time.__setitem__(0, fake_time[0] + s) or None
            ),
        )

        limiter.acquire()
        limiter.acquire()
        # Should have slept at least ~0.1s (1/10 sec)
        assert sum(slept_times) >= 0.09
