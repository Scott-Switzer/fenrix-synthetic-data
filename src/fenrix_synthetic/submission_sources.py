"""Live source collectors for sanitized submission artifacts."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .submission_fast import (
    CompanyContext,
    SourceFailure,
    load_yfinance,
    now_utc,
    safe_error,
    write_json,
    write_text,
)
from .submission_quality import (
    DEFAULT_TICKERS,
    TARGET_TICKER_RE,
    URL_RE,
    bin_value,
    brief_topic,
    scrub_text,
)


def collect_metrics(
    ticker: str, ctx: CompanyContext, root: Path, years: int
) -> tuple[str, dict[str, int], list[SourceFailure], str]:
    public_dir = root / "anonymized" / ctx.company_id / "metrics"
    public_dir.mkdir(parents=True, exist_ok=True)
    failures: list[SourceFailure] = []
    try:
        yf_ticker = load_yfinance().Ticker(ticker)
        history = yf_ticker.history(period=f"{years}y", auto_adjust=False, actions=False)
        close: Any = history["Close"].dropna() if "Close" in history else None
        volume: Any = history["Volume"].dropna() if "Volume" in history else None
        close_base = float(close.iloc[0]) if close is not None and len(close) else 0.0
        volume_base = float(volume.iloc[0]) if volume is not None and len(volume) else 0.0
        with (public_dir / "daily_price_index.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["relative_day", "close_index", "volume_index"])
            writer.writeheader()
            for idx, (_, row) in enumerate(history.iterrows()):
                writer.writerow(
                    {
                        "relative_day": f"DAY_{idx:04d}",
                        "close_index": round(float(row.get("Close", 0)) / close_base * 100, 4)
                        if close_base
                        else "",
                        "volume_index": round(float(row.get("Volume", 0)) / volume_base * 100, 4)
                        if volume_base
                        else "",
                    }
                )
        write_return_features(public_dir / "return_features.csv", history.get("Close", []))
        write_binned_fundamentals(public_dir / "fundamentals_binned.csv", yf_ticker, ctx)
        write_json(
            public_dir / "metrics_manifest.json",
            {
                "schema_version": "1.0",
                **ctx.public_ids(),
                "raw_fundamentals_exported": False,
                "generated_at": now_utc(),
            },
        )
        return "OK", {"metrics_files": 4}, failures, "metrics binned"
    except Exception as exc:  # noqa: BLE001
        failures.append(SourceFailure(ticker, "metrics", "FAIL", safe_error(exc)))
        for name in ("daily_price_index.csv", "return_features.csv", "fundamentals_binned.csv"):
            write_text(public_dir / name, "UNAVAILABLE\n")
        write_json(
            public_dir / "metrics_manifest.json",
            {
                "schema_version": "1.0",
                **ctx.public_ids(),
                "status": "INCOMPLETE",
                "reason": "metrics source unavailable",
            },
        )
        return "FAIL", {"metrics_files": 4}, failures, "metrics unavailable"


def write_return_features(path: Path, close_values: Any) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["relative_day", "return_direction", "return_magnitude_bin"]
        )
        writer.writeheader()
        previous: float | None = None
        for idx, close in enumerate(close_values):
            if previous is None or previous == 0:
                change = 0.0
            else:
                change = (float(close) - previous) / previous
            previous = float(close)
            writer.writerow(
                {
                    "relative_day": f"DAY_{idx:04d}",
                    "return_direction": "up" if change > 0 else "down" if change < 0 else "flat",
                    "return_magnitude_bin": "large"
                    if abs(change) >= 0.03
                    else "medium"
                    if abs(change) >= 0.01
                    else "small",
                }
            )


def write_binned_fundamentals(path: Path, yf_ticker: Any, ctx: CompanyContext) -> None:
    rows = []
    for source_name in ("income_stmt", "balance_sheet", "cashflow"):
        df = getattr(yf_ticker, source_name, None)
        if df is not None and not getattr(df, "empty", True):
            for line_item, row in list(df.iterrows())[:12]:
                rows.append(
                    {
                        "statement": source_name,
                        "line_item": scrub_text(str(line_item), ctx.private_map),
                        "latest_value_bin": bin_value(row.iloc[0]),
                    }
                )
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["statement", "line_item", "latest_value_bin"])
        writer.writeheader()
        writer.writerows(rows)


def collect_news(
    ticker: str, ctx: CompanyContext, root: Path, news_limit: int
) -> tuple[str, dict[str, int], list[SourceFailure], str]:
    public_dir = root / "anonymized" / ctx.company_id / "news"
    public_dir.mkdir(parents=True, exist_ok=True)
    failures: list[SourceFailure] = []
    briefs: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    try:
        items = load_yfinance().Ticker(ticker).news or []
        for item in [entry for entry in items if isinstance(entry, Mapping)][:news_limit]:
            content = item.get("content") if isinstance(item.get("content"), Mapping) else item
            if not isinstance(content, Mapping):
                content = item
            title = str(content.get("title", ""))
            summary = str(content.get("summary", ""))
            raw_for_topic = f"{title}. {summary}".strip()
            peer = next(
                (
                    target
                    for target in DEFAULT_TICKERS
                    if target != ticker and re.search(rf"\b{re.escape(target)}\b", raw_for_topic)
                ),
                "",
            )
            if peer:
                excluded.append(
                    {
                        "brief_id": f"NEWS_EXCLUDED_{len(excluded) + 1:03d}",
                        "reason": "named peer company",
                    }
                )
                continue
            raw_summary = (
                summary or f"A financial news item discussed {brief_topic(raw_for_topic)}."
            )
            clean = re.sub(
                r"\b(?:COMPANY|TICKER)_\d{3}\b",
                "the company",
                scrub_text(raw_summary, ctx.private_map),
            )
            clean = TARGET_TICKER_RE.sub("the company", clean)
            if clean and not URL_RE.search(clean):
                topic = brief_topic(raw_for_topic)
                briefs.append(
                    {
                        "brief_id": f"NEWS_{len(briefs) + 1:03d}",
                        "relative_date": f"RECENT_{len(briefs) + 1:03d}",
                        "source_type": "financial_news",
                        "topic": topic,
                        "sanitized_summary": (
                            "A recent financial news item discussed "
                            f"{topic.replace('_', ' ')} for the company. "
                            "Article titles, publishers, URLs, tickers, and named companies were removed."
                        ),
                        "identity_risk_removed": [
                            "headline",
                            "url",
                            "publisher",
                            "ticker",
                            "company_names",
                        ],
                    }
                )
        write_json(public_dir / "news_briefs.json", {"items": briefs, **ctx.public_ids()})
        write_json(
            public_dir / "news_manifest.json",
            {
                "schema_version": "1.0",
                **ctx.public_ids(),
                "excluded": excluded,
                "raw_headlines_exported": False,
                "raw_urls_exported": False,
                "generated_at": now_utc(),
            },
        )
        if not briefs:
            failures.append(
                SourceFailure(ticker, "news", "INCOMPLETE", "no usable sanitized briefs")
            )
        return (
            ("OK" if briefs else "INCOMPLETE"),
            {"news_files": 2},
            failures,
            json.dumps(briefs[:1]),
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(SourceFailure(ticker, "news", "FAIL", safe_error(exc)))
        write_json(public_dir / "news_briefs.json", {"items": [], **ctx.public_ids()})
        write_json(
            public_dir / "news_manifest.json",
            {"status": "INCOMPLETE", "reason": "news source unavailable", **ctx.public_ids()},
        )
        return "FAIL", {"news_files": 2}, failures, ""
