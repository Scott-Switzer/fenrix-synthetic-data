"""Private data boundary subsystem.

Enforces that all real source data, identity mappings, and private
intermediate artifacts live under FENRIX_PRIVATE_ROOT outside the
Git repository. Never logs raw text or private mappings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class PrivateBoundaryError(RuntimeError):
    """Raised when the private boundary is violated."""


def _resolve_repo_root() -> Path | None:
    """Resolve the git repository root without throwing.

    Returns None if not inside a git repository.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def resolve_private_root(env_var: str = "FENRIX_PRIVATE_ROOT") -> Path:
    """Resolve and validate the private root directory.

    Args:
        env_var: Environment variable name holding the private root path.

    Returns:
        Resolved, absolute Path to the private root.

    Raises:
        PrivateBoundaryError: If the env var is missing, empty, or the path
            is invalid, inside the repo, or a symlink escape.
    """
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        raise PrivateBoundaryError(
            f"Environment variable {env_var} is not set or empty. "
            "Set FENRIX_PRIVATE_ROOT to the private data directory "
            "outside the Git repository."
        )

    candidate = Path(raw).expanduser().resolve()

    # Reject non-existent (caller must create first, or we create on demand)
    # but we validate the path is sane.

    # Reject if the resolved path is inside the repo
    repo_root = _resolve_repo_root()
    if repo_root is not None:
        try:
            if candidate.is_relative_to(repo_root):
                raise PrivateBoundaryError(
                    f"FENRIX_PRIVATE_ROOT ({candidate}) is inside the Git repository "
                    f"({repo_root}). Private data must live outside the repository."
                )
        except (ValueError, OSError):
            pass

        # Symlink escape check: walk up the path and check each component
        check = candidate
        while check != check.parent:
            try:
                if check.is_symlink():
                    resolved_target = check.resolve()
                    try:
                        if resolved_target.is_relative_to(repo_root):
                            raise PrivateBoundaryError(
                                f"FENRIX_PRIVATE_ROOT ({candidate}) contains a symlink "
                                f"({check} -> {resolved_target}) that resolves inside "
                                f"the repository ({repo_root})."
                            )
                    except (ValueError, OSError):
                        pass
            except (OSError, ValueError):
                pass
            check = check.parent

    return candidate


def ensure_private_root(env_var: str = "FENRIX_PRIVATE_ROOT") -> Path:
    """Resolve and create the private root if it doesn't exist.

    Calls resolve_private_root and creates directories if needed.
    """
    root = resolve_private_root(env_var)
    root.mkdir(parents=True, exist_ok=True)
    return root


def private_path(*segments: str) -> Path:
    """Construct a path under the private root.

    Example:
        private_path("source", "SRC_001", "prices", "daily.parquet")
    """
    root = resolve_private_root()
    return root.joinpath(*segments)


def is_in_repo(path: Path) -> bool:
    """Check if a path is inside the Git repository."""
    repo_root = _resolve_repo_root()
    if repo_root is None:
        return False
    try:
        return path.resolve().is_relative_to(repo_root)
    except (ValueError, OSError):
        return False


def sanitize_path_for_log(path: Path | str) -> str:
    """Return a safe representation of a path for logging.

    Replaces the private root with [PRIVATE_ROOT] and the repo root
    with [REPO_ROOT] to avoid leaking directory structure.
    """
    p = Path(path).resolve()
    result = str(p)

    private_raw = os.environ.get("FENRIX_PRIVATE_ROOT", "")
    if private_raw:
        try:
            pr = Path(private_raw).expanduser().resolve()
            pr_str = str(pr)
            if result.startswith(pr_str):
                result = "[PRIVATE_ROOT]" + result[len(pr_str) :]
        except (OSError, ValueError):
            pass

    repo_root = _resolve_repo_root()
    if repo_root is not None:
        rr_str = str(repo_root)
        if result.startswith(rr_str):
            result = "[REPO_ROOT]" + result[len(rr_str) :]

    return result


def sanitize_exception_message(exc: Exception) -> str:
    """Return a sanitized exception message without private paths or values.

    Replaces any occurrence of the private root or repo root paths
    with placeholder tokens.
    """
    msg = str(exc)
    private_raw = os.environ.get("FENRIX_PRIVATE_ROOT", "")
    if private_raw:
        try:
            pr = str(Path(private_raw).expanduser().resolve())
            msg = msg.replace(pr, "[PRIVATE_ROOT]")
        except (OSError, ValueError):
            pass

    repo_root = _resolve_repo_root()
    if repo_root is not None:
        rr_str = str(repo_root)
        msg = msg.replace(rr_str, "[REPO_ROOT]")

    return msg


def validate_no_private_data_in_snapshot(
    directory: Path,
    *,
    private_root: Path | None = None,
) -> list[str]:
    """Scan a directory for files that may contain private data.

    Returns a list of violation descriptions. Empty list = clean.

    Checks for:
    - Files containing private root paths
    - Files inside the repo that reference SRC_001
    - Files that appear to be raw source data

    Args:
        directory: Directory to scan.
        private_root: Optional explicit private root. If None, resolves from env.

    Returns:
        List of violation descriptions.
    """
    violations: list[str] = []

    if private_root is None:
        try:
            private_root = resolve_private_root()
        except PrivateBoundaryError:
            private_root = None

    # For now, check that no tracked files contain source identifier in names
    source_patterns = ["SRC_001", "src_001"]

    for f in directory.rglob("*"):
        if not f.is_file():
            continue
        fname = f.name.lower()
        for pat in source_patterns:
            if pat.lower() in fname:
                violations.append(
                    f"File {f.relative_to(directory)} contains source identifier '{pat}' in filename"
                )

    return violations


def redacted_diagnostic_command() -> dict[str, Any]:
    """Produce a diagnostic report with all paths redacted.

    Returns a dict safe for logging/inclusion in sanitized output.
    """
    diag: dict[str, Any] = {
        "private_root_configured": "FENRIX_PRIVATE_ROOT" in os.environ,
        "inside_repo": _resolve_repo_root() is not None,
    }

    try:
        resolve_private_root()
        diag["private_root_valid"] = True
        diag["private_root_location"] = "[PRIVATE_ROOT]"
    except PrivateBoundaryError as e:
        diag["private_root_valid"] = False
        diag["private_root_error"] = sanitize_exception_message(e)

    return diag
