#!/usr/bin/env python3
"""Build the fast 8-company anonymized submission bundle.

This is intentionally a thin product path:

* collect best-effort yfinance metrics/news and bounded SEC excerpts;
* apply deterministic literal replacement before release packaging;
* run optional bounded NVIDIA QA only when configured;
* create and validate the final ZIP without shipping source/private folders.

It does not call the full reanonymization orchestrator and does not use NVIDIA
for document rewriting.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

DEFAULT_TICKERS = ["CL", "PEP", "TJX", "PM", "AMZN", "HBAN", "BLK", "GOOGL"]
SEC_USER_AGENT = os.environ.get(
    "FENRIX_SEC_USER_AGENT",
    "FENRIX Synthetic Data Worker contact fenrix-research@example.com",
)
SEC_TIMEOUT_SECONDS = 20
SEC_SLEEP_SECONDS = 0.25
MAX_SEC_HTML_BYTES = 8_000_000
MAX_10K_SECTION_CHARS = 60_000
MAX_EXCERPT_CHARS = 20_000
NVIDIA_MAX_SAMPLE_CHARS = 6_000

PUBLIC_TOP_LEVEL_FILES = {
    "README.md",
    "QUICKSTART.md",
    "DATA_DICTIONARY.md",
    "LIMITATIONS.md",
    "RUN_SUMMARY.md",
    "artifact_inventory.csv",
    "run_summary.json",
    "checksums.sha256",
}
PUBLIC_TOP_LEVEL_DIRS = {"anonymized", "qa"}
FORBIDDEN_ZIP_SUBSTRINGS = (
    "originals/",
    "private_maps/",
    "smoke_excerpts/",
    ".env",
    "nvapi-",
    "NVIDIA_API_KEY",
    "/Users/",
    "/content/",
)
SECRET_VALUE_RE = re.compile(r"\bnvapi-[A-Za-z0-9_-]{8,}\b")
URL_RE = re.compile(r"https?://[^\s<>'\")\]]+", re.IGNORECASE)
WWW_RE = re.compile(r"\bwww\.[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s<>'\")\]]*)?", re.IGNORECASE)
SEC_FILE_NUMBER_RE = re.compile(r"\b(?:000|001|002|003|005|033|333|811)-\d{4,8}\b")
ACCESSION_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b|\b\d{18}\b")
LOCAL_PATH_RE = re.compile(r"(/Users/|/content/)[^\s\"']*")


COMPANY_DATA: dict[str, dict[str, Any]] = {
    "CL": {
        "cik": "0000021665",
        "legal_name": "Colgate-Palmolive Company",
        "aliases": [
            "Colgate-Palmolive Company",
            "Colgate-Palmolive",
            "Colgate Palmolive",
            "Colgate",
            "Palmolive",
        ],
        "domains": ["colgatepalmolive.com", "colgate.com"],
        "products": [
            "Speed Stick",
            "Softsoap",
            "Irish Spring",
            "Tom's of Maine",
            "Hill's Science Diet",
            "Meridol",
            "EltaMD",
        ],
        "executives": ["Noel Wallace", "John Cummings"],
    },
    "PEP": {
        "cik": "0000077476",
        "legal_name": "PepsiCo, Inc.",
        "aliases": ["PepsiCo, Inc.", "PepsiCo", "Pepsi", "Pepsi-Cola", "Frito-Lay", "Quaker Oats"],
        "domains": ["pepsico.com", "pepsi.com"],
        "products": [
            "Mountain Dew",
            "Lay's",
            "Gatorade",
            "Tropicana",
            "Doritos",
            "Cheetos",
            "Quaker",
        ],
        "executives": ["Ramon Laguarta", "Hugh Johnston"],
    },
    "TJX": {
        "cik": "0000109198",
        "legal_name": "The TJX Companies, Inc.",
        "aliases": ["The TJX Companies, Inc.", "TJX Companies", "TJX", "T.J. Maxx"],
        "domains": ["tjx.com", "tjmaxx.com", "marshalls.com"],
        "products": ["T.J. Maxx", "Marshalls", "HomeGoods", "Sierra", "Homesense", "Winners"],
        "executives": ["Ernie Herrman"],
    },
    "PM": {
        "cik": "0001413329",
        "legal_name": "Philip Morris International Inc.",
        "aliases": [
            "Philip Morris International Inc.",
            "Philip Morris International",
            "Philip Morris",
            "PMI",
        ],
        "domains": ["pmi.com"],
        "products": ["Marlboro", "IQOS", "HEETS", "ZYN", "Parliament", "L&M"],
        "executives": ["Jacek Olczak", "Emmanuel Babeau"],
    },
    "AMZN": {
        "cik": "0001018724",
        "legal_name": "Amazon.com, Inc.",
        "aliases": ["Amazon.com, Inc.", "Amazon.com", "Amazon", "Amazon Web Services", "AWS"],
        "domains": ["amazon.com", "aws.amazon.com", "aboutamazon.com"],
        "products": ["Alexa", "Kindle", "Fire TV", "Echo", "Audible", "Prime", "Ring"],
        "executives": ["Jeff Bezos", "Andy Jassy", "Brian Olsavsky"],
    },
    "HBAN": {
        "cik": "0000049196",
        "legal_name": "Huntington Bancshares Incorporated",
        "aliases": [
            "Huntington Bancshares Incorporated",
            "Huntington Bancshares",
            "Huntington National Bank",
            "The Huntington National Bank",
            "Huntington Bank",
            "Huntington",
        ],
        "domains": ["huntington.com"],
        "products": ["Huntington"],
        "executives": ["Stephen Steinour"],
    },
    "BLK": {
        "cik": "0001364742",
        "legal_name": "BlackRock, Inc.",
        "aliases": ["BlackRock, Inc.", "BlackRock"],
        "domains": ["blackrock.com", "ishares.com"],
        "products": ["iShares", "Aladdin"],
        "executives": ["Larry Fink", "Robert Goldstein"],
    },
    "GOOGL": {
        "cik": "0001652044",
        "legal_name": "Alphabet Inc.",
        "aliases": ["Alphabet Inc.", "Alphabet", "Google LLC", "Google"],
        "domains": ["abc.xyz", "google.com", "gmail.com", "youtube.com", "deepmind.google"],
        "products": ["YouTube", "Android", "Pixel", "Waymo", "DeepMind", "Chrome", "Bard"],
        "executives": ["Sundar Pichai", "Ruth Porat"],
    },
}


@dataclasses.dataclass
class SourceFailure:
    ticker: str
    source: str
    status: str
    detail: str


@dataclasses.dataclass
class TickerResult:
    ticker: str
    company_id: str
    ticker_id: str
    cik_id: str
    cik_resolved: bool
    metrics_status: str
    sec_status: str
    news_status: str
    residual_status: str
    nvidia_status: str
    artifacts: dict[str, int]
    source_failures: list[SourceFailure]


@dataclasses.dataclass
class ZipValidationResult:
    ok: bool
    entry_count: int
    byte_size: int
    forbidden_name_hits: list[str]
    forbidden_text_hits: list[str]
    api_key_hits: list[str]
    local_path_hits: list[str]


class CompanyContext:
    """Per-company deterministic replacement map."""

    def __init__(self, ticker: str, ticker_index: int, cik: str | None = None) -> None:
        self.ticker = ticker.upper()
        self.index = ticker_index
        self.company_id = f"COMPANY_{ticker_index:03d}"
        self.ticker_id = f"TICKER_{ticker_index:03d}"
        self.cik_id = f"CIK_{ticker_index:03d}"
        self.cik = cik
        self._category_counts: dict[str, int] = {
            "COMPANY": ticker_index,
            "TICKER": ticker_index,
            "CIK": ticker_index,
        }
        self.private_map: dict[str, dict[str, str]] = {}

    def assign(self, original: str, category: str) -> str:
        clean = original.strip()
        if not clean:
            return ""
        category = category.upper()
        bucket = self.private_map.setdefault(category, {})
        normalized = clean.casefold()
        for existing, pseudo in bucket.items():
            if existing.casefold() == normalized:
                return pseudo
        if category == "COMPANY":
            pseudo = self.company_id
        elif category == "TICKER":
            pseudo = self.ticker_id
        elif category == "CIK":
            pseudo = self.cik_id
        else:
            next_value = self._category_counts.get(category, 0) + 1
            self._category_counts[category] = next_value
            pseudo = f"{category}_{next_value:03d}"
        bucket[clean] = pseudo
        return pseudo

    def public_ids(self) -> dict[str, str]:
        return {
            "company_id": self.company_id,
            "ticker_id": self.ticker_id,
            "cik_id": self.cik_id,
        }


def now_utc() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(payload))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_error(exc: BaseException | str) -> str:
    text = str(exc)
    text = SECRET_VALUE_RE.sub("[REDACTED_SECRET]", text)
    text = URL_RE.sub("[REDACTED_URL]", text)
    text = LOCAL_PATH_RE.sub("[REDACTED_LOCAL_PATH]", text)
    text = text.replace("NVIDIA_API_KEY", "provider credential")
    return text[:220]


def load_yfinance() -> Any:
    try:
        import yfinance as yf  # type: ignore[import-not-found]

        return yf
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("yfinance is unavailable in this environment") from exc


def dataframe_empty(df: Any) -> bool:
    return df is None or getattr(df, "empty", True)


def dataframe_to_csv(path: Path, df: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if dataframe_empty(df):
        path.write_text("", encoding="utf-8")
        return 0
    df.to_csv(path)
    return int(len(df))


def series_to_csv(path: Path, series: Any, value_name: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if series is None or len(series) == 0:
        path.write_text(f"event_index,{value_name}\n", encoding="utf-8")
        return 0
    df = series.rename(value_name).reset_index()
    df.insert(0, "event_index", range(len(df)))
    df.to_csv(path, index=False)
    return int(len(df))


def rel_day_lookup(index: Any) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for idx, value in enumerate(index):
        try:
            lookup[str(value.date())] = idx
        except AttributeError:
            lookup[str(value)[:10]] = idx
    return lookup


def finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def rebase(value: Any, base: float | None) -> float | None:
    number = finite_float(value)
    if number is None or base in (None, 0):
        return None
    return round((number / base) * 100, 6)


def write_rebased_prices(path: Path, history: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "relative_day",
        "open_index",
        "high_index",
        "low_index",
        "close_index",
        "volume_index",
    ]
    if dataframe_empty(history):
        with path.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(columns)
        return 0

    close_base = finite_float(history["Close"].dropna().iloc[0]) if "Close" in history else None
    volume_base = finite_float(history["Volume"].dropna().iloc[0]) if "Volume" in history else None
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for idx, (_, row) in enumerate(history.iterrows()):
            writer.writerow(
                {
                    "relative_day": f"DAY_{idx:04d}",
                    "open_index": rebase(row.get("Open"), close_base),
                    "high_index": rebase(row.get("High"), close_base),
                    "low_index": rebase(row.get("Low"), close_base),
                    "close_index": rebase(row.get("Close"), close_base),
                    "volume_index": rebase(row.get("Volume"), volume_base),
                }
            )
    return int(len(history))


def write_rebased_events(path: Path, series: Any, value_name: str, price_index: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    day_lookup = rel_day_lookup(getattr(price_index, "index", []))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["event_index", "relative_day", f"{value_name}_index"]
        )
        writer.writeheader()
        if series is None or len(series) == 0:
            return 0
        base = finite_float(series.dropna().iloc[0]) if len(series.dropna()) else None
        for idx, (event_date, value) in enumerate(series.items()):
            date_key = (
                str(event_date.date()) if hasattr(event_date, "date") else str(event_date)[:10]
            )
            rel = day_lookup.get(date_key)
            writer.writerow(
                {
                    "event_index": f"EVENT_{idx:03d}",
                    "relative_day": f"DAY_{rel:04d}" if rel is not None else "",
                    f"{value_name}_index": rebase(value, base),
                }
            )
    return int(len(series))


def write_split_events(path: Path, series: Any, price_index: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    day_lookup = rel_day_lookup(getattr(price_index, "index", []))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["event_index", "relative_day", "split_ratio"])
        writer.writeheader()
        if series is None or len(series) == 0:
            return 0
        for idx, (event_date, value) in enumerate(series.items()):
            date_key = (
                str(event_date.date()) if hasattr(event_date, "date") else str(event_date)[:10]
            )
            rel = day_lookup.get(date_key)
            writer.writerow(
                {
                    "event_index": f"EVENT_{idx:03d}",
                    "relative_day": f"DAY_{rel:04d}" if rel is not None else "",
                    "split_ratio": finite_float(value),
                }
            )
    return int(len(series))


def write_statement_relative(path: Path, df: Any, context: CompanyContext) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if dataframe_empty(df):
        with path.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(["line_item"])
        return 0

    columns = [f"PERIOD_{idx:02d}" for idx, _ in enumerate(df.columns)]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["line_item", *columns])
        for line_item, row in df.iterrows():
            clean_line_item = anonymize_text(str(line_item), context)
            writer.writerow([clean_line_item, *[finite_float(row[col]) for col in df.columns]])
    return int(len(df))


def collect_metrics(
    ticker: str,
    context: CompanyContext,
    output_root: Path,
    years: int,
) -> tuple[str, dict[str, int], list[SourceFailure], str]:
    failures: list[SourceFailure] = []
    artifacts: dict[str, int] = {}
    originals_dir = output_root / "originals" / ticker / "metrics"
    public_dir = output_root / "anonymized" / ticker / "metrics"
    originals_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)

    try:
        yf = load_yfinance()
        yf_ticker = yf.Ticker(ticker)
        history = yf_ticker.history(period=f"{years}y", auto_adjust=False, actions=False)
        dividends = getattr(yf_ticker, "dividends", None)
        splits = getattr(yf_ticker, "splits", None)
        statements = {
            "income_statement_annual": getattr(yf_ticker, "income_stmt", None),
            "income_statement_quarterly": getattr(yf_ticker, "quarterly_income_stmt", None),
            "balance_sheet_annual": getattr(yf_ticker, "balance_sheet", None),
            "balance_sheet_quarterly": getattr(yf_ticker, "quarterly_balance_sheet", None),
            "cash_flow_annual": getattr(yf_ticker, "cashflow", None),
            "cash_flow_quarterly": getattr(yf_ticker, "quarterly_cashflow", None),
        }

        artifacts["original_prices_rows"] = dataframe_to_csv(
            originals_dir / "daily_prices.csv", history
        )
        artifacts["public_prices_rows"] = write_rebased_prices(
            public_dir / "daily_price_index.csv", history
        )
        artifacts["original_dividend_rows"] = series_to_csv(
            originals_dir / "dividends.csv", dividends, "dividend"
        )
        artifacts["public_dividend_rows"] = write_rebased_events(
            public_dir / "dividends.csv", dividends, "dividend", history
        )
        artifacts["original_split_rows"] = series_to_csv(
            originals_dir / "splits.csv", splits, "split"
        )
        artifacts["public_split_rows"] = write_split_events(
            public_dir / "splits.csv", splits, history
        )

        for name, df in statements.items():
            artifacts[f"original_{name}_rows"] = dataframe_to_csv(originals_dir / f"{name}.csv", df)
            artifacts[f"public_{name}_rows"] = write_statement_relative(
                public_dir / f"{name}.csv", df, context
            )

        write_json(
            public_dir / "manifest.json",
            {
                "schema_version": "1.0",
                **context.public_ids(),
                "price_history": {
                    "window_years_requested": years,
                    "period_labeling": "relative_day",
                    "price_transform": "rebased first close equals 100",
                },
                "statement_periods": "relative latest-first columns",
                "generated_at": now_utc(),
            },
        )
        return "OK", artifacts, failures, build_metrics_summary(public_dir)
    except Exception as exc:  # noqa: BLE001
        failures.append(SourceFailure(ticker, "metrics", "FAIL", safe_error(exc)))
        write_json(
            public_dir / "manifest.json",
            {
                "schema_version": "1.0",
                **context.public_ids(),
                "status": "INCOMPLETE",
                "reason": "metrics source unavailable",
            },
        )
        return "FAIL", artifacts, failures, "metrics unavailable"


def build_metrics_summary(public_dir: Path) -> str:
    manifest_path = public_dir / "manifest.json"
    prices_path = public_dir / "daily_price_index.csv"
    row_count = 0
    if prices_path.exists():
        with prices_path.open(encoding="utf-8") as fh:
            row_count = max(0, sum(1 for _ in fh) - 1)
    return f"Metrics summary: daily rebased price rows={row_count}; manifest={manifest_path.name}."


class SECClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": SEC_USER_AGENT, "Accept": "application/json,text/html"}
        )
        self.last_request = 0.0

    def _sleep(self) -> None:
        elapsed = time.monotonic() - self.last_request
        if elapsed < SEC_SLEEP_SECONDS:
            time.sleep(SEC_SLEEP_SECONDS - elapsed)
        self.last_request = time.monotonic()

    def get_json(self, url: str) -> Any:
        self._sleep()
        response = self.session.get(url, timeout=SEC_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str, max_bytes: int = MAX_SEC_HTML_BYTES) -> str:
        self._sleep()
        response = self.session.get(url, timeout=SEC_TIMEOUT_SECONDS)
        response.raise_for_status()
        content = response.content[:max_bytes]
        return content.decode(response.encoding or "utf-8", errors="replace")


def resolve_cik(ticker: str, client: SECClient) -> tuple[str | None, str]:
    fallback = str(COMPANY_DATA.get(ticker, {}).get("cik", "")).zfill(10)
    try:
        payload = client.get_json("https://www.sec.gov/files/company_tickers.json")
        if isinstance(payload, Mapping):
            for item in payload.values():
                if isinstance(item, Mapping) and str(item.get("ticker", "")).upper() == ticker:
                    return str(item.get("cik_str", "")).zfill(10), "sec_company_tickers"
    except Exception:
        pass
    return (fallback or None), "static_fallback" if fallback else "unresolved"


def recent_rows(submissions: Mapping[str, Any], limit: int = 200) -> list[dict[str, str]]:
    recent = submissions.get("filings", {}).get("recent", {})
    if not isinstance(recent, Mapping):
        return []
    forms = list(recent.get("form", []) or [])
    rows: list[dict[str, str]] = []
    for idx, form in enumerate(forms[:limit]):

        def value(name: str, row_index: int = idx) -> str:
            values = recent.get(name, []) or []
            return str(values[row_index]) if row_index < len(values) else ""

        rows.append(
            {
                "form": str(form),
                "accessionNumber": value("accessionNumber"),
                "filingDate": value("filingDate"),
                "reportDate": value("reportDate"),
                "primaryDocument": value("primaryDocument"),
                "fileNumber": value("fileNumber"),
            }
        )
    return rows


def latest_row(rows: Sequence[Mapping[str, str]], form: str) -> Mapping[str, str] | None:
    form = form.upper()
    for row in rows:
        if str(row.get("form", "")).upper() == form:
            return row
    return None


def filing_url(cik: str, row: Mapping[str, str]) -> str | None:
    accession = str(row.get("accessionNumber", ""))
    primary = str(row.get("primaryDocument", ""))
    if not accession or not primary:
        return None
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary}"
    )


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


SECTION_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("business", r"item\s+1\.?\s*(?:business)?", ("item 1a", "item 2")),
    ("risk_factors", r"item\s+1a\.?\s*risk\s+factors", ("item 1b", "item 2")),
    ("md_and_a", r"item\s+7\.?\s*management", ("item 7a", "item 8")),
    ("financial_statement_summary", r"item\s+8\.?\s*financial\s+statements", ("item 9",)),
)


def normalize_item_search(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def find_section_bounds(
    text: str, start_pattern: str, end_tokens: Sequence[str]
) -> tuple[int, int] | None:
    match = re.search(start_pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    start = match.start()
    tail = text[match.end() :]
    end_offsets = []
    for token in end_tokens:
        end_match = re.search(re.escape(token), tail, flags=re.IGNORECASE)
        if end_match:
            end_offsets.append(match.end() + end_match.start())
    end = min(end_offsets) if end_offsets else min(len(text), start + MAX_10K_SECTION_CHARS)
    if end <= start:
        return None
    return start, min(end, start + MAX_10K_SECTION_CHARS)


def extract_10k_sections(text: str) -> dict[str, str]:
    compact = text
    sections: dict[str, str] = {}
    for key, start_pattern, end_tokens in SECTION_SPECS:
        bounds = find_section_bounds(compact, start_pattern, end_tokens)
        if bounds:
            body = compact[bounds[0] : bounds[1]].strip()
            if body:
                sections[key] = body
    return sections


def register_sec_identifiers(context: CompanyContext, rows: Sequence[Mapping[str, str]]) -> None:
    if context.cik:
        context.assign(context.cik, "CIK")
        context.assign(context.cik.lstrip("0"), "CIK")
    for row in rows:
        accession = str(row.get("accessionNumber", ""))
        if accession:
            context.assign(accession, "FILING_ID")
            context.assign(accession.replace("-", ""), "FILING_ID")
        file_number = str(row.get("fileNumber", ""))
        if file_number:
            context.assign(file_number, "FILING_ID")
        primary = str(row.get("primaryDocument", ""))
        if primary and primary.lower() not in {"index.html", "-"}:
            context.assign(primary, "FILING_ID")


def collect_sec(
    ticker: str,
    context: CompanyContext,
    output_root: Path,
) -> tuple[str, dict[str, int], list[SourceFailure], str]:
    failures: list[SourceFailure] = []
    artifacts: dict[str, int] = {}
    originals_dir = output_root / "originals" / ticker / "sec"
    public_dir = output_root / "anonymized" / ticker / "sec"
    originals_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)
    client = SECClient()

    if not context.cik:
        failures.append(SourceFailure(ticker, "sec", "FAIL", "CIK unresolved"))
        write_json(public_dir / "manifest.json", {"status": "INCOMPLETE", **context.public_ids()})
        return "FAIL", artifacts, failures, ""

    try:
        submissions = client.get_json(f"https://data.sec.gov/submissions/CIK{context.cik}.json")
        companyfacts = client.get_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{context.cik}.json"
        )
        write_json(originals_dir / "submissions.json", submissions)
        write_json(originals_dir / "companyfacts.json", companyfacts)
    except Exception as exc:  # noqa: BLE001
        failures.append(SourceFailure(ticker, "sec", "FAIL", safe_error(exc)))
        write_json(public_dir / "manifest.json", {"status": "INCOMPLETE", **context.public_ids()})
        return "FAIL", artifacts, failures, ""

    rows = recent_rows(submissions if isinstance(submissions, Mapping) else {})
    register_sec_identifiers(context, rows)
    artifacts["filing_inventory_rows"] = write_public_filing_inventory(
        public_dir / "filing_inventory.csv", rows, context
    )
    write_companyfacts_summary(public_dir / "companyfacts_summary.json", companyfacts, context)

    section_samples: list[str] = []
    latest_10k = latest_row(rows, "10-K")
    if latest_10k is not None:
        try:
            url = filing_url(context.cik, latest_10k)
            if url:
                context.assign(url, "URL")
                raw = client.get_text(url)
                text = html_to_text(raw)
                write_text(originals_dir / "latest_10k_text.txt", text[:1_500_000])
                sections = extract_10k_sections(text)
                if not sections:
                    sections = {"latest_10k_excerpt": text[:MAX_EXCERPT_CHARS]}
                for section_key, body in sections.items():
                    anon = anonymize_text(body, context)
                    write_text(public_dir / f"{section_key}.md", anon)
                    section_samples.append(anon[:NVIDIA_MAX_SAMPLE_CHARS])
                artifacts["ten_k_sections"] = len(sections)
        except Exception as exc:  # noqa: BLE001
            failures.append(SourceFailure(ticker, "sec_10k", "INCOMPLETE", safe_error(exc)))

    for form in ("10-Q", "8-K"):
        row = latest_row(rows, form)
        if row is None:
            continue
        try:
            url = filing_url(context.cik, row)
            if not url:
                continue
            context.assign(url, "URL")
            text = html_to_text(client.get_text(url))[:MAX_EXCERPT_CHARS]
            key = f"latest_{form.lower().replace('-', '_')}_excerpt"
            write_text(originals_dir / f"{key}.txt", text)
            write_text(public_dir / f"{key}.md", anonymize_text(text, context))
            artifacts[key] = 1
        except Exception as exc:  # noqa: BLE001
            failures.append(
                SourceFailure(ticker, f"sec_{form.lower()}", "INCOMPLETE", safe_error(exc))
            )

    write_json(
        public_dir / "manifest.json",
        {
            "schema_version": "1.0",
            **context.public_ids(),
            "status": "OK" if section_samples else "INCOMPLETE",
            "bounded_excerpts_only": True,
            "generated_at": now_utc(),
        },
    )
    artifacts["sec_public_files"] = sum(1 for _ in public_dir.glob("*"))
    summary = section_samples[0] if section_samples else ""
    return ("OK" if section_samples else "INCOMPLETE"), artifacts, failures, summary


def write_public_filing_inventory(
    path: Path, rows: Sequence[Mapping[str, str]], context: CompanyContext
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "filing_sequence",
                "filing_id",
                "form",
                "relative_filing_order",
                "primary_document_id",
                "file_number_id",
            ],
        )
        writer.writeheader()
        for idx, row in enumerate(rows):
            accession = str(row.get("accessionNumber", ""))
            primary = str(row.get("primaryDocument", ""))
            file_number = str(row.get("fileNumber", ""))
            writer.writerow(
                {
                    "filing_sequence": f"FILING_ROW_{idx:03d}",
                    "filing_id": context.assign(accession, "FILING_ID") if accession else "",
                    "form": row.get("form", ""),
                    "relative_filing_order": f"LATEST_MINUS_{idx}",
                    "primary_document_id": context.assign(primary, "FILING_ID") if primary else "",
                    "file_number_id": context.assign(file_number, "FILING_ID")
                    if file_number
                    else "",
                }
            )
    return len(rows)


def write_companyfacts_summary(path: Path, companyfacts: Any, context: CompanyContext) -> None:
    facts = companyfacts.get("facts", {}) if isinstance(companyfacts, Mapping) else {}
    summary: dict[str, Any] = {
        "schema_version": "1.0",
        **context.public_ids(),
        "taxonomy_count": len(facts) if isinstance(facts, Mapping) else 0,
        "taxonomies": [],
    }
    if isinstance(facts, Mapping):
        for taxonomy, taxonomy_payload in sorted(facts.items()):
            concepts = taxonomy_payload if isinstance(taxonomy_payload, Mapping) else {}
            summary["taxonomies"].append({"taxonomy": taxonomy, "concept_count": len(concepts)})
    write_json(path, summary)


def field_from_news_item(item: Mapping[str, Any], *keys: str) -> str:
    current: Any = item
    for key in keys:
        if isinstance(current, Mapping):
            current = current.get(key)
        else:
            return ""
    if current is None:
        return ""
    if isinstance(current, Mapping):
        for nested_key in ("raw", "fmt", "display", "url"):
            value = current.get(nested_key)
            if value:
                return str(value)
        return ""
    return str(current)


def normalize_news_item(item: Mapping[str, Any], index: int) -> dict[str, str]:
    content = item.get("content") if isinstance(item.get("content"), Mapping) else item
    if not isinstance(content, Mapping):
        content = item
    url = (
        field_from_news_item(content, "clickThroughUrl", "url")
        or field_from_news_item(content, "canonicalUrl", "url")
        or field_from_news_item(item, "link")
    )
    return {
        "index": str(index),
        "headline": field_from_news_item(content, "title") or field_from_news_item(item, "title"),
        "publisher": field_from_news_item(content, "provider", "displayName")
        or field_from_news_item(item, "publisher"),
        "timestamp": field_from_news_item(content, "pubDate")
        or field_from_news_item(content, "displayTime")
        or field_from_news_item(item, "providerPublishTime"),
        "summary": field_from_news_item(content, "summary")
        or field_from_news_item(item, "summary"),
        "url": url,
    }


def collect_news(
    ticker: str,
    context: CompanyContext,
    output_root: Path,
    news_limit: int,
) -> tuple[str, dict[str, int], list[SourceFailure], str]:
    failures: list[SourceFailure] = []
    artifacts: dict[str, int] = {}
    originals_dir = output_root / "originals" / ticker / "news"
    public_dir = output_root / "anonymized" / ticker / "news"
    originals_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)
    try:
        yf = load_yfinance()
        items = yf.Ticker(ticker).news or []
        if not isinstance(items, list):
            items = []
        write_json(originals_dir / "news_items.json", items[:news_limit])
        normalized = [
            normalize_news_item(item, idx)
            for idx, item in enumerate(items[:news_limit])
            if isinstance(item, Mapping)
        ]
        public_rows: list[dict[str, str]] = []
        for item in normalized:
            public_item = {
                "index": item["index"],
                "headline": anonymize_text(item["headline"], context),
                "publisher": anonymize_text(item["publisher"], context),
                "timestamp": item["timestamp"],
                "summary": anonymize_text(item["summary"], context),
                "url": context.assign(item["url"], "URL") if item["url"] else "",
            }
            if item["url"]:
                context.assign(item["url"], "URL")
            public_rows.append(public_item)
        write_json(public_dir / "news_items.json", {"items": public_rows, **context.public_ids()})
        artifacts["news_items"] = len(public_rows)
        if not public_rows:
            failures.append(SourceFailure(ticker, "news", "INCOMPLETE", "no news items returned"))
            return "INCOMPLETE", artifacts, failures, ""
        return "OK", artifacts, failures, json.dumps(public_rows[0], sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        failures.append(SourceFailure(ticker, "news", "FAIL", safe_error(exc)))
        write_json(
            public_dir / "news_items.json",
            {"items": [], "status": "INCOMPLETE", **context.public_ids()},
        )
        return "FAIL", artifacts, failures, ""


def base_aliases_for_context(context: CompanyContext) -> list[tuple[str, str]]:
    data = COMPANY_DATA.get(context.ticker, {})
    pairs: list[tuple[str, str]] = []
    for value in data.get("aliases", []):
        if str(value).upper() == context.ticker:
            continue
        pairs.append((str(value), context.assign(str(value), "COMPANY")))
    pairs.append((context.ticker, context.assign(context.ticker, "TICKER")))
    if context.cik:
        pairs.append((context.cik, context.assign(context.cik, "CIK")))
        pairs.append((context.cik.lstrip("0"), context.assign(context.cik.lstrip("0"), "CIK")))
    for value in data.get("domains", []):
        pairs.append((str(value), context.assign(str(value), "DOMAIN")))
    for value in data.get("products", []):
        pairs.append((str(value), context.assign(str(value), "PRODUCT")))
    for value in data.get("executives", []):
        pairs.append((str(value), context.assign(str(value), "EXEC")))
    for category, mapping in context.private_map.items():
        for original, pseudo in mapping.items():
            if category not in {"URL"}:
                pairs.append((original, pseudo))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


def anonymize_text(text: str, context: CompanyContext) -> str:
    if not text:
        return ""
    result = SECRET_VALUE_RE.sub("[REDACTED_SECRET]", text)
    result = LOCAL_PATH_RE.sub("[REDACTED_LOCAL_PATH]", result)

    def replace_url(match: re.Match[str]) -> str:
        return context.assign(match.group(0), "URL")

    result = URL_RE.sub(replace_url, result)
    result = WWW_RE.sub(replace_url, result)

    def replace_accession(match: re.Match[str]) -> str:
        return context.assign(match.group(0), "FILING_ID")

    result = ACCESSION_RE.sub(replace_accession, result)
    result = SEC_FILE_NUMBER_RE.sub(replace_accession, result)

    for original, pseudo in base_aliases_for_context(context):
        if not original:
            continue
        if original.upper() == context.ticker:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9]){re.escape(original)}(?![A-Za-z0-9])", re.IGNORECASE
            )
        elif original.isdigit() and len(original) >= 5:
            pattern = re.compile(rf"\b0*{re.escape(original.lstrip('0') or original)}\b")
        else:
            pattern = re.compile(re.escape(original), re.IGNORECASE)
        result = pattern.sub(pseudo, result)
    result = result.replace("NVIDIA_API_KEY", "provider credential")
    return SECRET_VALUE_RE.sub("[REDACTED_SECRET]", result)


def residual_scan_for_ticker(
    root: Path, ticker: str, context: CompanyContext
) -> tuple[str, dict[str, Any]]:
    public_dir = root / "anonymized" / ticker
    total_hits = 0
    hits_by_category: dict[str, int] = {}
    text_files = [
        path
        for path in public_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".csv", ".txt"}
    ]
    for path in text_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for category, mapping in context.private_map.items():
            for original in mapping:
                if not original:
                    continue
                pattern = residual_pattern(original, category, context.ticker)
                count = len(pattern.findall(text))
                if count:
                    total_hits += count
                    hits_by_category[category] = hits_by_category.get(category, 0) + count
        for forbidden in FORBIDDEN_ZIP_SUBSTRINGS:
            if forbidden in text:
                total_hits += 1
                hits_by_category["FORBIDDEN_LITERAL"] = (
                    hits_by_category.get("FORBIDDEN_LITERAL", 0) + 1
                )
    status = "PASS" if total_hits == 0 else "FAIL"
    report = {
        "schema_version": "1.0",
        **context.public_ids(),
        "overall_status": status,
        "text_files_scanned": len(text_files),
        "total_hits": total_hits,
        "hits_by_category": hits_by_category,
        "generated_at": now_utc(),
    }
    write_json(root / "qa" / f"{ticker}_residual_scan.json", report)
    return status, report


def residual_pattern(original: str, category: str, ticker: str = "") -> re.Pattern[str]:
    if category == "TICKER" or (ticker and original.upper() == ticker.upper()):
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(original)}(?![A-Za-z0-9])", re.IGNORECASE)
    if category == "CIK" and original.isdigit():
        return re.compile(rf"\b0*{re.escape(original.lstrip('0') or original)}\b")
    return re.compile(re.escape(original), re.IGNORECASE)


def run_nvidia_qa(
    ticker: str,
    context: CompanyContext,
    samples: Sequence[str],
    enable_nvidia_qa: str,
) -> dict[str, Any]:
    bounded_samples = [sample[:NVIDIA_MAX_SAMPLE_CHARS] for sample in samples if sample]
    payload_base: dict[str, Any] = {
        "schema_version": "1.0",
        **context.public_ids(),
        "bounded_review": True,
        "sample_count": len(bounded_samples[:3]),
        "max_sample_chars": NVIDIA_MAX_SAMPLE_CHARS,
    }
    if enable_nvidia_qa.lower() == "no":
        return {**payload_base, "status": "NOT_RUN", "decision": "NOT_RUN", "reason": "disabled"}
    if not bounded_samples:
        return {
            **payload_base,
            "status": "INCOMPLETE",
            "decision": "NOT_RUN",
            "reason": "no samples",
        }
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        return {
            **payload_base,
            "status": "INCOMPLETE",
            "decision": "NOT_RUN",
            "reason": "provider credential not configured",
        }

    try:
        response = requests.post(
            os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
            + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.environ.get("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a privacy reviewer. Return compact JSON only. "
                            "Do not quote source text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Can these anonymized excerpts identify the original company? "
                            "Return JSON with confidence number 0-1, guess_present boolean, "
                            "direct_identifier_present boolean, and evidence_types array.\n\n"
                            + "\n\n--- SAMPLE ---\n".join(bounded_samples[:3])
                        ),
                    },
                ],
                "temperature": 0,
                "max_tokens": 400,
            },
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        content = str(data["choices"][0]["message"]["content"])
        parsed = parse_model_json(content)
        confidence = finite_float(parsed.get("confidence")) if parsed else None
        guess_present = bool(parsed.get("guess_present")) if parsed else False
        direct_present = bool(parsed.get("direct_identifier_present")) if parsed else False
        if parsed is None or confidence is None:
            status = "INCOMPLETE"
            decision = "REVIEW_REQUIRED"
        elif direct_present or confidence >= 0.7:
            status = "FAIL"
            decision = "FAIL"
        elif guess_present or confidence >= 0.35:
            status = "REVIEW_REQUIRED"
            decision = "REVIEW_REQUIRED"
        else:
            status = "PASS"
            decision = "PASS"
        return {
            **payload_base,
            "status": status,
            "decision": decision,
            "confidence": confidence,
            "guess_present": guess_present,
            "direct_identifier_present": direct_present,
            "parse_status": "OK" if parsed else "INCOMPLETE",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            **payload_base,
            "status": "INCOMPLETE",
            "decision": "NOT_RUN",
            "reason": safe_error(exc),
            "error_class": type(exc).__name__,
        }


def parse_model_json(content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


def write_private_map(root: Path, ticker: str, context: CompanyContext) -> None:
    write_json(
        root / "private_maps" / ticker / "identity_map.json",
        {
            "schema_version": "1.0",
            "ticker": ticker,
            "cik": context.cik,
            "public_ids": context.public_ids(),
            "private_to_public": context.private_map,
            "generated_at": now_utc(),
        },
    )


def build_one_ticker(
    root: Path, ticker: str, ticker_index: int, years: int, news_limit: int, enable_nvidia_qa: str
) -> TickerResult:
    sec_client = SECClient()
    cik, _source = resolve_cik(ticker, sec_client)
    context = CompanyContext(ticker, ticker_index, cik)
    context.assign(ticker, "TICKER")
    if cik:
        context.assign(cik, "CIK")
        context.assign(cik.lstrip("0"), "CIK")
    for alias in COMPANY_DATA.get(ticker, {}).get("aliases", []):
        if str(alias).upper() == ticker:
            continue
        context.assign(str(alias), "COMPANY")
    for domain in COMPANY_DATA.get(ticker, {}).get("domains", []):
        context.assign(str(domain), "DOMAIN")
    for product in COMPANY_DATA.get(ticker, {}).get("products", []):
        context.assign(str(product), "PRODUCT")
    for executive in COMPANY_DATA.get(ticker, {}).get("executives", []):
        context.assign(str(executive), "EXEC")

    metrics_status, metrics_artifacts, metric_failures, metrics_sample = collect_metrics(
        ticker, context, root, years
    )
    sec_status, sec_artifacts, sec_failures, sec_sample = collect_sec(ticker, context, root)
    news_status, news_artifacts, news_failures, news_sample = collect_news(
        ticker, context, root, news_limit
    )
    write_private_map(root, ticker, context)
    residual_status, _residual_report = residual_scan_for_ticker(root, ticker, context)
    nvidia_result = run_nvidia_qa(
        ticker, context, [sec_sample, metrics_sample, news_sample], enable_nvidia_qa
    )
    write_json(root / "anonymized" / ticker / "qa" / "nvidia_review.json", nvidia_result)

    artifacts = {
        "metrics_files": count_files(root / "anonymized" / ticker / "metrics"),
        "sec_files": count_files(root / "anonymized" / ticker / "sec"),
        "news_files": count_files(root / "anonymized" / ticker / "news"),
        "qa_files": count_files(root / "anonymized" / ticker / "qa"),
        **metrics_artifacts,
        **sec_artifacts,
        **news_artifacts,
    }
    return TickerResult(
        ticker=ticker,
        company_id=context.company_id,
        ticker_id=context.ticker_id,
        cik_id=context.cik_id,
        cik_resolved=bool(cik),
        metrics_status=metrics_status,
        sec_status=sec_status,
        news_status=news_status,
        residual_status=residual_status,
        nvidia_status=str(nvidia_result.get("status", "INCOMPLETE")),
        artifacts=artifacts,
        source_failures=[*metric_failures, *sec_failures, *news_failures],
    )


def count_files(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file()) if path.exists() else 0


def write_docs(root: Path, results: Sequence[TickerResult]) -> None:
    docs = {
        "README.md": """# FENRIX 8-Company Anonymized Submission

