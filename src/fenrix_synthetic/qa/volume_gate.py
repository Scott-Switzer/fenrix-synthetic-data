"""V3.2 Volume Gate.

Ensures professor bundles have enough content volume for academic use.
A bundle that passes privacy but is too small is not useful for teaching.

Required checks:
- Exactly 8 companies
- Min total ZIP entries >= 1000 (if source coverage supports it)
- Min SEC/narrative docs per company >= 100 where available
- Min historical year span >= 7 per company where source coverage permits
- Target end year <= 2025
- No future public years
- Source coverage audit exists
- If volume is lower than target, source-backed waiver required
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Verdict constants ─────────────────────────────────────────────────


VOLUME_PASS = "PASS"
VOLUME_PASS_WITH_WAIVER = "PASS_WITH_WAIVER"
VOLUME_WARN = "WARN"
VOLUME_FAIL = "FAIL"


# ── Gate thresholds ───────────────────────────────────────────────────


@dataclass
class VolumeThresholds:
    """Configurable volume gate thresholds."""

    min_companies: int = 8
    min_total_zip_entries: int = 1000
    min_sec_docs_per_company: int = 100
    min_year_span_per_company: int = 7
    target_end_year: int = 2025
    historical_coverage_required: bool = True
    min_market_rows_per_company: int = 1000


DEFAULT_THRESHOLDS = VolumeThresholds()


# ── Result dataclasses ─────────────────────────────────────────────────


@dataclass
class VolumeCheck:
    """A single volume gate check result."""

    check_id: str
    description: str
    passed: bool
    target: str
    actual: str
    blocking: bool = True


@dataclass
class PerCompanyVolume:
    """Per-company volume statistics."""

    company_id: str
    total_files: int = 0
    sec_docs: int = 0
    financial_files: int = 0
    market_files: int = 0
    news_files: int = 0
    profile_files: int = 0
    earliest_year: int | None = None
    latest_year: int | None = None
    year_span: int = 0
    has_future_years: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class VolumeGateResult:
    """Complete volume gate evaluation."""

    passed: bool
    verdict: str  # PASS, PASS_WITH_WAIVER, WARN, FAIL
    checks: list[VolumeCheck]
    per_company: list[PerCompanyVolume]
    total_zip_entries: int = 0
    total_zip_bytes: int = 0
    company_count: int = 0
    min_year_span: int = 0
    min_sec_docs: int = 0
    waiver_required: bool = False
    waiver_reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "passed": self.passed,
            "company_count": self.company_count,
            "total_zip_entries": self.total_zip_entries,
            "total_zip_bytes": self.total_zip_bytes,
            "min_year_span": self.min_year_span,
            "min_sec_docs_per_company": self.min_sec_docs,
            "waiver_required": self.waiver_required,
            "waiver_reason": self.waiver_reason,
            "checks": [
                {
                    "check_id": c.check_id,
                    "passed": c.passed,
                    "target": c.target,
                    "actual": c.actual,
                    "detail": c.description,
                }
                for c in self.checks
            ],
            "per_company": [
                {
                    "company_id": pc.company_id,
                    "total_files": pc.total_files,
                    "sec_docs": pc.sec_docs,
                    "financial_files": pc.financial_files,
                    "market_files": pc.market_files,
                    "earliest_year": pc.earliest_year,
                    "latest_year": pc.latest_year,
                    "year_span": pc.year_span,
                    "has_future_years": pc.has_future_years,
                    "warnings": pc.warnings,
                }
                for pc in self.per_company
            ],
            "warnings": self.warnings,
        }


# ── Gate evaluation ────────────────────────────────────────────────────


def evaluate_volume_gate(
    bundle_root: Path,
    *,
    thresholds: VolumeThresholds | None = None,
    waiver_reason: str = "",
) -> VolumeGateResult:
    """Evaluate the V3.2 volume gate for a bundle directory.

    Args:
        bundle_root: Root directory of the professor bundle.
        thresholds: Custom thresholds (uses defaults if None).
        waiver_reason: Documented reason if volume is below targets
            due to limited source coverage (NOT due to pipeline issues).

    Returns:
        VolumeGateResult with pass/fail and per-company details.
    """
    t = thresholds or DEFAULT_THRESHOLDS
    checks: list[VolumeCheck] = []
    warnings: list[str] = []

    public_dir = bundle_root / "public" / "anonymized"
    if not public_dir.exists():
        return VolumeGateResult(
            passed=False,
            verdict=VOLUME_FAIL,
            checks=[VolumeCheck(
                check_id="public_dir_exists",
                description="public/anonymized/ directory not found",
                passed=False,
                target="exists",
                actual="missing",
            )],
            per_company=[],
        )

    company_dirs = sorted(d.name for d in public_dir.iterdir() if d.is_dir())
    company_count = len(company_dirs)

    # ── Per-company volume analysis ─────────────────────────────────
    per_company: list[PerCompanyVolume] = []
    for cd in company_dirs:
        pc = _analyze_company_volume(public_dir / cd, t.target_end_year)
        per_company.append(pc)

    # ── Check 1: Company count ──────────────────────────────────────
    check1 = VolumeCheck(
        check_id="company_count",
        description=f"Bundle has {company_count} companies (target {t.min_companies})",
        passed=company_count == t.min_companies,
        target=str(t.min_companies),
        actual=str(company_count),
    )
    checks.append(check1)

    # ── Check 2: No future years ────────────────────────────────────
    companies_with_future = [pc.company_id for pc in per_company if pc.has_future_years]
    check2 = VolumeCheck(
        check_id="no_future_years",
        description=(
            "No company has future years (>2025)"
            if not companies_with_future
            else f"Future years found in: {companies_with_future}"
        ),
        passed=len(companies_with_future) == 0,
        target=f"all years <= {t.target_end_year}",
        actual=f"{len(companies_with_future)} companies with future years"
        if companies_with_future
        else f"all years <= {t.target_end_year}",
    )
    checks.append(check2)

    # ── Check 3: Year span per company ──────────────────────────────
    if per_company:
        min_span = min(pc.year_span for pc in per_company)
        companies_below_span = [
            pc.company_id for pc in per_company
            if pc.year_span < t.min_year_span_per_company and pc.year_span > 0
        ]
    else:
        min_span = 0
        companies_below_span = []

    year_span_ok = min_span >= t.min_year_span_per_company
    check3 = VolumeCheck(
        check_id="min_year_span",
        description=f"Minimum year span: {min_span} (target >= {t.min_year_span_per_company})",
        passed=year_span_ok or bool(waiver_reason),
        target=f">= {t.min_year_span_per_company}",
        actual=str(min_span),
        blocking=not bool(waiver_reason),
    )
    checks.append(check3)
    if companies_below_span:
        warnings.append(f"Companies below year span target: {companies_below_span}")

    # ── Check 4: SEC docs per company ───────────────────────────────
    if per_company:
        min_sec = min(pc.sec_docs for pc in per_company)
    else:
        min_sec = 0

    sec_docs_ok = min_sec >= t.min_sec_docs_per_company
    check4 = VolumeCheck(
        check_id="min_sec_docs",
        description=f"Minimum SEC/docs per company: {min_sec} (target >= {t.min_sec_docs_per_company})",
        passed=sec_docs_ok or bool(waiver_reason),
        target=f">= {t.min_sec_docs_per_company}",
        actual=str(min_sec),
        blocking=not bool(waiver_reason),
    )
    checks.append(check4)

    # ── Check 5: Source coverage audit exists ────────────────────────
    coverage_exists = False
    for cov_name in ["source_coverage_by_company.csv", "filing_inventory_by_company.csv"]:
        cov_path = bundle_root / "coverage" / cov_name
        if cov_path.exists():
            coverage_exists = True
            break
    for cov_name in ["source_coverage_by_company.csv", "filing_inventory_by_company.csv"]:
        cov_path = bundle_root / cov_name
        if cov_path.exists():
            coverage_exists = True
            break

    check5 = VolumeCheck(
        check_id="coverage_audit_exists",
        description="Source coverage audit artifacts present",
        passed=coverage_exists or bool(waiver_reason),
        target="coverage/ files exist",
        actual="present" if coverage_exists else "missing",
        blocking=False,
    )
    checks.append(check5)

    # ── Compute total ZIP entries ────────────────────────────────────
    total_entries = 0
    total_bytes = 0
    import zipfile as _zipfile
    zip_path = bundle_root / "exports" / "anonymized_bundle.zip"
    if zip_path.exists():
        try:
            with _zipfile.ZipFile(zip_path, "r") as zf:
                total_entries = len(zf.namelist())
                for name in zf.namelist():
                    try:
                        info = zf.getinfo(name)
                        total_bytes += info.file_size
                    except KeyError:
                        pass
        except (_zipfile.BadZipFile, OSError):
            pass
    else:
        for pc in per_company:
            total_entries += pc.total_files
        total_entries += 10  # top-level docs, qa files

    # ── Check 6: Total ZIP entries ──────────────────────────────────
    entries_ok = total_entries >= t.min_total_zip_entries
    check6 = VolumeCheck(
        check_id="min_zip_entries",
        description=f"Total ZIP entries: {total_entries} (target >= {t.min_total_zip_entries})",
        passed=entries_ok or bool(waiver_reason),
        target=f">= {t.min_total_zip_entries}",
        actual=str(total_entries),
        blocking=not bool(waiver_reason),
    )
    checks.append(check6)

    # ── Check 7: Market rows per company ────────────────────────────
    if per_company:
        min_market_rows = min(
            _count_market_rows(public_dir / pc.company_id)
            for pc in per_company
        )
    else:
        min_market_rows = 0

    market_ok = min_market_rows >= t.min_market_rows_per_company
    check7 = VolumeCheck(
        check_id="min_market_rows",
        description=f"Minimum market rows: {min_market_rows} (target >= {t.min_market_rows_per_company})",
        passed=market_ok or bool(waiver_reason),
        target=f">= {t.min_market_rows_per_company}",
        actual=str(min_market_rows),
        blocking=not bool(waiver_reason),
    )
    checks.append(check7)

    # ── Check 8: Historical coverage vs emitted volume ──────────────
    check8 = VolumeCheck(
        check_id="historical_coverage_documented",
        description="Historical coverage through 2025 is documented",
        passed=coverage_exists or bool(waiver_reason),
        target="RUN_SUMMARY.md or coverage/ files with year spans",
        actual="documented" if coverage_exists else "not documented",
        blocking=False,
    )
    checks.append(check8)

    # ── Decision ────────────────────────────────────────────────────
    blocking_failed = [c for c in checks if c.blocking and not c.passed]
    non_blocking_failed = [c for c in checks if not c.blocking and not c.passed]

    for c in non_blocking_failed:
        warnings.append(f"{c.check_id}: {c.description}")

    if waiver_reason and any(not c.passed for c in checks if c.check_id in {"min_sec_docs", "min_zip_entries", "min_year_span", "min_market_rows"}):
        # Some volume targets missed but source-backed waiver provided
        verdict = VOLUME_PASS_WITH_WAIVER
        passed = True
        waiver_used = True
    elif len(blocking_failed) == 0:
        verdict = VOLUME_PASS
        passed = True
        waiver_used = False
    elif any("future_years" in c.check_id for c in blocking_failed):
        verdict = VOLUME_FAIL
        passed = False
        waiver_used = False
    else:
        verdict = VOLUME_FAIL
        passed = False
        waiver_used = False

    return VolumeGateResult(
        passed=passed,
        verdict=verdict,
        checks=checks,
        per_company=per_company,
        total_zip_entries=total_entries,
        total_zip_bytes=total_bytes,
        company_count=company_count,
        min_year_span=min_span,
        min_sec_docs=min_sec,
        waiver_required=waiver_used and not waiver_reason,
        waiver_reason=waiver_reason if waiver_reason else "",
        warnings=warnings,
    )


def _analyze_company_volume(company_dir: Path, target_end_year: int) -> PerCompanyVolume:
    """Analyze volume for a single company directory."""
    pc = PerCompanyVolume(company_id=company_dir.name)
    file_count = 0

    # Count files by category
    for fp in sorted(company_dir.rglob("*")):
        if not fp.is_file():
            continue
        file_count += 1
        rel = str(fp.relative_to(company_dir))

        if "sec/" in rel:
            pc.sec_docs += 1
        elif "financials/" in rel:
            pc.financial_files += 1
        elif "market/" in rel:
            pc.market_files += 1
        elif "news/" in rel:
            pc.news_files += 1
        elif "profile/" in rel:
            pc.profile_files += 1

    pc.total_files = file_count

    # Analyze years from financial metrics
    metrics_path = company_dir / "financials" / "transformed_metrics.csv"
    if metrics_path.exists():
        try:
            years: set[int] = set()
            with open(metrics_path) as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if row and len(row) >= 1:
                        try:
                            y = int(row[0])
                            years.add(y)
                        except ValueError:
                            pass
            if years:
                pc.earliest_year = min(years)
                pc.latest_year = max(years)
                pc.year_span = pc.latest_year - pc.earliest_year + 1
                if pc.latest_year > target_end_year:
                    pc.has_future_years = True
                    pc.warnings.append(
                        f"Future years detected: max year {pc.latest_year} > {target_end_year}"
                    )
        except (OSError, csv.Error):
            pc.warnings.append("transformed_metrics.csv unreadable")

    return pc


def _count_market_rows(company_dir: Path) -> int:
    """Count rows in market/price_series.csv."""
    price_path = company_dir / "market" / "price_series.csv"
    if not price_path.exists():
        return 0
    try:
        return max(0, len(price_path.read_text().splitlines()) - 1)
    except OSError:
        return 0


def write_volume_gate_report(result: VolumeGateResult, qa_dir: Path) -> Path:
    """Write the volume gate report to qa/volume_gate.json.

    Args:
        result: The volume gate result.
        qa_dir: Path to the qa/ directory.

    Returns:
        Path to the written report file.
    """
    qa_dir.mkdir(parents=True, exist_ok=True)
    report_path = qa_dir / "volume_gate.json"
    report_path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report_path
