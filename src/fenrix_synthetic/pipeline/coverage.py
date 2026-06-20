"""Coverage reporting for pipeline runs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson


class CoverageReporter:
    """Generate source coverage reports."""

    def __init__(self, ticker: str, output_dir: Path) -> None:
        self.ticker = ticker.upper()
        self.output_dir = output_dir

    def build_report(
        self,
        yfinance_results: list[Any],
        sec_results: list[Any],
        news_results: list[Any],
        news_coverage: Any | None = None,
    ) -> dict[str, Any]:
        """Build aggregate coverage report."""
        yf_summary = self._summarize_yfinance(yfinance_results)
        sec_summary = self._summarize_sec(sec_results)
        news_summary = self._summarize_news(news_results, news_coverage)

        report = {
            "ticker": self.ticker,
            "generated_at": datetime.now(UTC).isoformat(),
            "schema_version": "1.0.0",
            "yfinance": yf_summary,
            "sec": sec_summary,
            "news": news_summary,
            "overall": {
                "sources_attempted": 3,
                "sources_successful": sum(
                    [
                        yf_summary.get("has_data", False),
                        sec_summary.get("has_data", False),
                        news_summary.get("has_data", False),
                    ]
                ),
            },
        }
        return report

    def save_report(self, report: dict[str, Any]) -> Path:
        path = self.output_dir / "source_coverage" / "coverage_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(report, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2))
        return path

    def _summarize_yfinance(self, results: list[Any]) -> dict[str, Any]:
        if not results:
            return {"has_data": False, "artifacts": []}
        artifacts = []
        success_count = 0
        for r in results:
            d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
            artifacts.append(
                {
                    "artifact_type": d.get("artifact_type"),
                    "status": d.get("status"),
                    "row_count": d.get("row_count"),
                }
            )
            if d.get("status") == "success":
                success_count += 1
        return {
            "has_data": success_count > 0,
            "artifacts_collected": len(artifacts),
            "artifacts_successful": success_count,
            "artifacts": artifacts,
        }

    def _summarize_sec(self, results: list[Any]) -> dict[str, Any]:
        if not results:
            return {"has_data": False, "artifacts": []}
        artifacts = []
        success_count = 0
        filing_count = 0
        for r in results:
            d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
            artifacts.append(
                {
                    "artifact_type": d.get("artifact_type"),
                    "status": d.get("status"),
                    "row_count": d.get("row_count"),
                }
            )
            if d.get("status") == "success":
                success_count += 1
            if d.get("artifact_type") == "filing_documents":
                filing_count = d.get("metadata", {}).get("downloaded_count", 0)
        by_form: dict[str, int] = {}
        for r in results:
            d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
            meta = d.get("metadata", {})
            if "forms" in meta:
                for form in meta["forms"]:
                    by_form[form] = by_form.get(form, 0) + 1
        return {
            "has_data": success_count > 0,
            "artifacts_collected": len(artifacts),
            "artifacts_successful": success_count,
            "filing_count": filing_count,
            "filings_by_form": by_form,
            "artifacts": artifacts,
        }

    def _summarize_news(self, results: list[Any], coverage: Any | None) -> dict[str, Any]:
        if not results and coverage is None:
            return {"has_data": False}
        if coverage is not None:
            d = coverage.to_dict() if hasattr(coverage, "to_dict") else dict(coverage)
            return {
                "has_data": d.get("total_records", 0) > 0,
                "total_records": d.get("total_records", 0),
                "unique_urls": d.get("unique_urls", 0),
                "body_fetch_success": d.get("body_fetch_success", 0),
                "body_fetch_failure": d.get("body_fetch_failure", 0),
                "earliest_timestamp": d.get("earliest_timestamp"),
                "latest_timestamp": d.get("latest_timestamp"),
                "historical_10y_complete": d.get("historical_10y_complete", False),
                "coverage_limitations": d.get("coverage_limitations", []),
            }
        return {"has_data": False}