This bundle is a deterministic, classroom-oriented anonymized artifact built
from eight public-company data collection attempts. It is not a mathematical
anonymity guarantee and should not be described as release-safe against a
motivated semantic re-identification attack.

The package contains anonymized metrics, bounded SEC excerpts, news summaries,
QA reports, checksums, and inventory metadata. Source material and private
replacement maps are intentionally not part of the ZIP.
""",
        "QUICKSTART.md": """# Quickstart

Inspect the ZIP:

```bash
unzip -l exports/anonymized_bundle.zip | head -40
```

Review QA:

```bash
cat qa/release_gate.json
cat qa/residual_scan_summary.csv
cat qa/nvidia_attack_summary.csv
```

The anonymized company folders contain metrics, SEC excerpts, news items, and
per-company QA files.
""",
        "DATA_DICTIONARY.md": """# Data Dictionary

## Metrics

`daily_price_index.csv` uses relative day labels and rebases the first close to
100. Dividend events are rebased to the first available dividend. Split ratios
are retained. Statement columns use relative period labels.

## SEC

`filing_inventory.csv` replaces accessions, primary documents, and file numbers
with filing IDs. Bounded text excerpts are deterministic literal-replacement
outputs.

## News

`news_items.json` includes headline, publisher, timestamp, summary, and a URL
pseudonym when available.

