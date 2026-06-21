"""SEC collector for filings and structured SEC data.

Wraps the existing SECClient with date-range filtering,
caching, and artifact persistence.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..sec.client import SECClient
from ..sec.transport import LiveTransport
from ..storage.atomic import atomic_write_bytes, atomic_write_json
from .base import CollectionStatus, CollectorResult

logger = logging.getLogger(__name__)


class SECCollector:
    """Collect SEC filings and metadata for a ticker."""

    def __init__(
        self,
        output_dir: Path,
        ticker: str,
        years: int = 10,
        user_agent: str | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.ticker = ticker.upper()
        self.years = years
        self.user_agent = user_agent
        self.parser_version = "sec_edgar_v1"

    def collect_all(self) -> list[CollectorResult]:
        """Run SEC collection: submissions, companyfacts, filings."""
        results: list[CollectorResult] = []

        if not self.user_agent:
            results.append(
                CollectorResult(
                    source="sec",
                    artifact_type="all",
                    status=CollectionStatus.FAILED,
                    requested_range=(None, None),
                    observed_range=(None, None),
                    failure_reason="SEC_USER_AGENT not configured in environment",
                )
            )
            return results

        end_date = datetime.now(UTC)
        start_date = datetime(end_date.year - self.years, end_date.month, end_date.day, tzinfo=UTC)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        transport = LiveTransport(self.user_agent)
        client = SECClient(transport)

        # Resolve CIK
        cik = client.resolve_cik(self.ticker)
        if not cik:
            results.append(
                CollectorResult(
                    source="sec",
                    artifact_type="all",
                    status=CollectionStatus.FAILED,
                    requested_range=(start_str, end_str),
                    observed_range=(None, None),
                    failure_reason=f"Could not resolve CIK for ticker {self.ticker}",
                )
            )
            return results

        # Submissions
        submissions = client.get_submissions(cik)
        if submissions:
            sub_path = self.output_dir / "sec" / "submissions.json"
            atomic_write_json(sub_path, submissions)
            results.append(
                CollectorResult(
                    source="sec",
                    artifact_type="submissions",
                    status=CollectionStatus.SUCCESS,
                    requested_range=(start_str, end_str),
                    observed_range=(None, None),
                    row_count=len(
                        submissions.get("filings", {}).get("recent", {}).get("accessionNumber", [])
                    ),
                    fetch_timestamp=datetime.now(UTC).isoformat(),
                    parser_version=self.parser_version,
                    content_type="application/json",
                    relative_path=str(sub_path.relative_to(self.output_dir.parent)),
                    byte_size=sub_path.stat().st_size,
                    sha256=hashlib.sha256(sub_path.read_bytes()).hexdigest(),
                )
            )
        else:
            results.append(
                CollectorResult(
                    source="sec",
                    artifact_type="submissions",
                    status=CollectionStatus.FAILED,
                    requested_range=(start_str, end_str),
                    observed_range=(None, None),
                    failure_reason="submissions request failed",
                )
            )

        # Companyfacts
        try:
            facts = transport.get_json(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                timeout=30,
            )
            if facts:
                facts_path = self.output_dir / "sec" / "companyfacts.json"
                atomic_write_json(facts_path, facts)
                results.append(
                    CollectorResult(
                        source="sec",
                        artifact_type="companyfacts",
                        status=CollectionStatus.SUCCESS,
                        requested_range=(start_str, end_str),
                        observed_range=(None, None),
                        fetch_timestamp=datetime.now(UTC).isoformat(),
                        parser_version=self.parser_version,
                        content_type="application/json",
                        relative_path=str(facts_path.relative_to(self.output_dir.parent)),
                        byte_size=facts_path.stat().st_size,
                        sha256=hashlib.sha256(facts_path.read_bytes()).hexdigest(),
                    )
                )
        except Exception as exc:
            logger.warning("Companyfacts failed for %s: %s", self.ticker, exc)
            results.append(
                CollectorResult(
                    source="sec",
                    artifact_type="companyfacts",
                    status=CollectionStatus.FAILED,
                    requested_range=(start_str, end_str),
                    observed_range=(None, None),
                    failure_reason=str(exc),
                )
            )

        # Filing inventory for forms 10-K, 10-Q, 8-K within date range
        forms = ["10-K", "10-Q", "8-K"]
        filing_inventory: list[dict[str, Any]] = []
        for form in forms:
            try:
                filings = client.get_filings(
                    self.ticker,
                    form=form,
                    date_from=start_str,
                    date_to=end_str,
                    limit=100,
                    include_archival=True,
                )
                for f in filings:
                    f["form"] = form
                    filing_inventory.append(f)
            except Exception as exc:
                logger.warning("Filings %s failed for %s: %s", form, self.ticker, exc)

        # Save filing inventory
        inv_path = self.output_dir / "sec" / "filing_inventory.json"
        atomic_write_json(inv_path, filing_inventory)
        results.append(
            CollectorResult(
                source="sec",
                artifact_type="filing_inventory",
                status=CollectionStatus.SUCCESS
                if filing_inventory
                else CollectionStatus.UNAVAILABLE,
                requested_range=(start_str, end_str),
                observed_range=(None, None),
                row_count=len(filing_inventory),
                fetch_timestamp=datetime.now(UTC).isoformat(),
                parser_version=self.parser_version,
                content_type="application/json",
                relative_path=str(inv_path.relative_to(self.output_dir.parent)),
                byte_size=inv_path.stat().st_size,
                sha256=hashlib.sha256(inv_path.read_bytes()).hexdigest(),
                metadata={"forms": forms, "cik": cik},
            )
        )

        # Download primary documents for each filing
        downloaded_count = 0
        failed_count = 0
        for filing in filing_inventory[:20]:  # Limit to first 20 filings to be respectful
            accession = filing.get("accessionNumber", "")
            primary_doc = filing.get("primaryDocument", "")
            form = filing.get("form", "")
            if not accession or not primary_doc:
                continue
            try:
                url = SECClient.build_filing_url(cik, accession, primary_doc)
                resp = transport.get_bytes(url, timeout=60)
                accession_no_dashes = accession.replace("-", "")
                doc_path = self.output_dir / "sec" / "filings" / f"{accession_no_dashes}.html"
                doc_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(doc_path, resp.content)

                # Normalize text
                from ..extraction.converter import HtmlFilingExtractor

                extractor = HtmlFilingExtractor()
                text_result = extractor.extract(resp.content.decode("utf-8", errors="replace"))
                text_path = self.output_dir / "sec" / "filings" / f"{accession_no_dashes}.md"
                text_path.write_text(text_result["text"], encoding="utf-8")

                downloaded_count += 1
            except Exception as exc:
                logger.warning("Download failed for %s %s: %s", self.ticker, accession, exc)
                failed_count += 1

        results.append(
            CollectorResult(
                source="sec",
                artifact_type="filing_documents",
                status=CollectionStatus.SUCCESS
                if downloaded_count > 0
                else CollectionStatus.UNAVAILABLE,
                requested_range=(start_str, end_str),
                observed_range=(None, None),
                row_count=downloaded_count,
                fetch_timestamp=datetime.now(UTC).isoformat(),
                parser_version=self.parser_version,
                content_type="text/html",
                relative_path=str(self.output_dir / "sec" / "filings"),
                byte_size=0,
                sha256="",
                metadata={
                    "downloaded_count": downloaded_count,
                    "failed_count": failed_count,
                    "cik": cik,
                    "forms": forms,
                },
            )
        )

        return results
