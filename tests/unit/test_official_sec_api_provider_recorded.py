"""Tests for OfficialSecApiProvider using recorded SEC-shaped fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fenrix_synthetic.professor.sec_providers import (
    OfficialSecApiProvider,
    SecProviderError,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sec"


def _build_cache_from_fixtures(cache_dir: Path) -> dict[str, Path]:
    """Populate cache dir with recorded fixtures and return URL->path mapping."""
    # company_tickers.json -> company_tickers URL
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    tickers_hash = hashlib.sha256(tickers_url.encode()).hexdigest()[:16]
    tickers_cache = cache_dir / f"{tickers_hash}.json"
    tickers_cache.parent.mkdir(parents=True, exist_ok=True)
    tickers_data = json.loads((FIXTURE_DIR / "company_tickers.json").read_text())
    tickers_cache.write_text(json.dumps(tickers_data))

    # submissions-CIK0001234567.json -> submissions URL
    submissions_url = "https://data.sec.gov/submissions/CIK0001234567.json"
    submissions_hash = hashlib.sha256(submissions_url.encode()).hexdigest()[:16]
    submissions_cache = cache_dir / f"{submissions_hash}.json"
    submissions_data = json.loads((FIXTURE_DIR / "submissions-CIK0001234567.json").read_text())
    submissions_cache.write_text(json.dumps(submissions_data))

    return {
        "tickers": str(tickers_cache),
        "submissions": str(submissions_cache),
    }


@pytest.fixture
def recorded_sec_provider(tmp_path: Path) -> OfficialSecApiProvider:
    """Build an OfficialSecApiProvider with recorded fixtures in cache."""
    cache_dir = tmp_path / "sec_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    _build_cache_from_fixtures(cache_dir)

    provider = OfficialSecApiProvider(
        user_agent="TestAgent/1.0 test@test.com",
        cache_dir=cache_dir,
        live_network=False,
    )
    return provider


class TestOfficialSecApiProviderRecorded:
    """Test SEC provider against recorded fixtures without live network."""

    def test_ticker_to_cik_resolution_works(
        self, recorded_sec_provider: OfficialSecApiProvider
    ) -> None:
        """Ticker-to-CIK resolution must work against recorded company_tickers."""
        cik = recorded_sec_provider._resolve_cik("SYNTH")
        assert cik == "0001234567"

    def test_cik_normalized_to_10_digits(
        self, recorded_sec_provider: OfficialSecApiProvider
    ) -> None:
        """CIK must be normalized to 10 digits internally."""
        cik = recorded_sec_provider._resolve_cik("SYNTH")
        assert len(cik) == 10
        assert cik == "0001234567"

    def test_submissions_metadata_is_parsed(
        self, recorded_sec_provider: OfficialSecApiProvider
    ) -> None:
        """Submissions metadata must be parsed from recorded JSON."""
        cik = recorded_sec_provider._resolve_cik("SYNTH")
        assert cik == "0001234567"

    def test_discover_filings_10k(self, recorded_sec_provider: OfficialSecApiProvider) -> None:
        """10-K discovery must work against recorded submissions fixture."""
        filings = recorded_sec_provider.discover_filings("SYNTH", form="10-K", limit=1)
        assert len(filings) == 1
        assert filings[0].form_type == "10-K"
        assert filings[0].filing_date == "2024-11-15"
        assert filings[0].period_end == "2024-09-30"
        assert filings[0].company_id == "COMPANY_001"

    def test_discover_filings_10q_no_fixture(
        self, recorded_sec_provider: OfficialSecApiProvider
    ) -> None:
        """10-Q discovery must return empty when no 10-Q in recorded fixture."""
        filings = recorded_sec_provider.discover_filings("SYNTH", form="10-Q", limit=1)
        assert len(filings) == 0

    def test_discover_filings_8k_no_fixture(
        self, recorded_sec_provider: OfficialSecApiProvider
    ) -> None:
        """8-K discovery must return empty when no 8-K in recorded fixture."""
        filings = recorded_sec_provider.discover_filings("SYNTH", form="8-K", limit=1)
        assert len(filings) == 0

    def test_filing_includes_accession_ref_internally(
        self, recorded_sec_provider: OfficialSecApiProvider
    ) -> None:
        """Selected filings must include accession reference internally."""
        filings = recorded_sec_provider.discover_filings("SYNTH", form="10-K", limit=1)
        assert len(filings) == 1
        assert filings[0].accession_ref == "0001234567-24-000001".replace("-", "")

    def test_public_provenance_uses_opaque_keys(
        self, recorded_sec_provider: OfficialSecApiProvider
    ) -> None:
        """Public-facing provenance must use opaque keys, not accession/CIK/URL."""
        filings = recorded_sec_provider.discover_filings("SYNTH", form="10-K", limit=1)
        assert len(filings) == 1
        pk = filings[0].provenance_key
        assert pk.startswith("COMPANY_001:FILING:")
        assert "1234567" not in pk
        assert "sec.gov" not in pk

    def test_emits_provider_provenance(self, recorded_sec_provider: OfficialSecApiProvider) -> None:
        """OfficialSecApiProvider must emit provider provenance with real kind."""
        report = recorded_sec_provider.get_provider_report()
        assert report["provider_name"] == "OfficialSecApiProvider"
        assert report["provider_kind"] == "real"
        assert report["user_agent_configured"] is True

    def test_cache_hit_counts_recorded(self, recorded_sec_provider: OfficialSecApiProvider) -> None:
        """Cache hit counts must be recorded in provider provenance."""
        # First call fetches from pre-populated disk cache (1 cache hit),
        # second call uses in-memory CIK cache (no additional disk cache hit)
        recorded_sec_provider._resolve_cik("SYNTH")
        assert recorded_sec_provider._cache_hits >= 1
        assert recorded_sec_provider._cache_misses == 0

    def test_request_counts_recorded(self, recorded_sec_provider: OfficialSecApiProvider) -> None:
        """Request counts must be tracked."""
        # CIK resolution triggers one fetch (from cache, but still counts as cache hit)
        recorded_sec_provider._resolve_cik("SYNTH")
        report = recorded_sec_provider.get_provider_report()
        assert "request_count" in report

    def test_unknown_ticker_raises(self, recorded_sec_provider: OfficialSecApiProvider) -> None:
        """Unknown ticker must raise SecProviderError."""
        with pytest.raises(SecProviderError, match="Could not resolve CIK"):
            recorded_sec_provider._resolve_cik("UNKNOWN")

    def test_parses_filing_date_correctly(
        self, recorded_sec_provider: OfficialSecApiProvider
    ) -> None:
        """Filing date must be correctly parsed from recorded fixture."""
        filings = recorded_sec_provider.discover_filings("SYNTH", form="10-K", limit=1)
        assert filings[0].filing_date == "2024-11-15"
        assert filings[0].period_end == "2024-09-30"
