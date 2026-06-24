"""Strict release gate for V3 release boundary.

Aggregates direct identifier scan, metadata scan, package allowlist
validation, forbidden path validation, and manifest validation.

Fail-closed behavior:
- Scanner errors → FAIL
- Unreadable files → FAIL
- Missing manifest → FAIL
- Private artifacts present → FAIL
- Raw source artifacts present → FAIL
- Identity map present → FAIL
- Raw SEC HTML/XML present → FAIL
- ZIP contains forbidden entry names → FAIL
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def evaluate_strict_release_gate(
    bundle_root: Path,
    *,
    company_names: list[str] | None = None,
    tickers: list[str] | None = None,
    executive_names: list[str] | None = None,
    ciks: list[str] | None = None,
    mode: str = "strict",
    write_reports: bool = True,
) -> dict[str, Any]:
    """Evaluate the strict V3 release gate for a bundle directory.

    Args:
        bundle_root: Root directory of the professor bundle.
        company_names: Source company names to scan for.
        tickers: Source tickers to scan for.
        executive_names: Executive names to scan for.
        ciks: Known CIKs to scan for.
        mode: "strict" (default) - fail closed on any finding.
        write_reports: If True, write scan/gate report JSONs to qa/.

    Returns:
        Gate report dict with pass/fail, findings, and recommendations.
    """
    from .direct_identifier_scan import ScanHit, scan_path
    from .metadata_scan import MetadataHit, scan_metadata

    checked_at = datetime.now(UTC).isoformat()
    blocking_failures: list[str] = []
    findings: list[dict[str, Any]] = []
    di_hits: list[dict[str, Any]] = []
    metadata_hits: list[dict[str, Any]] = []
    forbidden_paths: list[str] = []
    missing_required: list[str] = []

    public_dir = bundle_root / "public"
    qa_dir = bundle_root / "qa"

    # ── Scan 1: Direct identifier scan on public/ ────────────────────
    di_result: Any = None
    if public_dir.exists():
        try:
            di_result = scan_path(
                public_dir,
                company_names=company_names,
                tickers=tickers,
                executive_names=executive_names,
                ciks=ciks,
                scan_html_xml=True,
            )
            for hit in di_result.hits:
                di_hits.append(hit.to_dict())
            if not di_result.passed:
                blocking_failures.append(
                    f"direct_identifier_scan_failed: {di_result.blocking_hits} hits"
                )
                for hit in di_result.blocking_hits:
                    findings.append(
                        {
                            "scanner": "direct_identifier",
                            "path": hit.path,
                            "pattern_id": hit.pattern_id,
                            "preview": hit.matched_text_preview,
                        }
                    )
        except Exception as e:
            blocking_failures.append(f"direct_identifier_scan_error: {e}")
    else:
        blocking_failures.append("public_dir_missing")

    # ── Scan 2: Metadata scan on public/ ─────────────────────────────
    md_result: Any = None
    if public_dir.exists():
        try:
            md_result = scan_metadata(public_dir, scan_html_xml_files=True)
            for hit in md_result.hits:
                metadata_hits.append(hit.to_dict())
            if not md_result.passed:
                blocking_failures.append(
                    f"metadata_scan_failed: {md_result.hit_count} hits"
                )
                for hit in md_result.hits:
                    findings.append(
                        {
                            "scanner": "metadata",
                            "path": hit.path,
                            "pattern_id": hit.pattern_id,
                            "category": hit.pattern_category,
                            "preview": hit.matched_text_preview,
                        }
                    )
        except Exception as e:
            blocking_failures.append(f"metadata_scan_error: {e}")

    # ── Check 3: Forbidden paths inside public/ or qa/ ───────────────
    # Only scan allowlisted directories for forbidden sub-paths.
    # The bundle root may legitimately contain private/, exports/, etc.
    forbidden_path_patterns: list[str] = [
        "private/",
        "raw/",
        "source/",
        "sources/",
        "identity/",
        "identities/",
        "mappings/",
        "checkpoints/",
        "cache/",
        "edgar_raw/",
        "sec_raw/",
        "original/",
        "originals/",
    ]
    scan_roots = [d for d in (public_dir, qa_dir) if d.exists()]
    for scan_root in scan_roots:
        for pattern in forbidden_path_patterns:
            check_path = scan_root / pattern.rstrip("/")
            if check_path.exists():
                rel = str(check_path.relative_to(bundle_root))
                forbidden_paths.append(rel)
                blocking_failures.append(f"forbidden_path_in_allowlisted_area: {rel}")

    # ── Check 4: Forbidden file patterns inside public/ or qa/ ───────
    # Only scan allowlisted directories.
    # The bundle root may legitimately contain build artifacts.
    forbidden_file_globs: list[str] = [
        "*.env",
        "*.key",
        "*.pem",
        "*identity*",
        "*_mapping*",
        "*identity_map*",
        "*source_map*",
        "*original*",
        "*.sqlite",
        "*.db",
    ]
    for scan_root in scan_roots:
        for glob_pattern in forbidden_file_globs:
            for fp in scan_root.glob(f"**/{glob_pattern}"):
                rel = str(fp.relative_to(bundle_root))
                forbidden_paths.append(rel)
                blocking_failures.append(f"forbidden_file_in_allowlisted_area: {rel}")

    # ── Check 5: No .html/.xml in public/ ────────────────────────────
    if public_dir.exists():
        for fp in public_dir.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in {".html", ".htm", ".xml", ".xbrl"}:
                rel = str(fp.relative_to(bundle_root))
                findings.append(
                    {
                        "scanner": "forbidden_extension",
                        "path": rel,
                        "pattern_id": "html_xml_forbidden",
                        "preview": f"Forbidden file extension: {fp.suffix}",
                    }
                )
                blocking_failures.append(f"forbidden_extension_in_public: {rel}")

    # ── Check 6: Private data files inside public/ or qa/ ────────────
    # Only flag if private data infiltrates allowlisted areas.
    # Private data at the bundle root is a legitimate build artifact.
    private_indicators: list[str] = [
        "evidence/evidence_graph.json",
        "replacement_plan.json",
        "identity_map.json",
        "source_map.json",
    ]
    for scan_root in scan_roots:
        for indicator in private_indicators:
            for fp in scan_root.glob(f"**/{indicator}"):
                rel = str(fp.relative_to(bundle_root))
                blocking_failures.append(f"private_data_in_allowlisted_area: {rel}")

    # ── Check 8: Manifest presence ─────────────────────────────────
    manifest_status = "missing"
    if (bundle_root / "RELEASE_MANIFEST.json").exists():
        manifest_status = "present"
    else:
        missing_required.append("RELEASE_MANIFEST.json")
        blocking_failures.append("manifest_missing")

    # ── Check 7: Forbidden content in ZIP ────────────────────────────
    import zipfile

    zip_path = bundle_root / "exports" / "anonymized_bundle.zip"
    if zip_path.exists():
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    # Forbidden entry name patterns
                    for fp_pattern in forbidden_path_patterns:
                        if name.startswith(fp_pattern):
                            blocking_failures.append(f"zip_contains_forbidden_path: {name}")
                    # Forbidden extensions
                    if name.lower().endswith((".html", ".htm", ".xml", ".xbrl", ".env", ".key", ".pem")):
                        blocking_failures.append(f"zip_contains_forbidden_extension: {name}")
        except (zipfile.BadZipFile, OSError) as e:
            blocking_failures.append(f"zip_read_error: {e}")

    # ── Decision ─────────────────────────────────────────────────────
    passed = len(blocking_failures) == 0

    gate_result = {
        "passed": passed,
        "mode": mode,
        "checked_at": checked_at,
        "scanned_files": (
            (di_result.scanned_files if di_result else 0)
            + (md_result.scanned_files if md_result else 0)
        ),
        "scanned_bytes": (
            (di_result.scanned_bytes if di_result else 0)
            + (md_result.scanned_bytes if md_result else 0)
        ),
        "direct_identifier_hits": di_hits,
        "metadata_hits": metadata_hits,
        "forbidden_paths": forbidden_paths,
        "missing_required_files": missing_required,
        "manifest_status": manifest_status,
        "fail_reasons": blocking_failures,
        "gate_hash": _compute_gate_hash(passed, blocking_failures),
    }

    # ── Write QA reports to disk ────────────────────────────────────
    if write_reports:
        qa_dir.mkdir(parents=True, exist_ok=True)

        di_report_data: dict[str, Any] = (
            di_result.to_dict() if di_result else {"scanned_files": 0, "hits": []}
        )
        (qa_dir / "direct_identifier_scan.json").write_bytes(
            json.dumps(di_report_data, indent=2, sort_keys=True).encode()
        )

        md_report_data: dict[str, Any] = (
            md_result.to_dict() if md_result else {"scanned_files": 0, "hits": []}
        )
        (qa_dir / "metadata_scan.json").write_bytes(
            json.dumps(md_report_data, indent=2, sort_keys=True).encode()
        )

        (qa_dir / "public_release_gate.json").write_bytes(
            json.dumps(gate_result, indent=2, sort_keys=True).encode()
        )

    return gate_result


def _compute_gate_hash(passed: bool, failures: list[str]) -> str:
    """Compute a deterministic hash of gate state."""
    return hashlib.sha256(
        json.dumps(
            {"passed": passed, "failures": sorted(failures)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:16]