## QA

Residual scan reports count literal private-value hits after replacement.
NVIDIA QA is bounded and may be incomplete when the provider credential is not
configured or the provider fails.
""",
        "LIMITATIONS.md": """# Limitations

This artifact preserves educational utility, not anonymity. Numeric patterns,
business descriptions, and public filing structure can still be identifying.

The residual scan is literal-only. It does not detect semantic clues. NVIDIA QA
is bounded to short samples and is allowed to be incomplete. ZIP creation is not
blocked by imperfect semantic anonymity.

No full-filing model rewrite is performed.
""",
    }
    summary_lines = [
        "# Run Summary",
        "",
        "| Company | Metrics | SEC | News | Residual | NVIDIA |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        summary_lines.append(
            f"| {result.company_id} | {result.metrics_status} | {result.sec_status} | "
            f"{result.news_status} | {result.residual_status} | {result.nvidia_status} |"
        )
    docs["RUN_SUMMARY.md"] = "\n".join(summary_lines) + "\n"
    for name, body in docs.items():
        write_text(root / name, body)


def write_qa_summaries(root: Path, results: Sequence[TickerResult]) -> None:
    qa_dir = root / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    with (qa_dir / "residual_scan_summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "company_id",
                "ticker_id",
                "overall_status",
                "total_hits",
                "text_files_scanned",
            ],
        )
        writer.writeheader()
        for result in results:
            report_path = qa_dir / f"{result.ticker}_residual_scan.json"
            report = (
                json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
            )
            writer.writerow(
                {
                    "company_id": result.company_id,
                    "ticker_id": result.ticker_id,
                    "overall_status": result.residual_status,
                    "total_hits": report.get("total_hits", 0),
                    "text_files_scanned": report.get("text_files_scanned", 0),
                }
            )
    with (qa_dir / "nvidia_attack_summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "company_id",
                "ticker_id",
                "status",
                "decision",
                "sample_count",
                "error_class",
            ],
        )
        writer.writeheader()
        for result in results:
            qa_path = root / "anonymized" / result.ticker / "qa" / "nvidia_review.json"
            qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() else {}
            writer.writerow(
                {
                    "company_id": result.company_id,
                    "ticker_id": result.ticker_id,
                    "status": qa.get("status", "INCOMPLETE"),
                    "decision": qa.get("decision", "NOT_RUN"),
                    "sample_count": qa.get("sample_count", 0),
                    "error_class": qa.get("error_class", ""),
                }
            )

    residual_failures = sum(1 for result in results if result.residual_status != "PASS")
    nvidia_statuses = {result.nvidia_status for result in results}
    if nvidia_statuses == {"PASS"}:
        nvidia_rollup = "PASS"
    elif "FAIL" in nvidia_statuses:
        nvidia_rollup = "FAIL"
    elif "REVIEW_REQUIRED" in nvidia_statuses:
        nvidia_rollup = "REVIEW_REQUIRED"
    else:
        nvidia_rollup = "INCOMPLETE"
    write_json(
        qa_dir / "release_gate.json",
        {
            "schema_version": "1.0",
            "artifact_built": True,
            "anonymity_claimed": False,
            "residual_failures": residual_failures,
            "nvidia_rollup": nvidia_rollup,
            "zip_required_for_product": True,
            "generated_at": now_utc(),
        },
    )


def source_failures_payload(results: Sequence[TickerResult]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for result in results:
        for failure in result.source_failures:
            failures.append(dataclasses.asdict(failure))
    return failures


def write_run_summary(root: Path, results: Sequence[TickerResult]) -> None:
    write_json(
        root / "run_summary.json",
        {
            "schema_version": "1.0",
            "run_folder": root.name,
            "generated_at": now_utc(),
            "tickers_attempted": [result.ticker for result in results],
            "tickers_completed": [
                result.ticker
                for result in results
                if result.metrics_status != "FAIL"
                or result.sec_status != "FAIL"
                or result.news_status != "FAIL"
            ],
            "per_ticker": [dataclasses.asdict(result) for result in results],
            "source_failures": source_failures_payload(results),
            "notes": [
                "ZIP excludes source folders and private maps.",
                "No anonymity guarantee is claimed.",
                "NVIDIA QA is bounded and may be incomplete.",
            ],
        },
    )


def iter_public_files(root: Path) -> Iterable[Path]:
    for rel_name in sorted(PUBLIC_TOP_LEVEL_FILES):
        path = root / rel_name
        if path.is_file():
            yield path
    for dir_name in sorted(PUBLIC_TOP_LEVEL_DIRS):
        base = root / dir_name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and not should_exclude_from_zip(path.relative_to(root).as_posix()):
                yield path


def should_exclude_from_zip(rel: str) -> bool:
    if rel.startswith(("originals/", "private_maps/", "smoke_excerpts/", "exports/")):
        return True
    parts = rel.split("/")
    if ".git" in parts or "__pycache__" in parts:
        return True
    if rel.endswith((".pyc", ".pyo", ".DS_Store")):
        return True
    if any(forbidden in rel for forbidden in FORBIDDEN_ZIP_SUBSTRINGS):
        return True
    return False


def write_artifact_inventory(root: Path) -> None:
    rows: list[dict[str, str]] = []
    for path in iter_public_files(root):
        rel = path.relative_to(root).as_posix()
        rows.append(
            {
                "relative_path": rel,
                "size_bytes": str(path.stat().st_size),
                "sha256": sha256_bytes(path.read_bytes()),
            }
        )
    with (root / "artifact_inventory.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["relative_path", "size_bytes", "sha256"])
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(root: Path) -> None:
    lines = []
    for path in iter_public_files(root):
        rel = path.relative_to(root).as_posix()
        if rel == "checksums.sha256":
            continue
        lines.append(f"{sha256_bytes(path.read_bytes())}  {rel}")
    write_text(root / "checksums.sha256", "\n".join(lines) + "\n")


def package_zip(root: Path) -> Path:
    exports_dir = root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    zip_path = exports_dir / "anonymized_bundle.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in iter_public_files(root):
            rel = path.relative_to(root).as_posix()
            if should_exclude_from_zip(rel):
                continue
            zf.write(path, rel)
    return zip_path


def validate_zip(zip_path: Path) -> ZipValidationResult:
    forbidden_name_hits: list[str] = []
    forbidden_text_hits: list[str] = []
    api_key_hits: list[str] = []
    local_path_hits: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for name in names:
            if any(forbidden in name for forbidden in FORBIDDEN_ZIP_SUBSTRINGS):
                forbidden_name_hits.append(name)
            if name.endswith("/"):
                continue
            if not name.lower().endswith((".md", ".json", ".csv", ".txt", ".sha256")):
                continue
            text = zf.read(name).decode("utf-8", errors="replace")
            for forbidden in FORBIDDEN_ZIP_SUBSTRINGS:
                if forbidden in text:
                    forbidden_text_hits.append(f"{name}:{forbidden}")
            if SECRET_VALUE_RE.search(text) or "NVIDIA_API_KEY" in text:
                api_key_hits.append(name)
            if LOCAL_PATH_RE.search(text):
                local_path_hits.append(name)
    ok = not (forbidden_name_hits or forbidden_text_hits or api_key_hits or local_path_hits)
    return ZipValidationResult(
        ok=ok,
        entry_count=len(names),
        byte_size=zip_path.stat().st_size,
        forbidden_name_hits=forbidden_name_hits,
        forbidden_text_hits=forbidden_text_hits,
        api_key_hits=api_key_hits,
        local_path_hits=local_path_hits,
    )


def prepare_output_tree(root: Path, tickers: Sequence[str]) -> None:
    for subdir in ("anonymized", "qa", "exports", "originals", "private_maps"):
        (root / subdir).mkdir(parents=True, exist_ok=True)
    for ticker in tickers:
        for subdir in ("metrics", "sec", "news", "qa"):
            (root / "anonymized" / ticker / subdir).mkdir(parents=True, exist_ok=True)
        for subdir in ("metrics", "sec", "news"):
            (root / "originals" / ticker / subdir).mkdir(parents=True, exist_ok=True)
        (root / "private_maps" / ticker).mkdir(parents=True, exist_ok=True)


def parse_tickers(raw: str) -> list[str]:
    return [ticker.strip().upper() for ticker in raw.split(",") if ticker.strip()]


def build_submission(
    tickers: Sequence[str],
    output_root: Path,
    years: int,
    news_limit: int,
    enable_nvidia_qa: str,
) -> tuple[Path, list[TickerResult], ZipValidationResult]:
    prepare_output_tree(output_root, tickers)
    results: list[TickerResult] = []
    for idx, ticker in enumerate(tickers, start=1):
        print(f"[build] {ticker} start", flush=True)
        result = build_one_ticker(output_root, ticker, idx, years, news_limit, enable_nvidia_qa)
        results.append(result)
        print(
            f"[build] {ticker} done metrics={result.metrics_status} sec={result.sec_status} "
            f"news={result.news_status} residual={result.residual_status} nvidia={result.nvidia_status}",
            flush=True,
        )
    write_qa_summaries(output_root, results)
    write_docs(output_root, results)
    write_run_summary(output_root, results)
    write_artifact_inventory(output_root)
    write_checksums(output_root)
    zip_path = package_zip(output_root)
    validation = validate_zip(zip_path)
    write_json(
        output_root / "qa" / "zip_validation.json",
        {
            "ok": validation.ok,
            "entry_count": validation.entry_count,
            "byte_size": validation.byte_size,
            "forbidden_name_hits": validation.forbidden_name_hits,
            "forbidden_text_hits": validation.forbidden_text_hits,
            "api_key_hits": validation.api_key_hits,
            "local_path_hits": validation.local_path_hits,
        },
    )
    if not validation.ok:
        # Repackage with validation report included so failed artifacts are diagnosable.
        zip_path = package_zip(output_root)
        validation = validate_zip(zip_path)
    return zip_path, results, validation


def default_output_root() -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.home() / "Desktop" / f"FENRIX_8_COMPANY_ANON_SUBMISSION_{timestamp}"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--output-root", default=str(default_output_root()))
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--news-limit", type=int, default=5)
    parser.add_argument("--enable-nvidia-qa", choices=["auto", "yes", "no"], default="auto")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    tickers = parse_tickers(args.tickers)
    if not tickers:
        print("No tickers provided", file=sys.stderr)
        return 2
    output_root = Path(args.output_root).expanduser()
    zip_path, results, validation = build_submission(
        tickers=tickers,
        output_root=output_root,
        years=args.years,
        news_limit=args.news_limit,
        enable_nvidia_qa=args.enable_nvidia_qa,
    )
    print(f"ZIP={zip_path}")
    print(f"ZIP_VALID={validation.ok}")
    print(f"ZIP_ENTRIES={validation.entry_count}")
    print(f"ZIP_BYTES={validation.byte_size}")
    nvidia_statuses = sorted({result.nvidia_status for result in results})
    print(f"NVIDIA_STATUSES={','.join(nvidia_statuses)}")
    return 0 if zip_path.exists() and validation.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
