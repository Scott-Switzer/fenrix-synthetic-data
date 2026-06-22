"""Packaging and orchestration for sanitized submission artifacts."""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import re
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .submission_fast import (
    CompanyContext,
    SECClient,
    TickerResult,
    ZipValidationResult,
    collect_sec,
    now_utc,
    resolve_cik,
    seed_context,
    sha256_bytes,
    write_json,
    write_text,
)
from .submission_quality import (
    ACCESSION_RE,
    ADDRESS_RE,
    DATE_RE,
    DEFAULT_TICKERS,
    EIN_RE,
    FORBIDDEN_ZIP_SUBSTRINGS,
    HEADER_RE,
    PHONE_RE,
    ROLE_RE,
    SEC_FILE_RE,
    TARGET_TICKER_RE,
    TAXONOMY_RE,
    URL_RE,
    VOTE_RE,
)
from .submission_sources import collect_metrics, collect_news

PUBLIC_TOP_LEVEL_FILES = {
    "README.md",
    "run_summary.json",
    "artifact_inventory.csv",
    "checksums.sha256",
}
PUBLIC_TOP_LEVEL_DIRS = {"anonymized", "qa"}


def run_nvidia_qa(
    ticker: str,
    ctx: CompanyContext,
    samples: Sequence[str],
    enable_nvidia_qa: str,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    """Run NVIDIA artifact verification if enabled.

    When ``enable_nvidia_qa`` is ``"no"`` or the provider is unavailable, the
    build continues and the report status is NOT_RUN or INCOMPLETE. The
    verifier never receives originals or private maps.
    """
    if enable_nvidia_qa == "no":
        return {
            "schema_version": "1.0",
            **ctx.public_ids(),
            "status": "NOT_RUN",
            "decision": "NOT_RUN",
            "reason": "nvidia qa disabled by flag",
            "sample_count": len([sample for sample in samples if sample]),
        }
    from .submission_nvidia import (
        nvidia_available,
        verify_public_artifact_with_nvidia,
        write_nvidia_artifact_report,
    )

    if not nvidia_available():
        report: dict[str, Any] = {
            "schema_version": "1.0",
            **ctx.public_ids(),
            "status": "INCOMPLETE",
            "decision": "NOT_RUN",
            "reason": "provider credential not set",
            "sample_count": len([sample for sample in samples if sample]),
        }
        if artifact_root is not None:
            write_nvidia_artifact_report(report, artifact_root / "qa")
        return report
    if artifact_root is None:
        return {
            "schema_version": "1.0",
            **ctx.public_ids(),
            "status": "INCOMPLETE",
            "decision": "NOT_RUN",
            "reason": "artifact root not provided",
            "sample_count": len([sample for sample in samples if sample]),
        }
    company_dir = artifact_root / "anonymized" / ctx.company_id
    review = verify_public_artifact_with_nvidia(artifact_root, company_dir=company_dir)
    status = str(review.get("status", "INCOMPLETE")).upper()
    decision = {
        "PASS": "PASS",
        "REVIEW_REQUIRED": "REVIEW_REQUIRED",
        "FAIL": "FAIL",
    }.get(status, "NOT_RUN")
    report = {
        "schema_version": "1.0",
        **ctx.public_ids(),
        "status": status,
        "decision": decision,
        "reason": review.get("reason", review.get("summary", "")),
        "sample_count": len([sample for sample in samples if sample]),
        "files_reviewed": review.get("files_reviewed", 0),
        "repair_passes": review.get("repair_passes", 0),
        "risks": review.get("risks", []),
    }
    write_nvidia_artifact_report(report, artifact_root / "qa")
    return report


def write_private_map(root: Path, ticker: str, ctx: CompanyContext) -> None:
    write_json(
        root / "private_maps" / ticker / "identity_map.json",
        {"schema_version": "1.0", "private_to_public": ctx.private_map, **ctx.public_ids()},
    )


def residual_scan_for_ticker(
    root: Path, ticker: str, ctx: CompanyContext
) -> tuple[str, dict[str, Any]]:
    hits: list[str] = []
    for path in (root / "anonymized" / ctx.company_id).rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".json", ".csv", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for bucket in ctx.private_map.values():
            for value in bucket:
                pattern = rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])"
                if value and re.search(pattern, text, re.I):
                    hits.append(f"{path.name}:{value}")
    report: dict[str, Any] = {
        "schema_version": "1.0",
        **ctx.public_ids(),
        "overall_status": "PASS" if not hits else "FAIL",
        "total_hits": len(hits),
        "hits": hits[:20],
    }
    write_json(root / "qa" / f"{ctx.ticker_id}_residual_scan.json", report)
    return report["overall_status"], report


