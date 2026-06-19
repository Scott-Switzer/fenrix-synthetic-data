#!/usr/bin/env python3
"""Phase 3C real-model smoke wrapper for Google Colab.

Phase 3C real-model execution was DEFERRED on the developer's primary
workstation due to a MEASURED ``IO Error: No space left on device (os
error 28)`` raised from inside ``hf_hub`` during the snapshot-
reconstruction step of ``gliner==0.2.27``'s ``from_pretrained``.

This wrapper is the documented fallback: it does NOT reimplement any
adapter, evaluation, or review logic. It only:

1. Clones / syncs the repository at the pinned commit / branch.
2. Installs the project with ``.[dev,local-ner]`` extras.
3. Invokes the existing ``fenrix-synth`` CLI for the two Phase 3C
   commands — ``providers prepare`` then ``discover-model`` — exactly
   as they would be invoked from any developer shell.
4. Exports the sanitized JSON report to the immutable Colab workspace
   directory and prints the absolute path so the result can be
   attached to PR #5 evidence.

Usage from a Colab cell:

    %run /content/fenrix-synthetic-data/scripts/colab_phase3c_smoke.py

Or from a fresh notebook that has not yet cloned the repo:

    !git clone https://github.com/Scott-Switzer/fenrix-synthetic-data.git
    %cd fenrix-synthetic-data
    %run scripts/colab_phase3c_smoke.py

The wrapper is INTENTIONALLY thin. Anything more (label set tuning,
threshold sweep, benchmark subset definition, review-queue edits)
belongs in a follow-up PR — never in a notebook.

Failure handling: every subprocess invocation has a hard wall-clock
timeout so a hung snapshot reconstruction cannot lock the Colab cell
indefinitely. ``CalledProcessError`` / ``TimeoutExpired`` is caught
and converted into a single combined diagnostic block (return code,
command, last stderr frame) so the root cause prints cleanly instead
of as a Python traceback. Output is captured via ``capture_output=True``
on every invocation so the diagnostic always has the actual stderr.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

PHASE3C_MODEL = "gliner-community/gliner_small-v2.5"
PHASE3C_COMPANY_ID = "TEST-CO-001"
PHASE3C_BRANCH = "feature/local-gliner-adapter"
PHASE3C_DOC = (
    # Synthetic-only — every value below is from the in-repo benchmark
    # fixture (bench-04) and contains no real HBAN data. The smoke
    # measures whether the adapter round-trips a real GLiNER response
    # through chunking, label mapping, deduplication, and the review
    # queue without auto-accepting any candidate.
    "Halloran Banking Group announced a partnership with Pinnacle Bay "
    "Securities Holdings. CEO Marisol Pelham said the new product will "
    "trade under the symbol HLG. The platform is called Treasury Income "
    "Note and will be reviewed by CFO Tomas Yairi."
)

# Per-step wall-clock limits (seconds). Conservative for free-tier Colab.
PIP_INSTALL_TIMEOUT_SECONDS = 900
CLI_TIMEOUT_SECONDS = 600


class Phase3CFailure(RuntimeError):
    """Raised when a Phase 3C subprocess fails with a recoverable diagnostic."""

    def __init__(self, *, step: str, cmd: list[str], returncode: int, stderr_tail: str) -> None:
        self.step = step
        self.cmd = cmd
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        super().__init__(f"{step}: returncode={returncode}")


def _run(
    cmd: list[str],
    *,
    step: str,
    timeout: float,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run a subprocess and surface a clean diagnostic on failure.

    Hard wall-clock timeout — ``subprocess.TimeoutExpired`` is converted
    into a ``Phase3CFailure`` so the wrapper always exits with an
    actionable error message instead of a Python traceback. Output is
    captured (text + capture_output=True) so that ``e.stderr`` carries
    the actual reactor text on failure.
    """
    printable = " ".join(shlex.quote(c) for c in cmd)
    print(f"\n[phase3c] {step}: $ {printable}", flush=True)
    try:
        subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            check=True,
            timeout=timeout,
            text=True,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as e:
        partial_stderr = (e.stderr or "")[-2000:] if isinstance(e.stderr, str) else ""
        raise Phase3CFailure(
            step=step,
            cmd=cmd,
            returncode=-1,
            stderr_tail=partial_stderr + f"\n[timed out after {timeout}s]",
        ) from e
    except subprocess.CalledProcessError as e:
        stderr_tail = (e.stderr or "")[-2000:]
        raise Phase3CFailure(
            step=step,
            cmd=cmd,
            returncode=e.returncode,
            stderr_tail=stderr_tail,
        ) from e


def _bounded_cache_env(*, hf_home: Path) -> dict[str, str]:
    """Build an env that pins all Hugging Face writes inside ``hf_home``.

    Colab free-tier disks are typically ~78 GiB; the developer's primary
    workstation was 100 % full. The wrapper pins every HF cache write
    (download blobs, snapshot reconstruction, XET-lite staging) inside
    a project-local directory that lives only for the lifetime of the
    Colab runtime session.
    """
    hf_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HF_HOME"] = str(hf_home)
    env["HF_HUB_CACHE"] = str(hf_home / "hub")
    env["HF_XET_HIGH_PERFORMANCE"] = "0"
    env["TMPDIR"] = str(hf_home / "tmp")
    (hf_home / "tmp").mkdir(parents=True, exist_ok=True)
    return env


