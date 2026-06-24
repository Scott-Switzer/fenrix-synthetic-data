"""Student bundle packager for V3 release boundary.

Creates a safe professor-facing ZIP using allowlist-based inclusion.
Forbidden paths/patterns are blocked before ZIP creation.
Post-creation validation ensures no forbidden content leaked.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

# ── Allowlisted public paths ───────────────────────────────────────────────

PUBLIC_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "README.md",
    "QUICKSTART.md",
    "RUN_SUMMARY.md",
    "DATA_DICTIONARY.md",
    "RELEASE_MANIFEST.md",
    "RELEASE_MANIFEST.json",
    "public/",
    "qa/",
    "checksums.sha256",
    "run_summary.json",
    "artifact_inventory.csv",
)

# ── Forbidden path/pattern matchers ────────────────────────────────────────

FORBIDDEN_PATH_PREFIXES: tuple[str, ...] = (
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
    "exports/",
)

FORBIDDEN_PATH_SUBSTRINGS: tuple[str, ...] = (
    "_identity_",
    "_mapping_",
    "identity_map",
    "source_map",
    "_private_",
    "_raw_",
)

FORBIDDEN_EXTENSIONS: tuple[str, ...] = (
    ".env",
    ".key",
    ".pem",
    ".html",
    ".htm",
    ".xml",
    ".xbrl",
    ".sqlite",
    ".db",
    ".pyc",
    ".pyo",
    ".DS_Store",
)


@dataclasses.dataclass(frozen=True)
class BundleValidationResult:
    """Result of bundle validation before/after ZIP creation."""

    passed: bool
    entry_count: int
    total_bytes: int
    rejected_entries: list[str]
    allowed_entries: list[str]
    validation_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "entry_count": self.entry_count,
            "total_bytes": self.total_bytes,
            "rejected_count": len(self.rejected_entries),
            "allowed_count": len(self.allowed_entries),
            "rejected_entries": self.rejected_entries,
            "validation_hash": self.validation_hash,
        }


def _is_path_allowed(rel_path: str) -> bool:
    """Check if a relative path is in the public allowlist."""
    for prefix in PUBLIC_ALLOWLIST_PREFIXES:
        if rel_path == prefix or rel_path.startswith(prefix):
            return True
    return False


def _is_path_forbidden(rel_path: str) -> bool:
    """Check if a relative path matches forbidden patterns."""
    for prefix in FORBIDDEN_PATH_PREFIXES:
        if rel_path.startswith(prefix) or rel_path == prefix.rstrip("/"):
            return True
    for substring in FORBIDDEN_PATH_SUBSTRINGS:
        if substring in rel_path:
            return True
    for ext in FORBIDDEN_EXTENSIONS:
        if rel_path.lower().endswith(ext):
            return True
    return False


def validate_bundle_tree(bundle_root: Path) -> BundleValidationResult:
    """Validate a bundle directory tree before packaging.

    Rules:
    - Files in forbidden paths outside allowlisted areas → silently skipped.
    - Files in allowlisted areas with forbidden content → rejected.
    - Files in allowed paths → included.
    - Unknown files (not allowed, not forbidden) → rejected.
    """
    rejected: list[str] = []
    allowed: list[str] = []
    skipped_forbidden: list[str] = []
    total_bytes = 0

    for fp in sorted(bundle_root.rglob("*")):
        if not fp.is_file():
            continue
        rel = str(fp.relative_to(bundle_root))

        is_forbidden = _is_path_forbidden(rel)
        is_allowed = _is_path_allowed(rel)

        if is_forbidden:
            if is_allowed:
                # Forbidden content inside an allowlisted area → reject
                rejected.append(rel)
            else:
                # Forbidden path outside allowlisted area → skip silently
                skipped_forbidden.append(rel)
            continue

        if is_allowed:
            total_bytes += fp.stat().st_size
            allowed.append(rel)
        else:
            rejected.append(rel)

    passed = len(rejected) == 0
    h = hashlib.sha256(
        json.dumps(
            {"allowed": sorted(allowed), "rejected": sorted(rejected)}, sort_keys=True
        ).encode()
    ).hexdigest()[:16]

    return BundleValidationResult(
        passed=passed,
        entry_count=len(allowed) + len(rejected) + len(skipped_forbidden),
        total_bytes=total_bytes,
        rejected_entries=rejected,
        allowed_entries=allowed,
        validation_hash=h,
    )


def package_student_bundle(
    bundle_root: Path,
    output_path: Path | None = None,
    *,
    validate_before: bool = True,
    validate_after: bool = True,
) -> tuple[Path, BundleValidationResult, BundleValidationResult]:
    """Package a student-facing ZIP from the bundle root.

    Args:
        bundle_root: Root of the professor bundle directory.
        output_path: Output ZIP path (defaults to exports/anonymized_bundle.zip).
        validate_before: Run validation before ZIP creation.
        validate_after: Run validation after ZIP creation.

    Returns:
        Tuple of (zip_path, pre_validation, post_validation).

    Raises:
        RuntimeError: If pre-validation fails.
    """
    if output_path is None:
        output_path = bundle_root / "exports" / "anonymized_bundle.zip"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-validation
    pre_validation = validate_bundle_tree(bundle_root)
    if validate_before and not pre_validation.passed:
        raise RuntimeError(
            f"Bundle pre-validation failed with {len(pre_validation.rejected_entries)} "
            f"rejected entries: {pre_validation.rejected_entries[:10]}"
        )

    # Create ZIP with allowlisted files only
    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in pre_validation.allowed_entries:
            fp = bundle_root / rel
            if fp.is_file():
                zf.write(fp, rel)

    # Post-validation: re-read ZIP and verify no forbidden entries
    post_validation = _validate_zip_contents(output_path, pre_validation.allowed_entries)
    if validate_after and not post_validation.passed:
        raise RuntimeError(
            f"Bundle post-validation failed with {len(post_validation.rejected_entries)} "
            f"rejected entries in ZIP"
        )

    return output_path, pre_validation, post_validation


def _validate_zip_contents(zip_path: Path, expected_entries: list[str]) -> BundleValidationResult:
    """Validate ZIP contents against forbidden patterns."""
    rejected: list[str] = []
    found: list[str] = []
    total_bytes = 0

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if _is_path_forbidden(name):
                    rejected.append(name)
                else:
                    found.append(name)
                    try:
                        info = zf.getinfo(name)
                        total_bytes += info.file_size
                    except KeyError:
                        pass
    except (zipfile.BadZipFile, OSError):
        rejected.append("zip_corrupt")

    passed = len(rejected) == 0
    return BundleValidationResult(
        passed=passed,
        entry_count=len(found) + len(rejected),
        total_bytes=total_bytes,
        rejected_entries=rejected,
        allowed_entries=found,
        validation_hash="",
    )