def build_one_ticker(
    root: Path,
    ticker: str,
    ticker_index: int,
    years: int,
    news_limit: int,
    enable_nvidia_qa: str,
) -> TickerResult:
    ctx = CompanyContext(ticker, ticker_index, resolve_cik(ticker, SECClient()))
    seed_context(ctx)
    metrics_status, metrics_artifacts, metric_failures, metrics_sample = collect_metrics(
        ticker, ctx, root, years
    )
    sec_status, sec_artifacts, sec_failures, sec_sample = collect_sec(ticker, ctx, root)
    news_status, news_artifacts, news_failures, news_sample = collect_news(
        ticker, ctx, root, news_limit
    )
    write_private_map(root, ticker, ctx)
    residual_status, _ = residual_scan_for_ticker(root, ticker, ctx)
    qa = run_nvidia_qa(
        ticker, ctx, [sec_sample, metrics_sample, news_sample], enable_nvidia_qa, root
    )
    write_json(root / "anonymized" / ctx.company_id / "qa" / "nvidia_review.json", qa)
    return TickerResult(
        ticker,
        ctx.company_id,
        ctx.ticker_id,
        ctx.cik_id,
        bool(ctx.cik),
        metrics_status,
        sec_status,
        news_status,
        residual_status,
        str(qa["status"]),
        {**metrics_artifacts, **sec_artifacts, **news_artifacts},
        [*metric_failures, *sec_failures, *news_failures],
    )


def public_result(result: TickerResult) -> dict[str, Any]:
    return {
        "company_id": result.company_id,
        "ticker_id": result.ticker_id,
        "cik_id": result.cik_id,
        "cik_resolved": result.cik_resolved,
        "metrics_status": result.metrics_status,
        "sec_status": result.sec_status,
        "news_status": result.news_status,
        "residual_status": result.residual_status,
        "nvidia_status": result.nvidia_status,
        "artifacts": result.artifacts,
        "source_failures": [
            {"source": failure.source, "status": failure.status, "detail": failure.detail}
            for failure in result.source_failures
        ],
    }


def write_docs(root: Path, results: Sequence[TickerResult]) -> None:
    write_text(
        root / "README.md",
        "# FENRIX Sanitized Submission\n\n"
        "Public bundle with sanitized summaries, briefs, binned metrics, QA, inventory, and checksums. "
        "Source files and private maps are excluded.\n",
    )
    write_run_summary(root, results)


def write_run_summary(root: Path, results: Sequence[TickerResult]) -> None:
    write_json(
        root / "run_summary.json",
        {
            "schema_version": "1.0",
            "generated_at": now_utc(),
            "companies": [public_result(result) for result in results],
        },
    )


def iter_public_files(root: Path) -> Iterable[Path]:
    for name in sorted(PUBLIC_TOP_LEVEL_FILES):
        path = root / name
        if path.is_file():
            yield path
    for name in sorted(PUBLIC_TOP_LEVEL_DIRS):
        base = root / name
        if base.exists():
            yield from (
                path
                for path in sorted(base.rglob("*"))
                if path.is_file() and not should_exclude_from_zip(path.relative_to(root).as_posix())
            )


def should_exclude_from_zip(rel: str) -> bool:
    return (
        rel.startswith(("originals/", "private_maps/", "smoke_excerpts/", "exports/"))
        or "__pycache__" in rel
        or rel.endswith((".pyc", ".pyo", ".DS_Store"))
    )


