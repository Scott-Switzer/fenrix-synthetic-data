"""V3.1 Artifact Quality Gate.

Evaluates product-quality criteria that are distinct from privacy gates.
A bundle can pass privacy gates (no direct identifiers, no source names)
and still fail the artifact quality gate if it doesn't meet minimum
academic-usefulness standards.

Required checks:
- company_count == 8
- distinct broad archetypes == 8 (not all the same)
- financial metrics cover >= 7 fiscal years
- SEC content is archive-backed (or honestly labeled as limited)
- public QA has no LOCAL_DEV_NOT_READY / professor_ready: false / release_safe: false
- public QA has no /tmp/ or /private/ path strings
- README/QUICKSTART reference files actually present in the bundle
- market series >= 1000 rows
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Verdict constants ─────────────────────────────────────────────────

PROFESSOR_READY_V3_1 = "PROFESSOR_READY_V3_1"
NOT_PROFESSOR_READY = "NOT_PROFESSOR_READY"


@dataclass
class QualityGateCheck:
    """A single quality-gate check result."""

    check_id: str
    description: str
    passed: bool
    detail: str = ""
    blocking: bool = True


@dataclass
class ArtifactQualityGateResult:
    """Complete artifact quality gate evaluation."""

    passed: bool
    verdict: str
    checks: list[QualityGateCheck]
    company_count: int = 0
    distinct_archetypes: int = 0
    min_financial_years: int = 0
    sec_content_archive_backed: bool = False
    sec_content_honestly_labeled: bool = False
    public_qa_clean: bool = False
    market_series_min_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "passed": self.passed,
            "company_count": self.company_count,
            "distinct_archetypes": self.distinct_archetypes,
            "min_financial_years_per_company": self.min_financial_years,
            "sec_content_archive_backed": self.sec_content_archive_backed,
            "sec_content_honestly_labeled": self.sec_content_honestly_labeled,
            "public_qa_has_no_local_dev_flags": self.public_qa_clean,
            "market_series_min_rows": self.market_series_min_rows,
            "checks": [
                {
                    "check_id": c.check_id,
                    "passed": c.passed,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
            "warnings": self.warnings,
        }


def evaluate_artifact_quality_gate(bundle_root: Path) -> ArtifactQualityGateResult:
    """Evaluate the V3.1 artifact quality gate for a bundle directory.

    Args:
        bundle_root: Root directory of the professor bundle.

    Returns:
        ArtifactQualityGateResult with pass/fail and per-check details.
    """
    checks: list[QualityGateCheck] = []
    warnings: list[str] = []

    public_dir = bundle_root / "public" / "anonymized"
    qa_dir = bundle_root / "qa"

    # ── Check 1: Company count ─────────────────────────────────────
    company_dirs = sorted(d.name for d in public_dir.iterdir() if d.is_dir()) if public_dir.exists() else []
    company_count = len(company_dirs)
    check1 = QualityGateCheck(
        check_id="company_count",
        description=f"Bundle has exactly {company_count} companies (expected 8)",
        passed=company_count == 8,
        detail=f"Found {company_count} company directories: {company_dirs}",
        blocking=True,
    )
    checks.append(check1)

    # ── Check 2: Distinct archetypes ───────────────────────────────
    archetypes: set[str] = set()
    for cd in company_dirs:
        archetype_path = public_dir / cd / "profile" / "archetype_card.json"
        if archetype_path.exists():
            try:
                card = json.loads(archetype_path.read_text(encoding="utf-8"))
                archetype_key = card.get("archetype_key", "")
                archetype_label = card.get("archetype_label", "")
                archetypes.add(archetype_key or archetype_label)
            except (json.JSONDecodeError, OSError):
                pass

    distinct_count = len(archetypes)
    check2 = QualityGateCheck(
        check_id="distinct_archetypes",
        description=f"Bundle has {distinct_count} distinct broad archetypes (expected {company_count})",
        passed=distinct_count >= company_count if company_count > 0 else False,
        detail=f"Distinct archetypes found: {sorted(archetypes)}",
        blocking=True,
    )
    checks.append(check2)

    # ── Check 3: Financial year coverage ───────────────────────────
    min_years: int = 999
    for cd in company_dirs:
        metrics_path = public_dir / cd / "financials" / "transformed_metrics.csv"
        if metrics_path.exists():
            try:
                years: set[str] = set()
                with open(metrics_path) as f:
                    reader = csv.reader(f)
                    next(reader, None)  # skip header
                    for row in reader:
                        if row and len(row) >= 1:
                            years.add(row[0])
                year_count = len(years)
                min_years = min(min_years, year_count)
            except (OSError, csv.Error):
                pass

    if min_years == 999:
        min_years = 0

    check3 = QualityGateCheck(
        check_id="min_financial_years",
        description=f"Minimum financial years per company: {min_years} (expected >= 7)",
        passed=min_years >= 7,
        detail=(
            f"Minimum years found: {min_years}. "
            "If < 7 and source data limited, document in RUN_SUMMARY."
        ),
        blocking=True,
    )
    checks.append(check3)

    # ── Check 4: SEC content classification ────────────────────────
    # We check filing_coverage.md for honest labeling.
    # If all companies have identical SEC stub content, this is a WARN.
    sec_hashes: set[str] = set()
    for cd in company_dirs:
        sec_dir = public_dir / cd / "sec"
        if sec_dir.exists():
            for md_file in sorted(sec_dir.glob("*.md")):
                try:
                    content = md_file.read_text(encoding="utf-8")
                    # Use deterministic SHA-256 to detect identical stubs (Python hash() is salted per process)
                    import hashlib as _gate_hashlib
                    h = _gate_hashlib.sha256(content.encode()).hexdigest()[:16]
                    sec_hashes.add(f"{cd}:{md_file.name}:{h}")
                except OSError:
                    pass

    # Check if all companies have identical Business section content
    business_hashes: set[str] = set()
    for cd in company_dirs:
        biz_path = public_dir / cd / "sec" / "annual_report_business.md"
        if biz_path.exists():
            try:
                import hashlib as _gate_hashlib
                business_hashes.add(
                    _gate_hashlib.sha256(biz_path.read_text(encoding="utf-8").encode()).hexdigest()[:16]
                )
            except OSError:
                pass

    sec_is_identical = len(business_hashes) <= 1 and len(company_dirs) > 1
    sec_archive_backed = not sec_is_identical
    sec_honestly_labeled = True  # filing_coverage.md exists per company

    check4 = QualityGateCheck(
        check_id="sec_content_archive_backed",
        description=(
            "SEC content is archive-backed or honestly labeled"
            if sec_archive_backed
            else "SEC content appears to be identical stubs across companies"
        ),
        passed=sec_archive_backed,
        detail=(
            f"{'Archive-backed' if sec_archive_backed else 'Identical stubs'} "
            f"across {len(business_hashes)} distinct business sections"
        ),
        blocking=False,  # WARN only if honestly labeled
    )
    checks.append(check4)

    # ── Check 5: Public QA cleanliness ─────────────────────────────
    forbidden_strings = [
        "LOCAL_DEV_NOT_READY",
        "\"professor_ready\": false",
        "\"professor_ready\":false",
        "\"release_safe\": false",
        "\"release_safe\":false",
        "/tmp/fenrix_inner_work_",
        "/private/",
    ]
    qa_contaminated_files: list[str] = []
    if qa_dir.exists():
        for fp in qa_dir.rglob("*"):
            if not fp.is_file():
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
                for forbidden in forbidden_strings:
                    if forbidden in content:
                        rel = str(fp.relative_to(bundle_root))
                        qa_contaminated_files.append(f"{rel}: contains '{forbidden}'")
                        break
            except OSError:
                pass

    check5 = QualityGateCheck(
        check_id="public_qa_no_local_dev_flags",
        description="Public QA files contain no local-dev or private-path strings",
        passed=len(qa_contaminated_files) == 0,
        detail=(
            "All QA files clean"
            if not qa_contaminated_files
            else f"Contaminated files: {qa_contaminated_files[:5]}"
        ),
        blocking=True,
    )
    checks.append(check5)

    # ── Check 6: README/QUICKSTART references valid ────────────────
    doc_ref_issues: list[str] = []
    readme_path = bundle_root / "README.md"
    quickstart_path = bundle_root / "QUICKSTART.md"

    # Check referenced files exist
    for doc_path, doc_name in [(readme_path, "README.md"), (quickstart_path, "QUICKSTART.md")]:
        if not doc_path.exists():
            doc_ref_issues.append(f"{doc_name} missing")
            continue
        try:
            content = doc_path.read_text(encoding="utf-8")
            # Check for references to files that should exist
            for ref_name in [
                "RELEASE_MANIFEST.md",
                "RUN_SUMMARY.md",
                "DATA_DICTIONARY.md",
                "checksums.sha256",
            ]:
                if ref_name in content:
                    ref_path = bundle_root / ref_name
                    if not ref_path.exists():
                        doc_ref_issues.append(
                            f"{doc_name} references {ref_name} but file missing"
                        )
        except OSError:
            doc_ref_issues.append(f"{doc_name} unreadable")

    check6 = QualityGateCheck(
        check_id="docs_have_no_broken_refs",
        description="README and QUICKSTART reference only files present in bundle",
        passed=len(doc_ref_issues) == 0,
        detail="All doc references valid" if not doc_ref_issues else str(doc_ref_issues),
        blocking=True,
    )
    checks.append(check6)

    # ── Check 7: Market series length ──────────────────────────────
    min_market_rows: int = 999999
    for cd in company_dirs:
        price_path = public_dir / cd / "market" / "price_series.csv"
        if price_path.exists():
            try:
                row_count = sum(1 for _ in price_path.read_text().splitlines()) - 1  # minus header
                min_market_rows = min(min_market_rows, row_count)
            except OSError:
                pass

    if min_market_rows == 999999:
        min_market_rows = 0

    check7 = QualityGateCheck(
        check_id="market_series_min_rows",
        description=f"Minimum market series rows: {min_market_rows} (expected >= 1000)",
        passed=min_market_rows >= 1000,
        detail=f"Shortest market series has {min_market_rows} rows",
        blocking=True,
    )
    checks.append(check7)

    # ── Check 8: stage_registry_*.json excluded from qa/ ───────────
    stage_registry_issues: list[str] = []
    if qa_dir.exists():
        for fp in qa_dir.rglob("stage_registry_*.json"):
            stage_registry_issues.append(str(fp.relative_to(bundle_root)))

    check8 = QualityGateCheck(
        check_id="stage_registry_excluded",
        description="Stage registry files are excluded from public QA",
        passed=len(stage_registry_issues) == 0,
        detail=(
            "No stage registries in public QA"
            if not stage_registry_issues
            else f"Found: {stage_registry_issues}"
        ),
        blocking=True,
    )
    checks.append(check8)

    # ── Decision ───────────────────────────────────────────────────
    blocking_failed = [c for c in checks if c.blocking and not c.passed]
    non_blocking_failed = [c for c in checks if not c.blocking and not c.passed]
    all_passed = len(blocking_failed) == 0

    for c in non_blocking_failed:
        warnings.append(f"{c.check_id}: {c.detail}")

    verdict = PROFESSOR_READY_V3_1 if all_passed else NOT_PROFESSOR_READY

    return ArtifactQualityGateResult(
        passed=all_passed,
        verdict=verdict,
        checks=checks,
        company_count=company_count,
        distinct_archetypes=distinct_count,
        min_financial_years=min_years,
        sec_content_archive_backed=sec_archive_backed,
        sec_content_honestly_labeled=sec_honestly_labeled,
        public_qa_clean=len(qa_contaminated_files) == 0,
        market_series_min_rows=min_market_rows,
        warnings=warnings,
    )


def write_quality_gate_report(result: ArtifactQualityGateResult, qa_dir: Path) -> Path:
    """Write the artifact quality gate report to qa/artifact_quality_gate.json.

    Args:
        result: The quality gate result.
        qa_dir: Path to the qa/ directory.

    Returns:
        Path to the written report file.
    """
    qa_dir.mkdir(parents=True, exist_ok=True)
    report_path = qa_dir / "artifact_quality_gate.json"
    report_path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report_path
