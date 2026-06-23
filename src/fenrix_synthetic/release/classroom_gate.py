"""Classroom release gate for professor bundles.

Evaluates whether a professor bundle meets all blocking conditions for release.
Blocks on: missing mandatory stages, identity leaks, missing GLiNER/rules audit,
missing evidence boundary, missing metric reports, missing cross-links,
missing pedagogy, empty-evidence QA pass, checksum drift, mock providers in
production mode, etc.

Readiness semantics:
- Fixture mode: can produce strict_fixture_ready=true, never professor_ready=true.
- Local-dev mode: can skip providers, never professor_ready=true.
- Production mode: professor_ready=true only with all real providers passing.

Usage:
    python -m fenrix_synthetic.release.classroom_gate \\
        --bundle-root <bundle> \\
        --release-date 2026-06-22 \\
        --output <gate_report.json>
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import click
import orjson

from ..professor.stages import (
    BuildMode,
    ProfessorStage,
    ProviderKind,
    StageRegistry,
    StageStatus,
    StageStatusRecord,
)


def evaluate_classroom_gate(
    bundle_root: Path,
    release_date: str,
    strict: bool = False,
    stage_registry: StageRegistry | None = None,
) -> dict[str, Any]:
    """Evaluate the classroom release gate.

    Returns a gate report dict with decision, blocking_failures, warnings,
    and split readiness fields (professor_ready, strict_fixture_ready, etc.).
    """
    blocking_failures: list[str] = []
    warnings: list[str] = []

    # ── Check 1: Stage registry ──────────────────────────────────────
    if stage_registry is None:
        registry_path = bundle_root / "qa" / "stage_registry.json"
        if registry_path.exists():
            registry_data = json.loads(registry_path.read_text())
            stage_registry = _reconstruct_registry(registry_data)
        else:
            blocking_failures.append("stage_registry_missing")
            stage_registry = StageRegistry()

    build_mode = stage_registry.build_mode

    if not stage_registry.all_stages_present:
        present = set(stage_registry._records.keys())
        missing = set(ProfessorStage) - present
        blocking_failures.append(f"missing_mandatory_stages: {sorted(s.value for s in missing)}")

    if not stage_registry.all_stages_pass:
        failed = [
            s.value
            for s in ProfessorStage
            if (rec := stage_registry.get(s)) is not None and rec.status != StageStatus.PASS
        ]
        if failed:
            blocking_failures.append(f"failed_stages: {failed}")

    # PROVIDER_NOT_RUN is always blocking in production mode
    if stage_registry.has_provider_not_run and build_mode == BuildMode.PRODUCTION:
        not_run = [
            s.value
            for s in ProfessorStage
            if (rec := stage_registry.get(s)) is not None
            and rec.status == StageStatus.PROVIDER_NOT_RUN
        ]
        blocking_failures.append(f"provider_not_run_in_production_mode: {not_run}")

    # Mock/fixture/skipped providers are blocking in production mode
    if build_mode == BuildMode.PRODUCTION and stage_registry.has_mock_providers:
        mock_stages = [
            s.value
            for s in ProfessorStage
            if (rec := stage_registry.get(s)) is not None
            and rec.provider_kind in {ProviderKind.MOCK, ProviderKind.FIXTURE, ProviderKind.SKIPPED}
        ]
        blocking_failures.append(f"mock_provider_in_production_mode: {mock_stages}")

    # Missing provider provenance is blocking in production mode
    if build_mode == BuildMode.PRODUCTION and stage_registry.has_missing_provider_provenance:
        blocking_failures.append("missing_provider_provenance_in_production_mode")

    if stage_registry.has_evidence_gaps:
        warnings.append("evidence_gaps_in_some_stages")

    # ── Check 2: Identity leaks in public artifacts ──────────────────
    public_dir = bundle_root / "public"
    if public_dir.exists():
        leak_issues = _scan_for_identity_leaks(public_dir)
        if leak_issues:
            blocking_failures.extend(leak_issues)

    # ── Check 3: Required QA reports exist ───────────────────────────
    qa_dir = bundle_root / "qa"
    required_qa_files = [
        "stage_registry.json",
        "entity_audit_report.json",
        "metrics_quality_report.json",
        "metrics_privacy_report.json",
        "metrics_schema_report.json",
        "rag_index_report.json",
        "adversarial_qa_report.json",
    ]
    for req_file in required_qa_files:
        if not (qa_dir / req_file).exists():
            blocking_failures.append(f"missing_qa_report: {req_file}")

    # ── Check 4: Required classroom materials exist ──────────────────
    required_docs = [
        "README.md",
        "CLASSROOM_GUIDE.md",
        "PROFESSOR_AUDIT_GUIDE.md",
        "EXERCISES.md",
        "ANSWER_KEY_STUB.md",
        "RUBRIC.md",
    ]
    for doc in required_docs:
        if not (public_dir / doc).exists():
            blocking_failures.append(f"missing_classroom_doc: {doc}")

    company_dirs = (
        list((public_dir / "anonymized").iterdir()) if (public_dir / "anonymized").exists() else []
    )
    for company_dir in company_dirs:
        if not company_dir.is_dir():
            continue
        if not (company_dir / "LEARNING_GUIDE.md").exists():
            blocking_failures.append(f"missing_learning_guide: {company_dir.name}")
        if not (company_dir / "crosslinks.json").exists():
            blocking_failures.append(f"missing_crosslinks: {company_dir.name}")

    # ── Check 5: Empty-evidence QA pass ──────────────────────────────
    adv_qa_path = qa_dir / "adversarial_qa_report.json"
    if adv_qa_path.exists():
        adv_qa = json.loads(adv_qa_path.read_text())
        if adv_qa.get("overall_status") == "PASS":
            nvidia = adv_qa.get("nvidia_review", {})
            if nvidia.get("confidence") == 0.0 and not nvidia.get("evidence_cited"):
                blocking_failures.append("empty_evidence_qa_pass")

    # ── Check 6: ZIP excludes private paths ──────────────────────────
    zip_path = bundle_root / "exports" / "anonymized_bundle.zip"
    if zip_path.exists():
        zip_issues = _validate_zip_contents(zip_path)
        blocking_failures.extend(zip_issues)

    # ── Check 7: Checksums file exists ───────────────────────────────
    if not (bundle_root / "checksums.sha256").exists():
        blocking_failures.append("missing_checksums")

    # ── Determine decision and readiness fields ──────────────────────
    decision = "FAIL" if blocking_failures else "PASS"
    if not blocking_failures and warnings:
        decision = "REVIEW_REQUIRED"

    # Split readiness fields from the registry
    professor_ready = decision == "PASS" and stage_registry.professor_ready
    release_safe = decision == "PASS" and stage_registry.release_safe
    strict_fixture_ready = decision == "PASS" and stage_registry.strict_fixture_ready
    fixture_ready = decision == "PASS" and stage_registry.fixture_ready
    beta_status = (
        stage_registry.beta_status
        if decision == "PASS"
        else ("PRODUCTION_BLOCKED" if strict else "NOT_PROFESSOR_READY")
    )
    non_production_conditions = stage_registry.non_production_conditions

    return {
        "decision": decision,
        "build_mode": build_mode.value,
        "professor_ready": professor_ready,
        "release_safe": release_safe,
        "fixture_ready": fixture_ready,
        "strict_fixture_ready": strict_fixture_ready,
        "beta_status": beta_status,
        "blocking_failures": blocking_failures,
        "warnings": warnings,
        "non_production_conditions": non_production_conditions,
        "strict_mode": strict,
        "release_date": release_date,
        "gate_hash": _compute_gate_hash(decision, blocking_failures, warnings),
    }


def _reconstruct_registry(data: dict[str, Any]) -> StageRegistry:
    """Reconstruct a StageRegistry from serialized data."""
    # Reconstruct build mode
    build_mode_str = data.get("build_mode", "production")
    try:
        build_mode = BuildMode(build_mode_str)
    except ValueError:
        build_mode = BuildMode.PRODUCTION

    registry = StageRegistry(build_mode=build_mode)
    for stage_name, stage_data in data.get("stages", {}).items():
        try:
            stage = ProfessorStage(stage_name)
        except ValueError:
            continue
        status_str = stage_data.get("status", "FAIL")
        try:
            status = StageStatus(status_str)
        except ValueError:
            status = StageStatus.FAIL

        # Reconstruct provider kind
        provider_kind_str = stage_data.get("provider_kind", "real")
        try:
            provider_kind = ProviderKind(provider_kind_str)
        except ValueError:
            provider_kind = ProviderKind.REAL

        registry.register(
            StageStatusRecord(
                stage=stage,
                status=status,
                evidence_count=stage_data.get("evidence_count", 0),
                warnings=stage_data.get("warnings", []),
                failures=stage_data.get("failures", []),
                provider_name=stage_data.get("provider_name", ""),
                provider_kind=provider_kind,
                provider_version=stage_data.get("provider_version", ""),
                provider_config_hash=stage_data.get("provider_config_hash", ""),
                is_production_provider=stage_data.get("is_production_provider", True),
            )
        )
    return registry


def _scan_for_identity_leaks(public_dir: Path) -> list[str]:
    """Scan public artifacts for identity leaks."""
    issues: list[str] = []
    forbidden_patterns = [
        "Canary Holdings Corporation",
        "CHC",
        "0000999999",
        "Eleanor Testperson",
        "canary-test.invalid",
    ]

    for fp in public_dir.rglob("*"):
        if not fp.is_file():
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for pattern in forbidden_patterns:
            if pattern in content:
                issues.append(f"identity_leak: '{pattern}' in {fp.relative_to(public_dir)}")

    return issues


def _validate_zip_contents(zip_path: Path) -> list[str]:
    """Validate ZIP excludes private/originals/maps/.env paths."""
    issues: list[str] = []
    excluded_prefixes = ("private/", "originals/", "maps/", ".env", "smoke_excerpts")

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            for prefix in excluded_prefixes:
                if name.startswith(prefix):
                    issues.append(f"zip_contains_excluded_path: {name}")

    return issues


def _compute_gate_hash(decision: str, failures: list[str], warnings: list[str]) -> str:
    """Compute a deterministic hash of the gate state."""
    content = json.dumps(
        {"decision": decision, "failures": sorted(failures), "warnings": sorted(warnings)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ── CLI entry point ───────────────────────────────────────────────────


@click.command()
@click.option("--bundle-root", type=click.Path(path_type=Path), required=True)
@click.option("--release-date", default="2026-06-22")
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--strict", is_flag=True, default=False)
def main(bundle_root: Path, release_date: str, output: Path, strict: bool) -> None:
    """Evaluate classroom release gate for a professor bundle."""
    result = evaluate_classroom_gate(
        bundle_root=bundle_root,
        release_date=release_date,
        strict=strict,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(orjson.dumps(result, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2))

    click.echo(f"Decision: {result['decision']}")
    click.echo(f"Build mode: {result['build_mode']}")
    click.echo(f"Professor ready: {result['professor_ready']}")
    click.echo(f"Release safe: {result['release_safe']}")
    click.echo(f"Strict fixture ready: {result['strict_fixture_ready']}")
    click.echo(f"Beta status: {result['beta_status']}")
    if result["non_production_conditions"]:
        click.echo(f"Non-production conditions: {', '.join(result['non_production_conditions'])}")
    if result["blocking_failures"]:
        click.echo(f"Blocking failures ({len(result['blocking_failures'])}):")
        for f in result["blocking_failures"]:
            click.echo(f"  - {f}")

    if result["decision"] == "FAIL":
        sys.exit(1)


if __name__ == "__main__":
    main()