def write_artifact_inventory(root: Path, results: Sequence[TickerResult]) -> None:
    rows = [
        {
            "public_path_id": path.relative_to(root).as_posix(),
            "size_bytes": str(path.stat().st_size),
            "sha256": sha256_bytes(path.read_bytes()),
        }
        for path in iter_public_files(root)
    ]
    with (root / "artifact_inventory.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["public_path_id", "size_bytes", "sha256"])
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(root: Path, results: Sequence[TickerResult]) -> None:
    lines = [
        f"{sha256_bytes(path.read_bytes())}  {path.relative_to(root).as_posix()}\n"
        for path in iter_public_files(root)
        if path.name != "checksums.sha256"
    ]
    write_text(root / "checksums.sha256", "".join(lines))


def package_zip(root: Path) -> Path:
    zip_path = root / "exports" / "anonymized_bundle.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in iter_public_files(root):
            zf.write(path, path.relative_to(root).as_posix())
    return zip_path


def validate_zip(zip_path: Path) -> ZipValidationResult:
    name_hits: list[str] = []
    text_hits: list[str] = []
    key_hits: list[str] = []
    path_hits: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for name in names:
            if any(value in name for value in FORBIDDEN_ZIP_SUBSTRINGS):
                name_hits.append(name)
            if not name.lower().endswith((".md", ".json", ".csv", ".txt", ".sha256")):
                continue
            text = zf.read(name).decode("utf-8", errors="replace")
            if forbidden_public_text(text):
                text_hits.append(name)
            if re.search(r"\bnvapi-[A-Za-z0-9_-]{8,}\b|NVIDIA_API_KEY", text):
                key_hits.append(name)
            if re.search(r"(/Users/|/content/)", text):
                path_hits.append(name)
    ok = not (name_hits or text_hits or key_hits or path_hits)
    return ZipValidationResult(
        ok, len(names), zip_path.stat().st_size, name_hits, text_hits, key_hits, path_hits
    )


def forbidden_public_text(text: str) -> bool:
    return (
        any(value in text for value in FORBIDDEN_ZIP_SUBSTRINGS)
        or TAXONOMY_RE.search(text) is not None
        or URL_RE.search(text) is not None
        or PHONE_RE.search(text) is not None
        or EIN_RE.search(text) is not None
        or SEC_FILE_RE.search(text) is not None
        or ACCESSION_RE.search(text) is not None
        or ADDRESS_RE.search(text) is not None
        or DATE_RE.search(text) is not None
        or HEADER_RE.search(text) is not None
        or ROLE_RE.search(text) is not None
        or TARGET_TICKER_RE.search(text) is not None
        or VOTE_RE.search(text) is not None
    )


def parse_tickers(raw: str) -> list[str]:
    return [ticker.strip().upper() for ticker in raw.split(",") if ticker.strip()]


def build_submission(
    tickers: Sequence[str],
    output_root: Path,
    years: int,
    news_limit: int,
    enable_nvidia_qa: str,
) -> tuple[Path, list[TickerResult], ZipValidationResult]:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "qa").mkdir(exist_ok=True)
    results = [
        build_one_ticker(output_root, ticker, idx, years, news_limit, enable_nvidia_qa)
        for idx, ticker in enumerate(tickers, 1)
    ]
    write_docs(output_root, results)
    write_artifact_inventory(output_root, results)
    write_checksums(output_root, results)
    zip_path = package_zip(output_root)
    validation = validate_zip(zip_path)
    write_json(output_root / "qa" / "zip_validation.json", dataclasses.asdict(validation))
    return zip_path, results, validation


def default_output_root() -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.home() / "Desktop" / f"FENRIX_8_COMPANY_ANON_SUBMISSION_REPAIR_{timestamp}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a sanitized multi-company public submission bundle."
    )
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--output-root", default=str(default_output_root()))
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--news-limit", type=int, default=5)
    parser.add_argument("--enable-nvidia-qa", choices=["auto", "yes", "no"], default="auto")
    args = parser.parse_args(argv)
    zip_path, results, validation = build_submission(
        parse_tickers(args.tickers),
        Path(args.output_root).expanduser(),
        args.years,
        args.news_limit,
        args.enable_nvidia_qa,
    )
    print(f"ZIP={zip_path}")
    print(f"ZIP_VALID={validation.ok}")
    for result in results:
        print(
            f"{result.ticker}: sec={result.sec_status} news={result.news_status} metrics={result.metrics_status}"
        )
    return 0 if zip_path.exists() and validation.ok else 1
