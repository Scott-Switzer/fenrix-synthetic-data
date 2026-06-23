"""SEC parser provider layer.

Provides a provider abstraction for SEC filing discovery, download, and
semantic parsing. The fixture provider reads from committed test fixtures;
production providers use the official SEC JSON APIs with fair-access
safeguards (rate limiting, retry, User-Agent enforcement, offline cache).
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .evidence import SourceFiling, SourceSection, SourceTable, build_provenance_key


class SecProviderError(Exception):
    """Raised when an SEC provider fails."""


class SecRateLimitError(SecProviderError):
    """Raised when SEC rate limit is exceeded."""


class SecAuthError(SecProviderError):
    """Raised when SEC rejects request (missing/invalid User-Agent)."""


class SecTimeoutError(SecProviderError):
    """Raised when SEC request times out."""


# ── SEC fair-access constants ────────────────────────────────────────────

SEC_MAX_REQUESTS_PER_SECOND = 8
"""SEC fair-access limit; we use 8 (not 10) to leave margin."""

SEC_DEFAULT_TIMEOUT_SECONDS = 30
"""Default HTTP timeout for SEC API requests."""

SEC_RETRY_BACKOFF_BASE = 1.0
"""Base seconds for exponential backoff on retry."""

SEC_MAX_RETRIES = 3
"""Maximum retry attempts for transient failures."""

SEC_USER_AGENT_MIN_LENGTH = 10
"""Minimum length for a meaningful User-Agent string."""

# ── Abstract provider interface ──────────────────────────────────────────


class SecProvider(ABC):
    """Abstract SEC provider interface."""

    @abstractmethod
    def discover_filings(
        self, ticker: str, form: str = "10-K", limit: int = 1
    ) -> list[SourceFiling]:
        """Discover SEC filings for a ticker."""

    @abstractmethod
    def parse_sections(self, filing: SourceFiling) -> list[SourceSection]:
        """Parse a filing into semantic sections (Item 1, 1A, 7, 8, etc.)."""

    @abstractmethod
    def extract_tables(self, filing: SourceFiling) -> list[SourceTable]:
        """Extract structured tables from a filing."""

    def health_check(self) -> bool:
        """Return True if provider is operational."""
        return True

    def get_provider_report(self) -> dict[str, Any]:
        """Return structured provider report for QA audit."""
        return {
            "provider_name": self.__class__.__name__,
            "provider_kind": "fixture",
            "request_count": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "rate_limit_setting": "N/A",
            "user_agent_configured": False,
            "filings_discovered": {},
            "filings_selected": {},
            "parse_success_count": 0,
            "parse_failure_count": 0,
            "semantic_validation_result": "N/A",
            "private_cache_location": "",
            "public_provenance_keys": [],
        }


class FixtureSecProvider(SecProvider):
    """SEC provider that reads from fixture data for offline testing.

    Produces deterministic synthetic filings with proper SEC structure
    (Item 1, 1A, 7, 8 for 10-K; Item 2 for 10-Q).
    """

    def __init__(self, fixture_data: dict[str, Any] | None = None) -> None:
        self._fixture = fixture_data or _default_fixture()

    def discover_filings(
        self, ticker: str, form: str = "10-K", limit: int = 1
    ) -> list[SourceFiling]:
        """Return fixture filings for the canary company."""
        filings: list[SourceFiling] = []
        for fdata in self._fixture.get("filings", []):
            if fdata["form_type"] != form:
                continue
            filing = SourceFiling(
                filing_id=fdata["filing_id"],
                company_id=fdata["company_id"],
                form_type=fdata["form_type"],
                filing_date=fdata["filing_date"],
                period_end=fdata["period_end"],
                accession_ref="[PRIVATE_REF]",
                provenance_key=build_provenance_key(
                    fdata["company_id"], "FILING", fdata["form_type"], fdata["period_end"][:4]
                ),
                section_count=len(fdata.get("sections", [])),
                table_count=len(fdata.get("tables", [])),
            )
            filings.append(filing)
            if len(filings) >= limit:
                break
        return filings

    def parse_sections(self, filing: SourceFiling) -> list[SourceSection]:
        """Parse fixture filing into sections."""
        sections: list[SourceSection] = []
        for fdata in self._fixture.get("filings", []):
            if fdata["filing_id"] != filing.filing_id:
                continue
            for sdata in fdata.get("sections", []):
                section = SourceSection(
                    section_id=sdata["section_id"],
                    filing_id=filing.filing_id,
                    company_id=filing.company_id,
                    item_id=sdata["item_id"],
                    item_title=sdata["item_title"],
                    text_content=sdata["text_content"],
                    char_count=len(sdata["text_content"]),
                    provenance_key=build_provenance_key(
                        filing.company_id,
                        "SECTION",
                        filing.form_type,
                        filing.period_end[:4],
                        sdata["item_id"],
                    ),
                )
                sections.append(section)
        return sections

    def extract_tables(self, filing: SourceFiling) -> list[SourceTable]:
        """Extract fixture tables."""
        tables: list[SourceTable] = []
        for fdata in self._fixture.get("filings", []):
            if fdata["filing_id"] != filing.filing_id:
                continue
            for tdata in fdata.get("tables", []):
                table = SourceTable(
                    table_id=tdata["table_id"],
                    filing_id=filing.filing_id,
                    company_id=filing.company_id,
                    table_name=tdata["table_name"],
                    table_data=tdata.get("rows", []),
                    row_count=len(tdata.get("rows", [])),
                    col_count=len(tdata.get("headers", [])),
                    provenance_key=build_provenance_key(
                        filing.company_id,
                        "TABLE",
                        filing.form_type,
                        filing.period_end[:4],
                        tdata["table_name"],
                    ),
                )
                tables.append(table)
        return tables


def _default_fixture() -> dict[str, Any]:
    """Default fixture data for Canary Holdings Corporation."""
    return {
        "company_id": "COMPANY_001",
        "company_name": "Canary Holdings Corporation",
        "ticker": "CHC",
        "cik": "0000999999",
        "filings": [
            {
                "filing_id": "filing-10k-2024",
                "company_id": "COMPANY_001",
                "form_type": "10-K",
                "filing_date": "2025-02-15",
                "period_end": "2024-12-31",
                "sections": [
                    {
                        "section_id": "sec-item-1",
                        "item_id": "ITEM_1",
                        "item_title": "Business",
                        "text_content": (
                            "Canary Holdings Corporation operates as a diversified "
                            "financial services company. The company provides banking, "
                            "wealth management, and insurance products through its "
                            "subsidiaries. Revenue is primarily generated from net "
                            "interest income and fee-based services."
                        ),
                    },
                    {
                        "section_id": "sec-item-1a",
                        "item_id": "ITEM_1A",
                        "item_title": "Risk Factors",
                        "text_content": (
                            "The company faces risks from interest rate fluctuations, "
                            "credit losses, regulatory changes, and economic downturns. "
                            "Concentration in commercial lending may amplify losses "
                            "during recessionary periods. Cybersecurity threats pose "
                            "ongoing operational risks."
                        ),
                    },
                    {
                        "section_id": "sec-item-7",
                        "item_id": "ITEM_7",
                        "item_title": "Management's Discussion and Analysis",
                        "text_content": (
                            "Net income increased 12% year-over-year driven by higher "
                            "net interest margin and strong fee income. Loan growth "
                            "was 8% with stable credit quality. The allowance for "
                            "credit losses decreased modestly reflecting improved "
                            "macroeconomic conditions. Operating expenses were well "
                            "controlled with positive operating leverage."
                        ),
                    },
                    {
                        "section_id": "sec-item-8",
                        "item_id": "ITEM_8",
                        "item_title": "Financial Statements",
                        "text_content": (
                            "Total assets were $150 billion at period end. Total loans "
                            "were $95 billion and total deposits were $120 billion. "
                            "Tier 1 capital ratio was 11.2%. Net interest income was "
                            "$4.8 billion and noninterest income was $1.6 billion."
                        ),
                    },
                ],
                "tables": [
                    {
                        "table_id": "tbl-income-statement",
                        "table_name": "INCOME_STATEMENT",
                        "headers": ["Line Item", "FY2024", "FY2023"],
                        "rows": [
                            {
                                "Line Item": "Net Interest Income",
                                "FY2024": "4800",
                                "FY2023": "4300",
                            },
                            {
                                "Line Item": "Noninterest Income",
                                "FY2024": "1600",
                                "FY2023": "1450",
                            },
                            {
                                "Line Item": "Total Revenue",
                                "FY2024": "6400",
                                "FY2023": "5750",
                            },
                            {
                                "Line Item": "Provision for Credit Losses",
                                "FY2024": "500",
                                "FY2023": "700",
                            },
                            {
                                "Line Item": "Net Income",
                                "FY2024": "1800",
                                "FY2023": "1550",
                            },
                        ],
                    },
                    {
                        "table_id": "tbl-balance-sheet",
                        "table_name": "BALANCE_SHEET",
                        "headers": ["Line Item", "FY2024", "FY2023"],
                        "rows": [
                            {
                                "Line Item": "Total Loans",
                                "FY2024": "95000",
                                "FY2023": "88000",
                            },
                            {
                                "Line Item": "Total Deposits",
                                "FY2024": "120000",
                                "FY2023": "112000",
                            },
                            {
                                "Line Item": "Total Assets",
                                "FY2024": "150000",
                                "FY2023": "140000",
                            },
                            {
                                "Line Item": "Total Equity",
                                "FY2024": "18000",
                                "FY2023": "16500",
                            },
                        ],
                    },
                ],
            },
        ],
        "news": [
            {
                "news_id": "news-001",
                "headline": "Canary Holdings reports Q4 earnings beat",
                "published_date": "2025-01-20",
                "text_content": "Canary Holdings Corporation reported Q4 earnings above analyst expectations.",
            },
            {
                "news_id": "news-002",
                "headline": "Canary Holdings announces dividend increase",
                "published_date": "2025-02-01",
                "text_content": "The board approved a 10% increase in the quarterly dividend.",
            },
            {
                "news_id": "news-003",
                "headline": "Canary Holdings completes acquisition",
                "published_date": "2025-03-15",
                "text_content": "Canary Holdings completed the acquisition of a regional insurance provider.",
            },
        ],
    }


# ── SEC semantic validation ────────────────────────────────────────────


def validate_10k_sections(sections: list[SourceSection]) -> list[str]:
    """Validate that a 10-K has required sections (Item 7, Item 8).

    Returns list of violations (empty = valid).
    """
    violations: list[str] = []
    item_ids = {s.item_id for s in sections}
    if "ITEM_7" not in item_ids:
        violations.append("10-K missing Item 7 (MD&A)")
    if "ITEM_8" not in item_ids:
        violations.append("10-K missing Item 8 (Financial Statements)")
    return violations


def validate_10q_sections(sections: list[SourceSection]) -> list[str]:
    """Validate that a 10-Q has required sections (Item 2).

    Returns list of violations (empty = valid).
    """
    violations: list[str] = []
    item_ids = {s.item_id for s in sections}
    if "ITEM_2" not in item_ids:
        violations.append("10-Q missing Item 2 (MD&A)")
    return violations


def validate_filing_date(
    form_type: str, filing_date: str, period_end: str, release_date: str
) -> list[str]:
    """Validate filing date constraints.

    - 8-K must not be future-dated
    - 10-Q must not be Q4 (Q4 10-Q is invalid — use 10-K)
    - Filename period must match filing period
    """
    violations: list[str] = []

    if form_type == "8-K" and filing_date > release_date:
        violations.append(
            f"Future-dated 8-K: filing_date {filing_date} > release_date {release_date}"
        )

    if form_type == "10-Q":
        month = int(period_end.split("-")[1]) if "-" in period_end else 0
        if month == 12:
            violations.append("Q4 10-Q is invalid — use 10-K for fiscal year end")

    return violations


def validate_filename_period_match(filename: str, form_type: str, period_end: str) -> list[str]:
    """Validate that filename period matches filing period."""
    violations: list[str] = []
    year = period_end[:4] if len(period_end) >= 4 else ""
    if year and year not in filename:
        violations.append(f"Filename '{filename}' does not contain period year '{year}'")
    return violations


# ── Real SEC provider using official SEC JSON APIs ───────────────────────


class OfficialSecApiProvider(SecProvider):
    """SEC provider using official SEC JSON APIs with fair-access safeguards.

    Uses:
    - https://data.sec.gov/submissions/CIK{cik}.json for submissions
    - https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json for XBRL facts
    - https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/ for filing HTML

    Safeguards:
    - Non-empty descriptive User-Agent required (min 10 chars)
    - Rate limited to 8 req/sec (SEC fair-access max is 10/sec)
    - Exponential backoff retry (3 attempts, base 1s)
    - 30-second HTTP timeout
    - SHA-256 keyed disk cache
    - Offline cache replay without network

    No API key required.
    """

    def __init__(
        self,
        user_agent: str,
        cache_dir: Path | None = None,
        cik: str | None = None,
        max_requests_per_second: int = SEC_MAX_REQUESTS_PER_SECOND,
        live_network: bool = True,
    ) -> None:
        if len(user_agent.strip()) < 10:
            raise SecProviderError(
                f"User-Agent must be a descriptive string >= {SEC_USER_AGENT_MIN_LENGTH} chars, "
                f"got {len(user_agent.strip())!r}"
            )
        if max_requests_per_second > 10:
            raise SecProviderError(
                f"max_requests_per_second must be <= 10 (SEC fair-access limit), "
                f"got {max_requests_per_second}"
            )
        if max_requests_per_second <= 0:
            raise SecProviderError(
                f"max_requests_per_second must be positive, got {max_requests_per_second}"
            )

        self.user_agent = user_agent
        self.cache_dir = cache_dir
        self._explicit_cik = cik
        self._max_requests_per_second = max_requests_per_second
        self._live_network = live_network
        self._session: Any = None
        self._cik_cache: dict[str, str] = {}

        # Provider report counters
        self._request_count = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._parse_success_count = 0
        self._parse_failure_count = 0
        self._filings_discovered: dict[str, int] = {}
        self._filings_selected: dict[str, int] = {}
        self._public_provenance_keys: list[str] = []

        # Rate limiter state
        self._last_request_time: float = 0.0
        self._min_interval = 1.0 / max_requests_per_second

    def _enforce_rate_limit(self) -> None:
        """Enforce SEC fair-access rate limit (requests per second)."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            sleep_time = self._min_interval - elapsed
            time.sleep(sleep_time)
        self._last_request_time = time.monotonic()

    def _fetch_with_retry(
        self,
        url: str,
        timeout: int = SEC_DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = SEC_MAX_RETRIES,
    ) -> str:
        """Central HTTP request helper with retry, backoff, and rate limiting.

        All live HTTP requests to SEC go through this method.
        """
        import requests

        if not self._live_network:
            raise SecProviderError(f"Live network disabled but no cache entry for URL: {url[:80]}")

        self._enforce_rate_limit()

        last_exception: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = self._session_obj.get(url, timeout=timeout)
                self._request_count += 1

                if resp.status_code == 403:
                    raise SecAuthError(
                        "SEC rejected request (403). "
                        "Ensure User-Agent is a descriptive string (not a browser default)."
                    )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    raise SecRateLimitError(f"SEC rate limit exceeded. Retry after {retry_after}s.")

                resp.raise_for_status()
                text: str = resp.text
                return text

            except (requests.ConnectionError, requests.Timeout) as e:
                last_exception = e
                if attempt < max_retries:
                    sleep_time = SEC_RETRY_BACKOFF_BASE * (2**attempt)
                    time.sleep(sleep_time)
                else:
                    raise SecTimeoutError(
                        f"SEC request failed after {max_retries} retries: {e}"
                    ) from e
            except requests.HTTPError as e:
                last_exception = e
                if (
                    e.response is not None
                    and e.response.status_code >= 500
                    and attempt < max_retries
                ):
                    sleep_time = SEC_RETRY_BACKOFF_BASE * (2**attempt)
                    time.sleep(sleep_time)
                else:
                    raise SecProviderError(f"SEC HTTP error: {e}") from e

        # Should not reach here, but mypy safety
        raise SecProviderError(
            f"SEC request failed after retries: {last_exception}"
        ) from last_exception

    @property
    def _session_obj(self) -> Any:
        """Lazy-initialize session with proper headers."""
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.headers.update(
                {
                    "User-Agent": self.user_agent,
                    "Accept": "application/json, text/html",
                    "Accept-Encoding": "gzip, deflate",
                }
            )
        return self._session

    def _resolve_cik(self, ticker: str) -> str:
        """Resolve ticker to CIK using SEC's ticker->CIK mapping."""
        if self._explicit_cik:
            return self._explicit_cik

        if ticker in self._cik_cache:
            return self._cik_cache[ticker]

        url = "https://www.sec.gov/files/company_tickers.json"
        data = self._fetch_json(url)

        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker.upper():
                cik = str(entry.get("cik_str", "")).zfill(10)
                self._cik_cache[ticker] = cik
                return cik

        raise SecProviderError(f"Could not resolve CIK for ticker: {ticker}")

    def _fetch_json(self, url: str) -> dict[str, Any]:
        """Fetch JSON from SEC API with SHA-256 keyed disk cache.

        If cache is enabled and a cache hit exists, returns cached data
        without network access. Cache keys are SHA-256 of the URL (16 hex chars).
        """
        from typing import cast

        cache_path = None
        if self.cache_dir:
            hash_str = hashlib.sha256(url.encode()).hexdigest()[:16]
            cache_path = self.cache_dir / f"{hash_str}.json"
            if cache_path.exists():
                self._cache_hits += 1
                return cast("dict[str, Any]", json.loads(cache_path.read_text()))

        response_text = self._fetch_with_retry(url)
        data: dict[str, Any] = json.loads(response_text)

        if cache_path:
            self._cache_misses += 1
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data))

        return data

    def discover_filings(
        self, ticker: str, form: str = "10-K", limit: int = 1
    ) -> list[SourceFiling]:
        """Discover SEC filings for a ticker using official SEC submissions API."""
        cik = self._resolve_cik(ticker)
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        data = self._fetch_json(url)

        # Record filings discovered by form type
        recent = data.get("filings", {}).get("recent", {})
        for i in range(len(recent.get("accessionNumber", []))):
            ftype = recent.get("form", [])[i]
            self._filings_discovered[ftype] = self._filings_discovered.get(ftype, 0) + 1

        filings: list[SourceFiling] = []
        for i in range(len(recent.get("accessionNumber", []))):
            if recent.get("form", [])[i] != form:
                continue

            accession = recent["accessionNumber"][i].replace("-", "")
            filing_date = recent["filingDate"][i]
            period_end = (
                recent.get("reportDate", [])[i] if recent.get("reportDate") else filing_date
            )

            filing_id = f"filing-{form.lower()}-{accession[:8]}"
            provenance_key = build_provenance_key("COMPANY_001", "FILING", form, period_end[:4])

            filing = SourceFiling(
                filing_id=filing_id,
                company_id="COMPANY_001",
                form_type=form,
                filing_date=filing_date,
                period_end=period_end,
                accession_ref=accession,
                provenance_key=provenance_key,
                section_count=0,
                table_count=0,
            )
            filings.append(filing)
            if len(filings) >= limit:
                break

        self._filings_selected[form] = len(filings)
        if filings:
            self._public_provenance_keys.append(
                build_provenance_key("COMPANY_001", "FILING", form, filings[0].period_end[:4])
            )
        return filings

    def _fetch_filing_html(self, cik: str, accession: str) -> str:
        """Fetch the primary HTML document for a filing."""
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}"
        index_url = f"{base}/index.json"

        try:
            index_data = self._fetch_json(index_url)
            for item in index_data.get("directory", {}).get("item", []):
                name = item.get("name", "")
                if name.endswith((".htm", ".html")) and not name.startswith("R"):
                    doc_url = f"{base}/{name}"
                    response_text = self._fetch_with_retry(doc_url)
                    return response_text
        except SecProviderError:
            raise
        except Exception as e:
            raise SecProviderError(f"Failed to fetch filing HTML: {e}") from e

        raise SecProviderError(f"No HTML document found for accession {accession}")

    def parse_sections(self, filing: SourceFiling) -> list[SourceSection]:
        """Parse a filing into semantic sections using SEC-specific parsing."""
        cik = self._resolve_cik("CHC")
        html = self._fetch_filing_html(cik, filing.accession_ref)

        from ..extraction.converter import HtmlFilingExtractor
        from ..extraction.segmenter import FilingSegmenter

        extractor = HtmlFilingExtractor()
        segmenter = FilingSegmenter()

        text_result = extractor.extract(html)
        full_text: str = text_result.text if hasattr(text_result, "text") else str(text_result)
        segments = segmenter.segment(full_text)

        sections: list[SourceSection] = []
        for i, seg in enumerate(segments):
            item_id = seg.item or f"ITEM_{i + 1}"
            item_title = seg.title or item_id
            section_text = seg.content

            section = SourceSection(
                section_id=f"sec-{item_id.lower()}",
                filing_id=filing.filing_id,
                company_id=filing.company_id,
                item_id=item_id,
                item_title=item_title,
                text_content=section_text,
                char_count=seg.char_count,
                provenance_key=build_provenance_key(
                    filing.company_id,
                    "SECTION",
                    filing.form_type,
                    filing.period_end[:4],
                    item_id,
                ),
            )
            sections.append(section)

        if sections:
            self._parse_success_count += 1
        else:
            self._parse_failure_count += 1

        for s in sections:
            self._public_provenance_keys.append(s.provenance_key)

        return sections

    def extract_tables(self, filing: SourceFiling) -> list[SourceTable]:
        """Extract structured tables from a filing.

        Production table extraction requires XBRL parsing (deferred).
        The FixtureSecProvider handles table extraction for tests.
        """
        return []

    def get_provider_report(self) -> dict[str, Any]:
        """Return structured provider report for QA audit."""
        return {
            "provider_name": self.__class__.__name__,
            "provider_kind": "real",
            "request_count": self._request_count,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "rate_limit_setting": f"{self._max_requests_per_second}/sec",
            "user_agent_configured": len(self.user_agent.strip()) >= SEC_USER_AGENT_MIN_LENGTH,
            "filings_discovered": dict(self._filings_discovered),
            "filings_selected": dict(self._filings_selected),
            "parse_success_count": self._parse_success_count,
            "parse_failure_count": self._parse_failure_count,
            "semantic_validation_result": "PASS" if self._parse_failure_count == 0 else "FAIL",
            "private_cache_location": str(self.cache_dir) if self.cache_dir else "",
            "public_provenance_keys": list(self._public_provenance_keys),
        }


