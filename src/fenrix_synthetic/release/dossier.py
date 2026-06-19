"""Release dossier generation (Phase 4J).

Generates a sanitized release bundle:
release/SYNTH_001/
  README.md
  manifest.json
  structured/
  unstructured/
  privacy_report.json
  utility_report.json
  attack_summary.json
  transformation_summary.json
  release_decision.json
  checksums.json

Contains: NO real company identity, NO raw source data, NO private paths,
NO private replacement map, NO unredacted attack guesses.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_checksums(files: dict[str, str]) -> dict[str, str]:
    """Compute SHA-256 checksums for each file in the dossier."""
    result: dict[str, str] = {}
    for name, content in sorted(files.items()):
        result[name] = hashlib.sha256(content.encode()).hexdigest()
    return result


def validate_dossier(
    dossier_root: Path,
    *,
    required_files: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Validate a release dossier has all required files and no forbidden content.

    Returns (is_valid, list of issues).
    """
    required = required_files or [
        "README.md",
        "manifest.json",
        "privacy_report.json",
        "utility_report.json",
        "attack_summary.json",
        "transformation_summary.json",
        "release_decision.json",
        "checksums.json",
    ]

    issues: list[str] = []

    # Check required files exist
    for rf in required:
        if not (dossier_root / rf).exists():
            issues.append(f"Missing required file: {rf}")

    # Check for forbidden content patterns
    forbidden_patterns = [
        "SRC_001",
        "FENRIX_PRIVATE_ROOT",
        "private_root",
    ]

    for f in dossier_root.rglob("*.json"):
        try:
            content = f.read_text()
            for pat in forbidden_patterns:
                if pat in content:
                    issues.append(
                        f"Forbidden pattern '{pat}' found in {f.relative_to(dossier_root)}"
                    )
        except (OSError, UnicodeDecodeError):
            pass

    return len(issues) == 0, issues


def generate_readme(
    company_id: str,
    release_version: str,
    policy_version: str,
    limitations: list[str] | None = None,
) -> str:
    """Generate the release dossier README.md."""
    lines = [
        f"# {company_id} Synthetic Data Release",
        "",
        f"**Release Version:** {release_version}",
        f"**Policy Version:** {policy_version}",
        f"**Generated:** {datetime.now(UTC).isoformat()}",
        "",
        "## Contents",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `manifest.json` | Release manifest with provenance and hashes |",
        "| `privacy_report.json` | Privacy evaluation summary |",
        "| `utility_report.json` | Utility evaluation summary |",
        "| `attack_summary.json` | Re-identification attack results (redacted) |",
        "| `transformation_summary.json` | Data transformation details |",
        "| `release_decision.json` | Release gate assessment |",
        "| `checksums.json` | File integrity checksums |",
        "",
        "## Important Disclaimers",
        "",
        "- This data has been transformed for privacy. It does NOT contain",
        "  original source company identities.",
        "- Transformed prices are NOT tradable historical prices.",
        "- This release does NOT claim cryptographic anonymity.",
        "- The data may still be re-identifiable through advanced analysis.",
        "- Use of this data for investment decisions is at your own risk.",
        "",
    ]

    if limitations:
        lines.append("## Limitations")
        lines.append("")
        for limit in limitations:
            lines.append(f"- {limit}")
        lines.append("")

    lines.append("## Contact")
    lines.append("")
    lines.append("For questions about this release, contact the FENRIX team.")
    lines.append("")

    return "\n".join(lines)


def generate_manifest(
    company_id: str,
    release_version: str,
    files: dict[str, str],
    checksums: dict[str, str],
    atlas_hash: str,
    pipeline_version: str,
) -> dict[str, Any]:
    """Generate the release dossier manifest."""
    return {
        "manifest_version": "1.0.0",
        "company_id": company_id,
        "release_version": release_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "pipeline_version": pipeline_version,
        "atlas_config_hash": atlas_hash,
        "files": sorted(files.keys()),
        "checksums": checksums,
        "disclaimers": [
            "Contains no real company identity",
            "Contains no raw source data",
            "Contains no private paths",
            "Contains no private replacement map",
            "Contains no unredacted attack guesses",
            "Transformed prices are not tradable historical prices",
        ],
    }


def generate_dossier(
    dossier_root: Path,
    *,
    company_id: str = "SYNTH_001",
    release_version: str = "1.0.0",
    policy_version: str = "pilot_v1",
    pipeline_version: str = "0.1.0",
    atlas_hash: str = "",
    privacy_report: dict[str, Any] | None = None,
    utility_report: dict[str, Any] | None = None,
    attack_summary: dict[str, Any] | None = None,
    transformation_summary: dict[str, Any] | None = None,
    release_decision: dict[str, Any] | None = None,
    masked_documents: dict[str, str] | None = None,
    structured_data: dict[str, Any] | None = None,
) -> Path:
    """Generate a complete release dossier.

    Args:
        dossier_root: Root directory for the dossier (must be outside repo)
        company_id: Release identifier (SYNTH_001)
        release_version: Release version string
        policy_version: Policy version identifier
        pipeline_version: Pipeline version
        atlas_hash: Identity atlas config hash
        privacy_report: Privacy evaluation report
        utility_report: Utility evaluation report
        attack_summary: Attack results summary (redacted)
        transformation_summary: Transformation details
        release_decision: Gate assessment result
        masked_documents: Dict of filename -> masked content
        structured_data: Dict of variant -> transformed data

    Returns:
        Path to the generated dossier root
    """
    dossier_root.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}

    # Write README
    readme = generate_readme(company_id, release_version, policy_version)
    (dossier_root / "README.md").write_text(readme)
    files["README.md"] = readme

    # Write masked documents
    if masked_documents:
        unstructured_dir = dossier_root / "unstructured"
        unstructured_dir.mkdir(exist_ok=True)
        for fname, content in masked_documents.items():
            (unstructured_dir / fname).write_text(content)

    # Write structured data
    if structured_data:
        structured_dir = dossier_root / "structured"
        structured_dir.mkdir(exist_ok=True)
        for variant, data in structured_data.items():
            (structured_dir / f"{variant}.json").write_text(
                json.dumps(data, indent=2)
            )  # Write reports
    for fname, data in [
        ("privacy_report.json", privacy_report),
        ("utility_report.json", utility_report),
        ("attack_summary.json", attack_summary),
        ("transformation_summary.json", transformation_summary),
        ("release_decision.json", release_decision),
    ]:
        if data:
            content = json.dumps(data, indent=2, sort_keys=True)
            (dossier_root / fname).write_text(content)
            files[fname] = content

    # Write manifest (MUST be before checksums so manifest is included)
    manifest = generate_manifest(
        company_id, release_version, files, {}, atlas_hash, pipeline_version
    )
    manifest_content = json.dumps(manifest, indent=2, sort_keys=True)
    (dossier_root / "manifest.json").write_text(manifest_content)
    files["manifest.json"] = manifest_content

    # Compute and write checksums (includes manifest)
    checksums = build_checksums(files)
    (dossier_root / "checksums.json").write_text(json.dumps(checksums, indent=2, sort_keys=True))

    return dossier_root
