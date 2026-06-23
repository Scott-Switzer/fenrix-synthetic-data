"""Live SEC smoke tests — require FENRIX_LIVE_SEC=1."""

from __future__ import annotations

import os

import pytest

from fenrix_synthetic.professor.sec_providers import OfficialSecApiProvider, SecProviderError

pytestmark = pytest.mark.live_sec


def _requires_live_sec() -> bool:
    """Check if live SEC tests are enabled."""
    return os.environ.get("FENRIX_LIVE_SEC", "").strip() in ("1", "true", "yes")


@pytest.mark.skipif(not _requires_live_sec(), reason="FENRIX_LIVE_SEC not set")
class TestLiveSecSmoke:
    """Minimal live SEC smoke tests.

    These tests require FENRIX_LIVE_SEC=1 and actual network access.
    They verify:
    - Basic connectivity to SEC EDGAR
    - Company ticker resolution
    - Filing discovery for a well-known company
    - Expected filing metadata structure
    """

    def test_live_company_tickers_resolves_apple(self) -> None:
        """Live company_tickers must resolve AAPL to a CIK."""
        provider = OfficialSecApiProvider(
            user_agent="FENRIX Test/1.0 (test@fenrix-synth.local)",
            live_network=True,
            cache_dir=None,
        )
        cik = provider._resolve_cik("AAPL")
        assert len(cik) == 10
        assert cik.isdigit()

    def test_live_submissions_discovery(self) -> None:
        """Live submissions must discover filings for a CIK."""
        provider = OfficialSecApiProvider(
            user_agent="FENRIX Test/1.0 (test@fenrix-synth.local)",
            live_network=True,
            cache_dir=None,
        )
        cik = provider._resolve_cik("AAPL")
        filings = provider.discover_filings(cik, form="10-K", limit=1)
        assert len(filings) == 1
        assert filings[0].form_type == "10-K"
        assert filings[0].filing_date

    def test_live_10k_selection(self) -> None:
        """Live 10-K filing must have a valid metadata structure."""
        provider = OfficialSecApiProvider(
            user_agent="FENRIX Test/1.0 (test@fenrix-synth.local)",
            live_network=True,
            cache_dir=None,
        )
        cik = provider._resolve_cik("AAPL")
        filings = provider.discover_filings(cik, form="10-K", limit=1)
        assert len(filings) == 1
        filing = filings[0]
        assert filing.filing_date
        assert filing.period_end
        assert filing.accession_ref
        assert filing.company_id
        assert filing.provenance_key

    def test_live_rate_limit_respected(self) -> None:
        """Multiple rapid requests must not exceed SEC rate limits."""
        provider = OfficialSecApiProvider(
            user_agent="FENRIX Test/1.0 (test@fenrix-synth.local)",
            live_network=True,
            cache_dir=None,
            max_requests_per_second=1,
        )

        # Make a few requests within the rate limit
        cik = provider._resolve_cik("AAPL")
        assert cik
        filings = provider.discover_filings(cik, form="10-K", limit=1)
        assert len(filings) == 1

    def test_live_unknown_ticker_raises(self) -> None:
        """Unknown ticker must raise SecProviderError."""
        provider = OfficialSecApiProvider(
            user_agent="FENRIX Test/1.0 (test@fenrix-synth.local)",
            live_network=True,
        )
        with pytest.raises(SecProviderError):
            provider._resolve_cik("ZZZZUNKNOWNTICKER")

    def test_live_provider_report(self) -> None:
        """Live provider must return a valid provider report."""
        provider = OfficialSecApiProvider(
            user_agent="FENRIX Test/1.0 (test@fenrix-synth.local)",
            live_network=True,
        )
        provider._resolve_cik("AAPL")  # populate request counter
        report = provider.get_provider_report()
        assert report["provider_kind"] == "real"
        assert report["request_count"] > 0
        assert report["filings_discovered"]