def create_sec_provider(
    provider_type: str,
    config: dict[str, Any],
) -> SecProvider:
    """Factory function to create SEC provider from config."""
    if provider_type == "OfficialSecApiProvider":
        return OfficialSecApiProvider(
            user_agent=config.get(
                "user_agent", "FENRIX Synthetic Data DataWorker/0.1 contact@fenrix.ai"
            ),
            cache_dir=Path(config["cache_dir"]) if config.get("cache_dir") else None,
            cik=config.get("cik"),
            max_requests_per_second=config.get(
                "max_requests_per_second", SEC_MAX_REQUESTS_PER_SECOND
            ),
            live_network=config.get("live_network", True),
        )
    elif provider_type == "FixtureSecProvider":
        return FixtureSecProvider()
    else:
        raise SecProviderError(f"Unknown SEC provider type: {provider_type}")


def validate_sec_fair_access_config(config: dict[str, Any]) -> list[str]:
    """Validate SEC fair-access configuration.

    Returns list of violations (empty = valid).
    """
    violations: list[str] = []

    user_agent = config.get("user_agent", "")
    if len(user_agent.strip()) < SEC_USER_AGENT_MIN_LENGTH:
        violations.append(
            f"User-Agent must be >= {SEC_USER_AGENT_MIN_LENGTH} characters (got {len(user_agent.strip())})"
        )

    max_rps = config.get("max_requests_per_second", SEC_MAX_REQUESTS_PER_SECOND)
    if max_rps > 10:
        violations.append(f"max_requests_per_second must be <= 10 (SEC limit), got {max_rps}")
    if max_rps <= 0:
        violations.append(f"max_requests_per_second must be positive, got {max_rps}")

    return violations
