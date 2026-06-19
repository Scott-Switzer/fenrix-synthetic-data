"""SEC EDGAR client for filing discovery.

Adapted from Zion Terminal sec/client.py (commit e75ae57).

Provides ticker-to-CIK resolution, filing discovery with form and
date filtering, accession handling, and primary-document URL
construction.  Depends on SecTransport rather than calling requests
directly.
"""

from __future__ import annotations

import logging
from typing import Any

from .transport import SecTransport

logger = logging.getLogger(__name__)

_BASE = "https://data.sec.gov"
_SEC_BASE = "https://www.sec.gov"


class SECClient:
    """Client for SEC EDGAR JSON filing discovery.

    Uses an injected ``SecTransport`` so it is independently testable
    with fixture or in-memory transports.
    """

    def __init__(self, transport: SecTransport) -> None:
        self._transport = transport
        self._ticker_map: dict[str, dict[str, Any]] | None = None

    def resolve_cik(self, ticker: str) -> str | None:
        """Resolve a ticker to a 10-digit zero-padded CIK."""
        if self._ticker_map is None:
            self._ticker_map = self._load_ticker_map()
        upper = ticker.upper().replace(".", "-")
        entry = self._ticker_map.get(upper)
        if entry:
            return str(entry["cik_str"]).zfill(10)
        return None

    def _load_ticker_map(self) -> dict[str, dict[str, Any]]:
        try:
            data = self._transport.get_json(
                f"{_SEC_BASE}/files/company_tickers.json",
                timeout=15,
            )
            result: dict[str, dict[str, Any]] = {}
            for entry in data.values():
                ticker_key = str(entry.get("ticker", "")).upper()
                if ticker_key:
                    result[ticker_key] = entry
            logger.info("Loaded %d tickers from SEC", len(result))
            return result
        except Exception as exc:
            logger.warning("Failed to load SEC ticker map: %s", exc)
            return {}

    def get_submissions(self, cik: str) -> dict[str, Any] | None:
        """Fetch company submissions (filing history)."""
        try:
            val: dict[str, Any] | None = self._transport.get_json(
                f"{_BASE}/submissions/CIK{cik}.json",
                timeout=30,
            )
            return val
        except Exception as exc:
            logger.warning("SEC submissions request failed for CIK %s: %s", cik, exc)
            return None

    def _fetch_older_filings(
        self,
        cik: str,
        older_files: list[Any],
    ) -> list[dict[str, Any]]:
        all_filings: list[dict[str, Any]] = []
        for file_ref in older_files:
            filename = file_ref if isinstance(file_ref, str) else file_ref.get("name", "")
            if not filename:
                continue
            try:
                data = self._transport.get_json(
                    f"{_BASE}/submissions/{filename}",
                    timeout=30,
                )
                count = len(data.get("accessionNumber", []))
                for i in range(count):
                    filing = {
                        "accessionNumber": data["accessionNumber"][i],
                        "filingDate": data["filingDate"][i],
                        "reportDate": (
                            data.get("reportDate", [""])[i]
                            if i < len(data.get("reportDate", []))
                            else ""
                        ),
                        "form": data["form"][i],
                        "primaryDocument": (
                            data.get("primaryDocument", [""])[i]
                            if i < len(data.get("primaryDocument", []))
                            else ""
                        ),
                        "primaryDocDescription": (
                            data.get("primaryDocDescription", [""])[i]
                            if i < len(data.get("primaryDocDescription", []))
                            else ""
                        ),
                    }
                    all_filings.append(filing)
            except Exception as exc:
                logger.warning("Failed to fetch older filing file %s: %s", filename, exc)
                continue
        return all_filings

    def get_filings(
        self,
        ticker: str,
        *,
        form: str | None = None,
        year: int | None = None,
        quarter: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
        include_archival: bool = True,
    ) -> list[dict[str, Any]]:
        """Get filing metadata with optional filters.

        ``year`` and ``quarter`` filter on ``reportDate`` (fiscal period
        end date).  ``date_from`` and ``date_to`` filter on ``filingDate``
        (SEC acceptance date).
        """
        cik = self.resolve_cik(ticker)
        if not cik:
            logger.warning("Could not resolve CIK for ticker %s", ticker)
            return []

        submissions = self.get_submissions(cik)
        if not submissions:
            return []

        company_name = submissions.get("name", "")
        recent = submissions.get("filings", {}).get("recent", {})
        if not recent:
            return []

        filings = self._columnar_to_filings(recent, cik, ticker, company_name)
        filtered = self._apply_filing_filters(
            filings,
            form=form,
            year=year,
            quarter=quarter,
            date_from=date_from,
            date_to=date_to,
        )

        if len(filtered) < limit and include_archival:
            older_files = submissions.get("filings", {}).get("files", [])
            if older_files:
                logger.info(
                    "Recent filings insufficient (%d/%d) — walking %d archival files",
                    len(filtered),
                    limit,
                    len(older_files),
                )
                older_raw = self._fetch_older_filings(cik, older_files)
                for f in older_raw:
                    f["cik"] = cik
                    f["ticker"] = ticker.upper()
                    f["companyName"] = company_name
                older_filtered = self._apply_filing_filters(
                    older_raw,
                    form=form,
                    year=year,
                    quarter=quarter,
                    date_from=date_from,
                    date_to=date_to,
                )
                filtered.extend(older_filtered)

        return filtered[:limit]

    @staticmethod
    def _columnar_to_filings(
        columnar: dict[str, Any],
        cik: str,
        ticker: str,
        company_name: str,
    ) -> list[dict[str, Any]]:
        count = len(columnar.get("accessionNumber", []))
        filings: list[dict[str, Any]] = []
        for i in range(count):
            filing = {
                "accessionNumber": columnar["accessionNumber"][i],
                "filingDate": columnar["filingDate"][i],
                "reportDate": (
                    columnar.get("reportDate", [""])[i]
                    if i < len(columnar.get("reportDate", []))
                    else ""
                ),
                "form": columnar["form"][i],
                "primaryDocument": (
                    columnar.get("primaryDocument", [""])[i]
                    if i < len(columnar.get("primaryDocument", []))
                    else ""
                ),
                "primaryDocDescription": (
                    columnar.get("primaryDocDescription", [""])[i]
                    if i < len(columnar.get("primaryDocDescription", []))
                    else ""
                ),
                "cik": cik,
                "ticker": ticker.upper(),
                "companyName": company_name,
            }
            filings.append(filing)
        return filings

    @staticmethod
    def _apply_filing_filters(
        filings: list[dict[str, Any]],
        *,
        form: str | None = None,
        year: int | None = None,
        quarter: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        filtered = filings
        if form:
            filtered = [f for f in filtered if f["form"] == form]
        if year:
            filtered = [f for f in filtered if _filing_year(f) == year]
        if quarter:
            filtered = [f for f in filtered if _filing_in_quarter(f, quarter)]
        if date_from:
            filtered = [f for f in filtered if f["filingDate"] >= date_from]
        if date_to:
            filtered = [f for f in filtered if f["filingDate"] <= date_to]
        return filtered

    @staticmethod
    def build_filing_url(cik: str, accession_number: str, primary_document: str) -> str:
        """Construct the SEC filing document URL."""
        accession_no_dashes = accession_number.replace("-", "")
        return (
            f"{_SEC_BASE}/Archives/edgar/data/"
            f"{cik.lstrip('0') or '0'}/{accession_no_dashes}/{primary_document}"
        )

    @staticmethod
    def format_accession(accession_number: str) -> str:
        """Normalize accession number: strip dashes."""
        return accession_number.replace("-", "")

    @staticmethod
    def normalize_cik(value: str) -> str:
        """Normalize CIK to 10-digit zero-padded string."""
        return value.strip().zfill(10)


def _filing_year(filing: dict[str, Any]) -> int | None:
    """Extract fiscal year from reportDate, falling back to filingDate."""
    report_date = filing.get("reportDate", "")
    date_str = report_date if report_date else filing.get("filingDate", "")
    try:
        return int(date_str[:4])
    except (ValueError, IndexError):
        return None


def _filing_in_quarter(filing: dict[str, Any], quarter: int) -> bool:
    """Check if filing falls in a given calendar quarter."""
    report_date = filing.get("reportDate", "")
    date_str = report_date if report_date else filing.get("filingDate", "")
    try:
        month = int(date_str[5:7])
        q = (month - 1) // 3 + 1
        return q == quarter
    except (ValueError, IndexError):
        return False
