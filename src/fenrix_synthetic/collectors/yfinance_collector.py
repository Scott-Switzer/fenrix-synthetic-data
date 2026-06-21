"""YFinance collector for structured financial data.

Each collector method fails independently. One unavailable Yahoo field
does not destroy the entire company run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import CollectionStatus, CollectorResult

logger = logging.getLogger(__name__)


@dataclass
class YFinanceResult:
    """Aggregated results from all yfinance collection attempts."""

    ticker: str
    results: list[CollectorResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "results": [r.to_dict() for r in self.results],
            "metadata": self.metadata,
        }


class YFinanceCollector:
    """Collect structured financial data from Yahoo Finance via yfinance."""

    def __init__(self, output_dir: Path, ticker: str, years: int = 10) -> None:
        self.output_dir = output_dir
        self.ticker = ticker.upper()
        self.years = years
        self.parser_version = "yfinance"
        self._metadata: dict[str, Any] = {}

    def collect_all(self) -> YFinanceResult:
        """Run all collection methods, failing independently."""
        result = YFinanceResult(ticker=self.ticker)
        end_date = datetime.now(UTC)
        start_date = datetime(end_date.year - self.years, end_date.month, end_date.day, tzinfo=UTC)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed; skipping financial data collection")
            result.results.append(
                CollectorResult(
                    source="yfinance",
                    artifact_type="all",
                    status=CollectionStatus.UNAVAILABLE,
                    requested_range=(start_str, end_str),
                    observed_range=(None, None),
                    failure_reason="yfinance package not installed",
                )
            )
            return result

        ticker_obj = yf.Ticker(self.ticker)

        # Collect company info for metadata
        try:
            info = ticker_obj.info or {}
            self._metadata = {
                "short_name": info.get("shortName"),
                "long_name": info.get("longName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "website": info.get("website"),
                "market_cap": info.get("marketCap"),
                "employees": info.get("fullTimeEmployees"),
                "country": info.get("country"),
                "state": info.get("state"),
                "city": info.get("city"),
                "address1": info.get("address1"),
                "phone": info.get("phone"),
                "fax": info.get("fax"),
                "currency": info.get("currency"),
                "exchange": info.get("exchange"),
                "quote_type": info.get("quoteType"),
            }
            # Remove None values
            self._metadata = {k: v for k, v in self._metadata.items() if v is not None}
        except Exception as exc:
            logger.warning("Failed to fetch company info for %s: %s", self.ticker, exc)

        result.metadata = self._metadata

        # OHLCV
        result.results.append(self._collect_ohlcv(ticker_obj, start_str, end_str))

        # Dividends and splits
        result.results.append(self._collect_dividends(ticker_obj, start_str, end_str))
        result.results.append(self._collect_splits(ticker_obj, start_str, end_str))

        # Corporate actions
        result.results.append(self._collect_actions(ticker_obj, start_str, end_str))

        # Financial statements
        result.results.append(self._collect_income_statement(ticker_obj, start_str, end_str))
        result.results.append(
            self._collect_income_statement_quarterly(ticker_obj, start_str, end_str)
        )
        result.results.append(self._collect_balance_sheet(ticker_obj, start_str, end_str))
        result.results.append(self._collect_balance_sheet_quarterly(ticker_obj, start_str, end_str))
        result.results.append(self._collect_cash_flow(ticker_obj, start_str, end_str))
        result.results.append(self._collect_cash_flow_quarterly(ticker_obj, start_str, end_str))

        # Earnings and recommendations
        result.results.append(self._collect_earnings_dates(ticker_obj, start_str, end_str))
        result.results.append(self._collect_recommendations(ticker_obj, start_str, end_str))

        # Price targets and estimates
        result.results.append(self._collect_price_targets(ticker_obj, start_str, end_str))
        result.results.append(self._collect_earnings_estimates(ticker_obj, start_str, end_str))
        result.results.append(self._collect_revenue_estimates(ticker_obj, start_str, end_str))
        result.results.append(self._collect_growth_estimates(ticker_obj, start_str, end_str))

        # Save metadata
        self._save_metadata(result)

        return result

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _save_metadata(self, result: YFinanceResult) -> None:
        import orjson

        meta_path = self.output_dir / "metadata.json"
        meta = {
            "ticker": self.ticker,
            "collection_timestamp": self._now(),
            "years_requested": self.years,
            "company_info": self._metadata,
            "collection_summary": [r.to_dict() for r in result.results],
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_bytes(orjson.dumps(meta, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2))

    def _save_parquet(self, df: Any, path: Path) -> tuple[int, int, int, str]:
        """Save DataFrame as Parquet, return (rows, cols, missing, sha256)."""
        import hashlib

        import pandas as pd

        if df is None or (hasattr(df, "empty") and df.empty):
            return 0, 0, 0, ""

        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(df)

        rows, cols = len(df), len(df.columns)
        missing = int(df.isna().sum().sum())

        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=True)

        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        return rows, cols, missing, sha256

    def _save_csv(self, df: Any, path: Path) -> tuple[int, int, int, str]:
        """Save DataFrame as CSV, return (rows, cols, missing, sha256)."""
        import hashlib

        import pandas as pd

        if df is None or (hasattr(df, "empty") and df.empty):
            return 0, 0, 0, ""

        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(df)

        rows, cols = len(df), len(df.columns)
        missing = int(df.isna().sum().sum())

        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=True)

        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        return rows, cols, missing, sha256

    def _collect_ohlcv(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            hist = ticker_obj.history(start=start, end=end, auto_adjust=True)
            path = self.output_dir / "metrics" / "ohlcv.parquet"
            rows, cols, missing, sha256 = self._save_parquet(hist, path)
            observed_min = hist.index.min().strftime("%Y-%m-%d") if len(hist) > 0 else None
            observed_max = hist.index.max().strftime("%Y-%m-%d") if len(hist) > 0 else None
            return CollectorResult(
                source="yfinance",
                artifact_type="ohlcv",
                status=CollectionStatus.SUCCESS if rows > 0 else CollectionStatus.UNAVAILABLE,
                requested_range=(start, end),
                observed_range=(observed_min, observed_max),
                row_count=rows,
                column_count=cols,
                missing_count=missing,
                fetch_timestamp=self._now(),
                parser_version=self.parser_version,
                content_type="application/vnd.apache.parquet",
                relative_path=str(path.relative_to(self.output_dir.parent)),
                byte_size=path.stat().st_size if path.exists() else 0,
                sha256=sha256,
            )
        except Exception as exc:
            logger.warning("OHLCV failed for %s: %s", self.ticker, exc)
            return CollectorResult(
                source="yfinance",
                artifact_type="ohlcv",
                status=CollectionStatus.FAILED,
                requested_range=(start, end),
                observed_range=(None, None),
                failure_reason=str(exc),
                fetch_timestamp=self._now(),
            )

    def _collect_dividends(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            divs = ticker_obj.dividends
            if divs is None or (hasattr(divs, "empty") and divs.empty):
                return CollectorResult(
                    source="yfinance",
                    artifact_type="dividends",
                    status=CollectionStatus.UNAVAILABLE,
                    requested_range=(start, end),
                    observed_range=(None, None),
                    failure_reason="no dividend data returned",
                    fetch_timestamp=self._now(),
                )
            path = self.output_dir / "metrics" / "dividends.parquet"
            rows, cols, missing, sha256 = self._save_parquet(divs, path)
            return self._make_result("dividends", path, rows, cols, missing, sha256, start, end)
        except Exception as exc:
            return self._make_error("dividends", start, end, exc)

    def _collect_splits(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            splits = ticker_obj.splits
            if splits is None or (hasattr(splits, "empty") and splits.empty):
                return CollectorResult(
                    source="yfinance",
                    artifact_type="splits",
                    status=CollectionStatus.UNAVAILABLE,
                    requested_range=(start, end),
                    observed_range=(None, None),
                    failure_reason="no split data returned",
                    fetch_timestamp=self._now(),
                )
            path = self.output_dir / "metrics" / "splits.parquet"
            rows, cols, missing, sha256 = self._save_parquet(splits, path)
            return self._make_result("splits", path, rows, cols, missing, sha256, start, end)
        except Exception as exc:
            return self._make_error("splits", start, end, exc)

    def _collect_actions(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            actions = ticker_obj.actions
            if actions is None or (hasattr(actions, "empty") and actions.empty):
                return CollectorResult(
                    source="yfinance",
                    artifact_type="actions",
                    status=CollectionStatus.UNAVAILABLE,
                    requested_range=(start, end),
                    observed_range=(None, None),
                    failure_reason="no actions data returned",
                    fetch_timestamp=self._now(),
                )
            path = self.output_dir / "metrics" / "actions.parquet"
            rows, cols, missing, sha256 = self._save_parquet(actions, path)
            return self._make_result("actions", path, rows, cols, missing, sha256, start, end)
        except Exception as exc:
            return self._make_error("actions", start, end, exc)

    def _collect_income_statement(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            df = ticker_obj.income_stmt
            if df is None or (hasattr(df, "empty") and df.empty):
                return self._make_unavailable("income_statement_annual", start, end)
            path = self.output_dir / "metrics" / "income_statement_annual.parquet"
            rows, cols, missing, sha256 = self._save_parquet(df, path)
            return self._make_result(
                "income_statement_annual", path, rows, cols, missing, sha256, start, end
            )
        except Exception as exc:
            return self._make_error("income_statement_annual", start, end, exc)

    def _collect_income_statement_quarterly(
        self, ticker_obj: Any, start: str, end: str
    ) -> CollectorResult:
        try:
            df = ticker_obj.quarterly_income_stmt
            if df is None or (hasattr(df, "empty") and df.empty):
                return self._make_unavailable("income_statement_quarterly", start, end)
            path = self.output_dir / "metrics" / "income_statement_quarterly.parquet"
            rows, cols, missing, sha256 = self._save_parquet(df, path)
            return self._make_result(
                "income_statement_quarterly", path, rows, cols, missing, sha256, start, end
            )
        except Exception as exc:
            return self._make_error("income_statement_quarterly", start, end, exc)

    def _collect_balance_sheet(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            df = ticker_obj.balance_sheet
            if df is None or (hasattr(df, "empty") and df.empty):
                return self._make_unavailable("balance_sheet_annual", start, end)
            path = self.output_dir / "metrics" / "balance_sheet_annual.parquet"
            rows, cols, missing, sha256 = self._save_parquet(df, path)
            return self._make_result(
                "balance_sheet_annual", path, rows, cols, missing, sha256, start, end
            )
        except Exception as exc:
            return self._make_error("balance_sheet_annual", start, end, exc)

    def _collect_balance_sheet_quarterly(
        self, ticker_obj: Any, start: str, end: str
    ) -> CollectorResult:
        try:
            df = ticker_obj.quarterly_balance_sheet
            if df is None or (hasattr(df, "empty") and df.empty):
                return self._make_unavailable("balance_sheet_quarterly", start, end)
            path = self.output_dir / "metrics" / "balance_sheet_quarterly.parquet"
            rows, cols, missing, sha256 = self._save_parquet(df, path)
            return self._make_result(
                "balance_sheet_quarterly", path, rows, cols, missing, sha256, start, end
            )
        except Exception as exc:
            return self._make_error("balance_sheet_quarterly", start, end, exc)

    def _collect_cash_flow(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            df = ticker_obj.cashflow
            if df is None or (hasattr(df, "empty") and df.empty):
                return self._make_unavailable("cash_flow_annual", start, end)
            path = self.output_dir / "metrics" / "cash_flow_annual.parquet"
            rows, cols, missing, sha256 = self._save_parquet(df, path)
            return self._make_result(
                "cash_flow_annual", path, rows, cols, missing, sha256, start, end
            )
        except Exception as exc:
            return self._make_error("cash_flow_annual", start, end, exc)

    def _collect_cash_flow_quarterly(
        self, ticker_obj: Any, start: str, end: str
    ) -> CollectorResult:
        try:
            df = ticker_obj.quarterly_cashflow
            if df is None or (hasattr(df, "empty") and df.empty):
                return self._make_unavailable("cash_flow_quarterly", start, end)
            path = self.output_dir / "metrics" / "cash_flow_quarterly.parquet"
            rows, cols, missing, sha256 = self._save_parquet(df, path)
            return self._make_result(
                "cash_flow_quarterly", path, rows, cols, missing, sha256, start, end
            )
        except Exception as exc:
            return self._make_error("cash_flow_quarterly", start, end, exc)

    def _collect_earnings_dates(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            df = ticker_obj.earnings_dates
            if df is None or (hasattr(df, "empty") and df.empty):
                return self._make_unavailable("earnings_dates", start, end)
            path = self.output_dir / "metrics" / "earnings_dates.parquet"
            rows, cols, missing, sha256 = self._save_parquet(df, path)
            return self._make_result(
                "earnings_dates", path, rows, cols, missing, sha256, start, end
            )
        except Exception as exc:
            return self._make_error("earnings_dates", start, end, exc)

    def _collect_recommendations(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            df = ticker_obj.recommendations
            if df is None or (hasattr(df, "empty") and df.empty):
                return self._make_unavailable("recommendations", start, end)
            path = self.output_dir / "metrics" / "recommendations.parquet"
            rows, cols, missing, sha256 = self._save_parquet(df, path)
            return self._make_result(
                "recommendations", path, rows, cols, missing, sha256, start, end
            )
        except Exception as exc:
            return self._make_error("recommendations", start, end, exc)

    def _collect_price_targets(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            # Try analyst_price_targets dict
            pts = getattr(ticker_obj, "analyst_price_targets", None)
            if pts:
                import pandas as pd

                df = pd.DataFrame([pts])
                path = self.output_dir / "metrics" / "price_targets.csv"
                rows, cols, missing, sha256 = self._save_csv(df, path)
                return self._make_result(
                    "price_targets", path, rows, cols, missing, sha256, start, end
                )
            return self._make_unavailable("price_targets", start, end)
        except Exception as exc:
            return self._make_error("price_targets", start, end, exc)

    def _collect_earnings_estimates(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            est = getattr(ticker_obj, "earnings_estimate", None)
            if est is not None and not (hasattr(est, "empty") and est.empty):
                path = self.output_dir / "metrics" / "earnings_estimates.parquet"
                rows, cols, missing, sha256 = self._save_parquet(est, path)
                return self._make_result(
                    "earnings_estimates", path, rows, cols, missing, sha256, start, end
                )
            return self._make_unavailable("earnings_estimates", start, end)
        except Exception as exc:
            return self._make_error("earnings_estimates", start, end, exc)

    def _collect_revenue_estimates(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            est = getattr(ticker_obj, "revenue_estimate", None)
            if est is not None and not (hasattr(est, "empty") and est.empty):
                path = self.output_dir / "metrics" / "revenue_estimates.parquet"
                rows, cols, missing, sha256 = self._save_parquet(est, path)
                return self._make_result(
                    "revenue_estimates", path, rows, cols, missing, sha256, start, end
                )
            return self._make_unavailable("revenue_estimates", start, end)
        except Exception as exc:
            return self._make_error("revenue_estimates", start, end, exc)

    def _collect_growth_estimates(self, ticker_obj: Any, start: str, end: str) -> CollectorResult:
        try:
            est = getattr(ticker_obj, "growth_estimates", None)
            if est is not None and not (hasattr(est, "empty") and est.empty):
                path = self.output_dir / "metrics" / "growth_estimates.parquet"
                rows, cols, missing, sha256 = self._save_parquet(est, path)
                return self._make_result(
                    "growth_estimates", path, rows, cols, missing, sha256, start, end
                )
            return self._make_unavailable("growth_estimates", start, end)
        except Exception as exc:
            return self._make_error("growth_estimates", start, end, exc)

    def _make_result(
        self,
        artifact_type: str,
        path: Path,
        rows: int,
        cols: int,
        missing: int,
        sha256: str,
        start: str,
        end: str,
    ) -> CollectorResult:
        status = CollectionStatus.SUCCESS if rows > 0 else CollectionStatus.UNAVAILABLE
        observed_min = None
        observed_max = None
        return CollectorResult(
            source="yfinance",
            artifact_type=artifact_type,
            status=status,
            requested_range=(start, end),
            observed_range=(observed_min, observed_max),
            row_count=rows,
            column_count=cols,
            missing_count=missing,
            fetch_timestamp=self._now(),
            parser_version=self.parser_version,
            content_type="application/vnd.apache.parquet"
            if path.suffix == ".parquet"
            else "text/csv",
            relative_path=str(path.relative_to(self.output_dir.parent)),
            byte_size=path.stat().st_size if path.exists() else 0,
            sha256=sha256,
        )

    def _make_error(
        self, artifact_type: str, start: str, end: str, exc: Exception
    ) -> CollectorResult:
        logger.warning("%s failed for %s: %s", artifact_type, self.ticker, exc)
        return CollectorResult(
            source="yfinance",
            artifact_type=artifact_type,
            status=CollectionStatus.FAILED,
            requested_range=(start, end),
            observed_range=(None, None),
            failure_reason=str(exc),
            fetch_timestamp=self._now(),
        )

    def _make_unavailable(self, artifact_type: str, start: str, end: str) -> CollectorResult:
        return CollectorResult(
            source="yfinance",
            artifact_type=artifact_type,
            status=CollectionStatus.UNAVAILABLE,
            requested_range=(start, end),
            observed_range=(None, None),
            failure_reason="no data returned",
            fetch_timestamp=self._now(),
        )
