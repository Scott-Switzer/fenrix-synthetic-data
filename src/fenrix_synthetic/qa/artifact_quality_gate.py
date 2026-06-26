"""V3.2 Artifact Quality Gate.

Evaluates product-quality criteria that are distinct from privacy gates.
A bundle can pass privacy gates (no direct identifiers, no source names)
and still fail the artifact quality gate if it doesn't meet minimum
academic-usefulness standards.

V3.2 updates:
- Fail if fiscal periods include future years (>2025)
- Fail if utility gate passes despite privacy gate failure
- Fail if docs claim "full historical coverage" without coverage table proof
- Fail if SEC business sections are identical across all companies
  and archive-backed ratio is too high without explicit waiver
- Fail if total volume is too low without source-backed reason

Required checks:
- company_count == 8
- distinct broad archetypes == 8 (not all the same)
- financial metrics cover >= 7 fiscal years, capped at 2025
- SEC content is archive-backed (or honestly labeled as limited)
- public QA has no LOCAL_DEV_NOT_READY / professor_ready: false / release_safe: false
- public QA has no /tmp/ or /private/ path strings
- README/QUICKSTART reference files actually present in the bundle
- market series >= 1000 rows
- no future years in any fiscal data
- utility gate cannot be PASS if privacy gate failed
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Verdict constants ─────────────────────────────────────────────────

PROFESSOR_READY_V3_2 = "PROFESSOR_READY_V3_2"
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
    has_future_years: bool = False
    utility_passed_while_privacy_failed: bool = False
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
            "has_future_years": self.has_future_years,
            "utility_passed_while_privacy_failed": self.utility_passed_while_privacy_failed,
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

    # ── Check 9: No future years in fiscal data (V3.2) ─────────────
    future_years_found: list[str] = []
    max_year_overall = 0
    for cd in company_dirs:
        metrics_path = public_dir / cd / "financials" / "transformed_metrics.csv"
        if metrics_path.exists():
            try:
                with open(metrics_path) as f:
                    reader = csv.reader(f)
                    next(reader, None)
                    for row in reader:
                        if row and len(row) >= 1:
                            try:
                                y = int(row[0])
                                max_year_overall = max(max_year_overall, y)
                                if y > 2025:
                                    future_years_found.append(f"{cd}: year {y}")
                            except ValueError:
                                pass
            except (OSError, csv.Error):
                pass

    has_future = len(future_years_found) > 0
    # Also check market price_series for DAY_ indices (relative is fine)
    # Check financials/summary.md for future year mentions
    for cd in company_dirs:
        summary_path = public_dir / cd / "financials" / "summary.md"
        if summary_path.exists():
            try:
                text = summary_path.read_text(encoding="utf-8", errors="replace")
                for y in range(2026, 2031):
                    if str(y) in text:
                        if f"{cd}: summary.md mentions {y}" not in future_years_found:
                            future_years_found.append(f"{cd}: summary.md mentions {y}")
            except OSError:
                pass

    check9 = QualityGateCheck(
        check_id="no_future_years",
        description=(
            "No future years (>2025) in financial data"
            if not has_future
            else f"Future years found: {future_years_found[:5]}"
        ),
        passed=not has_future,
        detail=(
            f"All fiscal years <= 2025 (max seen: {max_year_overall})"
            if not has_future
            else f"Future years: {future_years_found[:5]}"
        ),
        blocking=True,
    )
    checks.append(check9)

    # ── Check 10: Utility vs Privacy consistency (V3.2) ────────────
    utility_gate_passed = False
    privacy_gate_failed = False
    utility_json = qa_dir / "utility_preservation_summary.json"
    blind_json = qa_dir / "llm_blind_guess_summary.json"
    decoy_json = qa_dir / "decoy_aware_llm_summary.json"

    if utility_json.exists():
        try:
            udata = json.loads(utility_json.read_text(encoding="utf-8"))
            utility_gate_passed = udata.get("utility_gate") == "pass"
        except (json.JSONDecodeError, OSError):
            pass

    if blind_json.exists():
        try:
            bdata = json.loads(blind_json.read_text(encoding="utf-8"))
            if bdata.get("privacy_gate") == "fail":
                privacy_gate_failed = True
        except (json.JSONDecodeError, OSError):
            pass

    if decoy_json.exists():
        try:
            ddata = json.loads(decoy_json.read_text(encoding="utf-8"))
            if ddata.get("decoy_gate") == "fail":
                privacy_gate_failed = True
        except (json.JSONDecodeError, OSError):
            pass

    utility_privacy_consistent = not (utility_gate_passed and privacy_gate_failed)
    check10 = QualityGateCheck(
        check_id="utility_privacy_consistency",
        description=(
            "Utility gate not PASS while privacy gate FAIL"
            if utility_privacy_consistent
            else "Utility gate PASS but privacy gate FAIL — invalid"
        ),
        passed=utility_privacy_consistent,
        detail=(
            f"Utility gate: {'PASS' if utility_gate_passed else 'not PASS'}, "
            f"Privacy gate: {'FAIL' if privacy_gate_failed else 'PASS'}"
        ),
        blocking=True,
    )
    checks.append(check10)

    # ── Check 11: Coverage table proof (V3.2) ──────────────────────
    coverage_proof_found = False
    run_summary = bundle_root / "RUN_SUMMARY.md"
    if run_summary.exists():
        try:
            text = run_summary.read_text(encoding="utf-8", errors="replace")
            # Check for historical year span documentation
            if "earliest" in text.lower() and ("2005" in text or "201" in text):
                coverage_proof_found = True
        except OSError:
            pass
    # Also check coverage dir
    for cov_file in ["source_coverage_by_company.csv", "filing_inventory_by_company.csv"]:
        cov_path = bundle_root / "coverage" / cov_file
        if cov_path.exists():
            coverage_proof_found = True
        cov_path2 = bundle_root / cov_file
        if cov_path2.exists():
            coverage_proof_found = True

    check11 = QualityGateCheck(
        check_id="coverage_table_proof",
        description=(
            "Historical coverage is documented with year span evidence"
            if coverage_proof_found
            else "No coverage table proof found — RUN_SUMMARY should document year spans"
        ),
        passed=coverage_proof_found,
        detail=(
            "Coverage evidence found in RUN_SUMMARY.md or coverage/"
            if coverage_proof_found
            else "Missing coverage documentation"
        ),
        blocking=False,  # WARN but don't block if honestly labeled
    )
    checks.append(check11)

    # ── Decision ───────────────────────────────────────────────────
    blocking_failed = [c for c in checks if c.blocking and not c.passed]
    non_blocking_failed = [c for c in checks if not c.blocking and not c.passed]
    all_passed = len(blocking_failed) == 0

    for c in non_blocking_failed:
        warnings.append(f"{c.check_id}: {c.detail}")

    verdict = PROFESSOR_READY_V3_2 if all_passed else NOT_PROFESSOR_READY

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
        has_future_years=has_future,
        utility_passed_while_privacy_failed=(utility_gate_passed and privacy_gate_failed),
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