def _write_synthetic_doc(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(PHASE3C_DOC, encoding="utf-8")
    print(f"[phase3c] wrote synthetic smoke document: {target}")


def _record_environment() -> dict[str, Any]:
    """Capture Part 1 record items: python, gliner, torch, platform, devices.

    The wrapper imports torch lazily so it does NOT force a torch
    download on Colab before the user explicitly opted into
    ``pip install -e .[dev,local-ner]``.
    """
    record: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    try:
        import gliner  # noqa: F401

        record["gliner"] = getattr(gliner, "__version__", "unknown")
    except ImportError:
        record["gliner"] = None
    try:
        import torch  # type: ignore[import-not-found]

        record["torch"] = torch.__version__
        mps = torch.backends.mps.is_available() if hasattr(torch.backends, "mps") else None
        record["mps_available"] = mps
        record["cuda_available"] = torch.cuda.is_available()
    except ImportError:
        record["torch"] = None
        record["mps_available"] = None
        record["cuda_available"] = None
    return record


def _format_failure(failure: Phase3CFailure) -> str:
    printable = " ".join(shlex.quote(c) for c in failure.cmd)
    return (
        f"\n[phase3c] FAILURE in step: {failure.step}\n"
        f"[phase3c] returncode: {failure.returncode}\n"
        f"[phase3c] command: {printable}\n"
        f"[phase3c] --- last 2 KiB of stderr ---\n"
        f"{failure.stderr_tail}\n"
        f"[phase3c] --- end stderr ---\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("/content/fenrix-synthetic-data"),
        help="Path to the cloned fenrix-synthetic-data repository.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("/content/fenrix-phase3c"),
        help="Project-local scratch dir for model cache + sanitized output.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Confidence threshold for the real-model pass (default: 0.5).",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip `pip install -e .[dev,local-ner]` (use when already installed).",
    )
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root.resolve()
    work_dir: Path = args.work_dir.resolve()
    if not (repo_root / "pyproject.toml").exists():
        print(
            f"[phase3c] ERROR: {repo_root / 'pyproject.toml'} not found.\n"
            f"[phase3c] Clone the repo first:\n"
            f"[phase3c]     !git clone https://github.com/Scott-Switzer/"
            f"fenrix-synthetic-data.git {repo_root}\n"
            f"[phase3c]     !cd {repo_root} && git checkout {PHASE3C_BRANCH}",
            file=sys.stderr,
        )
        return 2

    print(f"[phase3c] repo_root = {repo_root}")
    print(f"[phase3c] work_dir  = {work_dir}")
    print(f"[phase3c] model     = {PHASE3C_MODEL}")
    print(f"[phase3c] threshold = {args.threshold}")
    print(f"[phase3c] company   = {PHASE3C_COMPANY_ID}")
    print("[phase3c] synthetic-only smoke document; no real HBAN data.")

    env = _bounded_cache_env(hf_home=work_dir / "hf_home")
    env_record = _record_environment()
    print(f"[phase3c] environment record: {env_record}")

    doc_path = work_dir / "smoke_doc.md"
    private_root = work_dir / "private"
    sanitized_path = work_dir / "sanitized_smoke.json"
    _write_synthetic_doc(doc_path)

    try:
        if not args.skip_install:
            _run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-e",
                    f"{repo_root}[dev,local-ner]",
                ],
                step="pip_install",
                timeout=PIP_INSTALL_TIMEOUT_SECONDS,
                cwd=repo_root,
                env=env,
            )

        # Step 1: pre-download + cache the model so the inference call
        # below can run from a fully reconstructable snapshot.
        _run(
            [
                sys.executable,
                "-m",
                "fenrix_synthetic.cli",
                "providers",
                "prepare",
                "--model",
                PHASE3C_MODEL,
                "--allow-download",
            ],
            step="providers_prepare",
            timeout=CLI_TIMEOUT_SECONDS,
            cwd=repo_root,
            env=env,
        )

        # Step 2: real-model discovery via the existing Fenrix CLI.
        _run(
            [
                sys.executable,
                "-m",
                "fenrix_synthetic.cli",
                "discover-model",
                "--provider",
                "gliner_local",
                "--company",
                PHASE3C_COMPANY_ID,
                "--document",
                str(doc_path),
                "--model",
                PHASE3C_MODEL,
                "--threshold",
                str(args.threshold),
                "--allow-download",
                "--private-output-root",
                str(private_root),
                "--output",
                str(sanitized_path),
            ],
            step="discover_model",
            timeout=CLI_TIMEOUT_SECONDS,
            cwd=repo_root,
            env=env,
        )
    except Phase3CFailure as failure:
        sys.stderr.write(_format_failure(failure))
        sys.stderr.flush()
        return 3
    except Exception:
        # Last-ditch: print traceback but keep the script's exit code
        # consistent (3) so Colab prints a single error message.
        traceback.print_exc(file=sys.stderr)
        return 3

    print("\n[phase3c] DONE.")
    print(f"[phase3c] sanitized report: {sanitized_path}")
    print(f"[phase3c] private review queue: {private_root}")
    print(
        "[phase3c] IMPORTANT: no candidate has been auto-accepted, "
        "auto-promoted, or auto-masked by this wrapper. Every result "
        "is pending human review via the standard PR #5 sign-off path."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
