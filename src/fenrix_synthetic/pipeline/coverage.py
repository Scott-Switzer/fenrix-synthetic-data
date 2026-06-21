"""Coverage reporting for pipeline runs.

Repaired: companyfacts zero-row is flagged, historical_10y_complete is
never reported as true when only recent articles exist, SEC archive
non-null is validated at gate level.
"""

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
        companyfacts_zero = False
        for r in results:
            d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
            artifact_type = d.get("artifact_type", "")
            status = d.get("status", "")
            row_count = d.get("row_count", 0)
            artifacts.append(
                {
                    "artifact_type": artifact_type,
                    "status": status,
                    "row_count": row_count,
                }
            )
            if status == "success":
                success_count += 1
            if artifact_type == "filing_documents":
                filing_count = d.get("metadata", {}).get("downloaded_count", 0)
            # Flag zero-row companyfacts
            if artifact_type == "companyfacts" and row_count == 0:
                companyfacts_zero = True

        by_form: dict[str, int] = {}
        for r in results:
            d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
            meta = d.get("metadata", {})
            if "forms" in meta:
                for form in meta["forms"]:
                    by_form[form] = by_form.get(form, 0) + 1

        summary: dict[str, Any] = {
            "has_data": success_count > 0,
            "artifacts_collected": len(artifacts),
            "artifacts_successful": success_count,
            "filing_count": filing_count,
            "filings_by_form": by_form,
            "artifacts": artifacts,
        }
        if companyfacts_zero:
            summary["warnings"] = summary.get("warnings", []) + [
                "companyfacts returned 0 rows — financial facts are missing"
            ]
        return summary

    def _summarize_news(self, results: list[Any], coverage: Any | None) -> dict[str, Any]:
        if not results and coverage is None:
            return {"has_data": False}
        if coverage is not None:
            d = coverage.to_dict() if hasattr(coverage, "to_dict") else dict(coverage)
            # NEVER report historical_10y_complete=true when only recent articles exist
            earliest = d.get("earliest_timestamp")
            latest = d.get("latest_timestamp")
            is_10y = d.get("historical_10y_complete", False)
            if earliest and latest:
                try:
                    from datetime import datetime as dt

                    earliest_dt = dt.fromisoformat(earliest.replace("Z", "+00:00"))
                    latest_dt = dt.fromisoformat(latest.replace("Z", "+00:00"))
                    span_years = (latest_dt - earliest_dt).days / 365.25
                    if span_years < 9.0:
                        is_10y = False
                except (ValueError, TypeError):
                    pass

            return {
                "has_data": d.get("total_records", 0) > 0,
                "total_records": d.get("total_records", 0),
                "unique_urls": d.get("unique_urls", 0),
                "body_fetch_success": d.get("body_fetch_success", 0),
                "body_fetch_failure": d.get("body_fetch_failure", 0),
                "earliest_timestamp": earliest,
                "latest_timestamp": latest,
                "historical_10y_complete": is_10y,
                "coverage_limitations": d.get("coverage_limitations", []),
            }
        return {"has_data": False}
