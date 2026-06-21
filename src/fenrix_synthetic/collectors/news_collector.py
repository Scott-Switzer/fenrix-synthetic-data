"""News collector with provider interface.

For this first vertical slice:
1. yfinance.Ticker.news
2. yfinance.Search(company query).news
3. Best-effort page-body extraction when technically accessible
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .base import CollectionStatus, CollectorResult

logger = logging.getLogger(__name__)


class NewsProvider(Protocol):
    """Protocol for news sources."""

    def fetch_news(self, ticker: str, company_name: str | None = None) -> list[dict[str, Any]]: ...


@dataclass
class NewsCoverageReport:
    """Coverage report for collected news."""

    ticker: str
    earliest_timestamp: str | None = None
    latest_timestamp: str | None = None
    total_records: int = 0
    unique_urls: int = 0
    duplicate_count: int = 0
    body_fetch_success: int = 0
    body_fetch_failure: int = 0
    historical_10y_complete: bool = False
    coverage_limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "earliest_timestamp": self.earliest_timestamp,
            "latest_timestamp": self.latest_timestamp,
            "total_records": self.total_records,
            "unique_urls": self.unique_urls,
            "duplicate_count": self.duplicate_count,
            "body_fetch_success": self.body_fetch_success,
            "body_fetch_failure": self.body_fetch_failure,
            "historical_10y_complete": self.historical_10y_complete,
            "coverage_limitations": self.coverage_limitations,
        }


class YFinanceNewsProvider:
    """Fetch news from yfinance Ticker and Search."""

    def fetch_news(self, ticker: str, company_name: str | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed; skipping news collection")
            return records

        # Ticker.news
        try:
            ticker_obj = yf.Ticker(ticker)
            news = ticker_obj.news or []
            for item in news:
                records.append(self._normalize_item(item, ticker, source="ticker_news"))
        except Exception as exc:
            logger.warning("Ticker.news failed for %s: %s", ticker, exc)

        # Search.news
        try:
            query = company_name or ticker
            search = yf.Search(query)
            search_news = getattr(search, "news", None) or []
            for item in search_news:
                records.append(self._normalize_item(item, ticker, source="search_news"))
        except Exception as exc:
            logger.warning("Search.news failed for %s: %s", ticker, exc)

        return records

    def _normalize_item(self, item: dict[str, Any], ticker: str, source: str) -> dict[str, Any]:
        content = item.get("content", item)
        ts = content.get("published", content.get("pubDate", ""))
        return {
            "headline": content.get("title", content.get("headline", "")),
            "publisher": content.get("publisher", {}).get("name", "")
            if isinstance(content.get("publisher"), dict)
            else content.get("publisher", ""),
            "published_timestamp": ts,
            "summary": content.get("summary", content.get("snippet", "")),
            "canonical_url": content.get("canonicalUrl", {}).get("url", "")
            if isinstance(content.get("canonicalUrl"), dict)
            else content.get("url", ""),
            "related_tickers": content.get("relatedTickers", []),
            "source": source,
            "body": "",
            "body_fetched": False,
            "fetch_status": "pending",
        }


class NewsCollector:
    """Collect news articles for a ticker."""

    def __init__(
        self,
        output_dir: Path,
        ticker: str,
        company_name: str | None = None,
        providers: list[NewsProvider] | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.ticker = ticker.upper()
        self.company_name = company_name
        self.providers = providers or [YFinanceNewsProvider()]
        self.parser_version = "news_v1"

    def collect_all(self) -> tuple[list[CollectorResult], NewsCoverageReport]:
        """Run all news providers and deduplicate."""
        all_records: list[dict[str, Any]] = []
        for provider in self.providers:
            try:
                records = provider.fetch_news(self.ticker, self.company_name)
                all_records.extend(records)
            except Exception as exc:
                logger.warning("News provider failed for %s: %s", self.ticker, exc)

        # Deduplicate by canonical_url
        seen_urls: set[str] = set()
        deduped: list[dict[str, Any]] = []
        duplicates = 0
        for r in all_records:
            url = r.get("canonical_url", "")
            if url in seen_urls:
                duplicates += 1
                continue
            if url:
                seen_urls.add(url)
            deduped.append(r)

        # Best-effort body extraction
        body_success = 0
        body_failure = 0
        for r in deduped:
            url = r.get("canonical_url", "")
            if not url:
                continue
            try:
                body = self._fetch_body(url)
                if body:
                    r["body"] = body[:5000]  # Limit length
                    r["body_fetched"] = True
                    r["fetch_status"] = "success"
                    body_success += 1
                else:
                    r["fetch_status"] = "empty"
                    body_failure += 1
            except Exception as exc:
                logger.debug("Body fetch failed for %s: %s", url, exc)
                r["fetch_status"] = "failed"
                body_failure += 1

        # Save records
        import orjson

        records_path = self.output_dir / "news" / "articles.json"
        records_path.parent.mkdir(parents=True, exist_ok=True)
        records_path.write_bytes(
            orjson.dumps(deduped, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # Compute timestamps
        timestamps: list[str] = []
        for r in deduped:
            ts = r.get("published_timestamp", "")
            if ts:
                timestamps.append(str(ts))

        earliest = min(timestamps) if timestamps else None
        latest = max(timestamps) if timestamps else None

        report = NewsCoverageReport(
            ticker=self.ticker,
            earliest_timestamp=earliest,
            latest_timestamp=latest,
            total_records=len(deduped),
            unique_urls=len(seen_urls),
            duplicate_count=duplicates,
            body_fetch_success=body_success,
            body_fetch_failure=body_failure,
            historical_10y_complete=False,
            coverage_limitations=[
                "News coverage is limited to recent articles available via yfinance.",
                "Historical 10-year news is not available through this source.",
                "Body extraction is best-effort and may fail for paywalled or dynamically rendered pages.",
            ],
        )

        # Save coverage report
        coverage_path = self.output_dir / "news" / "news_coverage.json"
        coverage_path.write_bytes(
            orjson.dumps(report.to_dict(), option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        sha256 = hashlib.sha256(records_path.read_bytes()).hexdigest()

        result = CollectorResult(
            source="yfinance_news",
            artifact_type="news_articles",
            status=CollectionStatus.SUCCESS if deduped else CollectionStatus.UNAVAILABLE,
            requested_range=(None, None),
            observed_range=(earliest, latest),
            row_count=len(deduped),
            fetch_timestamp=datetime.now(UTC).isoformat(),
            parser_version=self.parser_version,
            content_type="application/json",
            relative_path=str(records_path.relative_to(self.output_dir.parent)),
            byte_size=records_path.stat().st_size,
            sha256=sha256,
            metadata={
                "unique_urls": len(seen_urls),
                "duplicates_removed": duplicates,
                "body_fetch_success": body_success,
                "body_fetch_failure": body_failure,
            },
        )

        return [result], report

    def _fetch_body(self, url: str) -> str | None:
        """Best-effort body extraction."""
        try:
            import requests
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; FenrixBot/1.0; research@example.invalid)"
            }
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.content, "html.parser")
            # Remove script/style
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            # Try article tag first
            article = soup.find("article")
            if article:
                return article.get_text(separator="\n", strip=True)
            # Fallback to main or body
            main = soup.find("main") or soup.find("body")
            if main:
                return main.get_text(separator="\n", strip=True)
            return soup.get_text(separator="\n", strip=True)
        except Exception:
            return None
