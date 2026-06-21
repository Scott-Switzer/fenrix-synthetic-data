"""Sanitized public submission artifact builder."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import requests

from .submission_quality import (
    COMPANY_DATA,
    SEC_FILES,
    build_recent_event_summary,
    html_to_text,
    scrub_text,
    section,
    summarize,
)

SEC_USER_AGENT = os.environ.get(
    "FENRIX_SEC_USER_AGENT",
    "FENRIX Synthetic Data Worker contact fenrix-research@example.com",
)
PUBLIC_TOP_LEVEL_FILES = {
    "README.md",
    "run_summary.json",
    "artifact_inventory.csv",
    "checksums.sha256",
}
PUBLIC_TOP_LEVEL_DIRS = {"anonymized", "qa"}


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
    def __init__(self, ticker: str, ticker_index: int, cik: str | None = None) -> None:
        self.ticker = ticker.upper()
        self.index = ticker_index
        self.cik = cik
        self.company_id = f"COMPANY_{ticker_index:03d}"
        self.ticker_id = f"TICKER_{ticker_index:03d}"
        self.cik_id = f"CIK_{ticker_index:03d}"
        self.private_map: dict[str, dict[str, str]] = {}

    def assign(self, original: str, category: str) -> str:
        clean = str(original).strip()
        if not clean:
            return ""
        category = category.upper()
        bucket = self.private_map.setdefault(category, {})
        for existing, pseudo in bucket.items():
            if existing.casefold() == clean.casefold():
                return pseudo
        pseudo = {"COMPANY": self.company_id, "TICKER": self.ticker_id, "CIK": self.cik_id}.get(
            category,
            f"{category}_{len(bucket) + 1:03d}",
        )
        bucket[clean] = pseudo
        return pseudo

    def public_ids(self) -> dict[str, str]:
        return {"company_id": self.company_id, "ticker_id": self.ticker_id, "cik_id": self.cik_id}


class SECClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": SEC_USER_AGENT, "Accept": "application/json,text/html"}
        )
        self.last_request = 0.0

    def wait(self) -> None:
        delay = 0.25 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()

    def get_json(self, url: str) -> Any:
        self.wait()
        response = self.session.get(url, timeout=25)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str) -> str:
        self.wait()
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.content[:8_000_000].decode(response.encoding or "utf-8", errors="replace")


def now_utc() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_error(exc: BaseException | str) -> str:
    return scrub_text(str(exc))[:220].replace("NVIDIA_API_KEY", "provider credential")


def load_yfinance() -> Any:
    try:
        import yfinance as yf  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("install optional extra .[submission] or provide yfinance") from exc
    return yf


def seed_context(ctx: CompanyContext) -> None:
    data = COMPANY_DATA.get(ctx.ticker, {})
    ctx.assign(ctx.ticker, "TICKER")
    if ctx.cik:
        ctx.assign(ctx.cik, "CIK")
        ctx.assign(ctx.cik.lstrip("0"), "CIK")
    aliases = {str(value) for value in data.get("aliases", [])}
    for value in [*aliases, *data.get("domains", []), *data.get("people", [])]:
        ctx.assign(str(value), "COMPANY" if value in aliases else "IDENTIFIER")


def recent_rows(submissions: Mapping[str, Any]) -> list[dict[str, str]]:
    recent = submissions.get("filings", {}).get("recent", {})
    if not isinstance(recent, Mapping):
        return []
    forms = list(recent.get("form", []) or [])
    rows = []
    for idx, form in enumerate(forms[:1000]):
        row = {"form": str(form)}
        for key in ("accessionNumber", "primaryDocument", "fileNumber"):
            values = recent.get(key, []) or []
            row[key] = str(values[idx]) if idx < len(values) else ""
        rows.append(row)
    return rows


def latest_row(rows: Sequence[Mapping[str, str]], form: str) -> Mapping[str, str] | None:
    return next((row for row in rows if row.get("form", "").upper() == form), None)


def filing_url(cik: str, row: Mapping[str, str]) -> str | None:
    accession = row.get("accessionNumber", "")
    primary = row.get("primaryDocument", "")
    if not accession or not primary:
        return None
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary}"
    )


def resolve_cik(ticker: str, client: SECClient) -> str | None:
    try:
        data = client.get_json("https://www.sec.gov/files/company_tickers.json")
        for item in data.values():
            if str(item.get("ticker", "")).upper() == ticker:
                return str(item.get("cik_str", "")).zfill(10)
    except Exception:
        pass
    fallback = str(COMPANY_DATA.get(ticker, {}).get("cik", "")).zfill(10)
    return fallback or None


def collect_sec(
    ticker: str, ctx: CompanyContext, root: Path
) -> tuple[str, dict[str, int], list[SourceFailure], str]:
    public_dir = root / "anonymized" / ctx.company_id / "sec"
    public_dir.mkdir(parents=True, exist_ok=True)
    failures: list[SourceFailure] = []
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        **ctx.public_ids(),
        "sections": {},
        "generated_at": now_utc(),
    }
    summaries: dict[str, tuple[str, str]] = {}
    try:
        rows = (
            recent_rows(SECClient().get_json(f"https://data.sec.gov/submissions/CIK{ctx.cik}.json"))
            if ctx.cik
            else []
        )
        tenk_url = filing_url(ctx.cik or "", latest_row(rows, "10-K") or {}) if ctx.cik else None
        tenk = html_to_text(SECClient().get_text(tenk_url)) if tenk_url else ""
        specs = {
            "business": (r"item\s+1\b.{0,120}business", [r"item\s+1a\b", r"item\s+2\b"]),
            "risk_factors": (r"item\s+1a\b.{0,120}risk\s+factors", [r"item\s+1b\b", r"item\s+2\b"]),
            "mdna": (r"item\s+7\b.{0,180}management", [r"item\s+7a\b", r"item\s+8\b"]),
            "financial": (r"item\s+8\b.{0,160}financial", [r"item\s+9\b"]),
        }
        for key, spec in specs.items():
            summaries[key] = summarize(
                section(tenk, spec[0], spec[1]), ctx.private_map, key.replace("_", " ").title()
            )
        event_url = filing_url(ctx.cik or "", latest_row(rows, "8-K") or {}) if ctx.cik else None
        event_text = html_to_text(SECClient().get_text(event_url)) if event_url else ""
        summaries["recent_event"] = build_recent_event_summary(event_text, ctx.company_id, "8-K")
    except Exception as exc:  # noqa: BLE001
        failures.append(SourceFailure(ticker, "sec", "INCOMPLETE", safe_error(exc)))
    sample = ""
    for key, filename in SEC_FILES.items():
        body, reason = summaries.get(
            key,
            (
                f"# {key.replace('_', ' ').title()}\n\nUNAVAILABLE: source unavailable.\n",
                "source unavailable",
            ),
        )
        write_text(public_dir / filename, body)
        manifest["sections"][key] = reason
        sample = sample or body
    write_json(public_dir / "sec_manifest.json", manifest)
    ok = any(value == "OK" for value in manifest["sections"].values())
    return ("OK" if ok else "INCOMPLETE"), {"sec_files": 6}, failures, sample
