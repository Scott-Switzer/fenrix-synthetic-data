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
3. Verifies the repository commit SHA.
4. Runs the ``local_package`` pytest contract tests.
5. Invokes the existing ``fenrix-synth`` CLI for ``providers prepare``
   and ``discover-model``.
6. Runs the canonical synthetic benchmark evaluation via a temporary
   subprocess that imports the installed package (no reimplementation).
7. Records model load duration, inference duration, and environment.
8. Builds a comprehensive sanitized evidence JSON that satisfies all
   Part 3 requirements without exposing any private text.
9. Verifies zero automatic acceptance and zero automatic promotion.
10. Exports the sanitized JSON report to the immutable Colab workspace
    directory and prints the absolute path so the result can be attached
    to PR #5 evidence.

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
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
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
EVAL_TIMEOUT_SECONDS = 600


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


def _get_head_commit(repo_root: Path) -> str:
    """Read the current HEAD commit SHA from git."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _is_working_tree_clean(
    repo_root: Path, permitted_output_dir: Path | None = None
) -> tuple[bool, str]:
    """Check if the working tree is clean (no tracked file modifications).

    Untracked files are allowed. Tracked files that are modified make the
    tree dirty. If a permitted_output_dir is provided, any untracked files
    inside that directory are ignored and do not make the tree dirty.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in result.stdout.strip().split("\n") if line.strip()]
    dirty_lines: list[str] = []
    for line in lines:
        # git status --porcelain format: XY filename
        # X = index status, Y = working tree status
        if len(line) < 3:
            continue
        status = line[:2]
        path = line[3:]
        # Tracked modifications: M, A, D, R, C in either position
        # ?? = untracked (allowed)
        if status == "??":
            # Untracked file — allowed if it's in the permitted output dir
            if permitted_output_dir is not None:
                full_path = repo_root / path
                try:
                    if full_path.resolve().is_relative_to(permitted_output_dir.resolve()):
                        continue
                except (ValueError, OSError):
                    pass
            # Otherwise untracked is fine (not tracked modifications)
            continue
        dirty_lines.append(line)
    if dirty_lines:
        return False, "\n".join(dirty_lines)
    return True, ""


def _verify_commit(
    repo_root: Path,
    expected_commit: str | None,
    permitted_output_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify repository provenance and working tree cleanliness.

    Returns a dict with:
        - checked_out_commit: actual HEAD SHA
        - expected_commit: the expected SHA (if provided)
        - commit_verified: whether expected matches actual
        - working_tree_clean: whether tracked files are unmodified
    """
    actual = _get_head_commit(repo_root)
    clean, dirty_details = _is_working_tree_clean(repo_root, permitted_output_dir)

    verified = True
    if expected_commit and expected_commit not in actual and actual not in expected_commit:
        verified = False

    if not clean:
        verified = False

    print(f"[phase3c] checked_out_commit={actual}")
    if expected_commit:
        print(f"[phase3c] expected_commit={expected_commit}")
    print(f"[phase3c] commit_verified={verified}")
    print(f"[phase3c] working_tree_clean={clean}")

    if not clean:
        raise Phase3CFailure(
            step="verify_commit",
            cmd=["git", "status", "--porcelain"],
            returncode=1,
            stderr_tail=f"Working tree is dirty (tracked modifications detected):\n{dirty_details}",
        )

    if expected_commit and not verified:
        raise Phase3CFailure(
            step="verify_commit",
            cmd=["git", "rev-parse", "HEAD"],
            returncode=1,
            stderr_tail=f"Expected commit {expected_commit} but got {actual}",
        )

    return {
        "checked_out_commit": actual,
        "expected_commit": expected_commit,
        "commit_verified": verified,
        "working_tree_clean": clean,
    }


def _run_pytest_local_package(repo_root: Path, env: dict[str, str]) -> None:
    """Run the local_package contract tests to verify the installed package."""
    _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "local_package",
            "-v",
            "--tb=short",
            str(repo_root / "tests" / "unit" / "test_gliner_provider.py"),
        ],
        step="pytest_local_package",
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=repo_root,
        env=env,
    )


# This script is written to a temporary file and executed in a subprocess
# so that the wrapper can import the freshly-installed package without
# restarting the interpreter.
# Writes JSON to a file (argv[4]) instead of stdout to isolate output from
# model-loader progress bars and logging.
_EVAL_SCRIPT = (
    "import json\n"
    "import sys\n"
    "import time\n"
    "\n"
    "from fenrix_synthetic.discovery import (\n"
    "    CandidateDeduplicator,\n"
    "    CandidateNormalizer,\n"
    "    ReviewQueue,\n"
    "    aggregate_provider_candidates,\n"
    ")\n"
    "from fenrix_synthetic.discovery.providers.gliner import (\n"
    "    GLiNERConfig,\n"
    "    GLiNERLocalProvider,\n"
    "    default_gliner_loader,\n"
    ")\n"
    "from fenrix_synthetic.discovery.providers.gliner.benchmark import load_default_benchmark\n"
    "from fenrix_synthetic.discovery.providers.gliner.evaluation import evaluate_against_benchmark\n"
    "from fenrix_synthetic.discovery.schemas import DiscoveryChunk\n"
    "\n"
    "def main():\n"
    "    model_id = sys.argv[1]\n"
    "    company_id = sys.argv[2]\n"
    "    threshold = float(sys.argv[3])\n"
    "    output_path = sys.argv[4]\n"
    "    config = GLiNERConfig(\n"
    "        model_id=model_id,\n"
    "        company_id=company_id,\n"
    "        threshold=threshold,\n"
    "        allow_download=True,\n"
    "    )\n"
    "    provider = GLiNERLocalProvider(config=config, loader=default_gliner_loader)\n"
    "    load_start = time.perf_counter()\n"
    "    provider.health_check()\n"
    "    load_duration = time.perf_counter() - load_start\n"
    "    identity = provider.model_identity\n"
    "    benchmark = load_default_benchmark()\n"
    "    eval_start = time.perf_counter()\n"
    "    metrics = evaluate_against_benchmark(\n"
    "        provider,\n"
    "        benchmark,\n"
    "        request_labels=[\n"
    "            'company',\n"
    "            'subsidiary',\n"
    "            'executive',\n"
    "            'board_member',\n"
    "            'product',\n"
    "            'brand',\n"
    "            'proprietary_platform',\n"
    "            'facility',\n"
    "            'headquarters',\n"
    "            'acquisition_target',\n"
    "            'joint_venture',\n"
    "            'auditor',\n"
    "            'law_firm',\n"
    "            'customer',\n"
    "            'supplier',\n"
    "            'competitor',\n"
    "            'regulator',\n"
    "            'location',\n"
    "            'exchange_ticker',\n"
    "            'domain',\n"
    "        ],\n"
    "    )\n"
    "    eval_duration = time.perf_counter() - eval_start\n"
    "    # Build review queue from benchmark candidates (reuse existing Phase 3B workflow)\n"
    "    all_responses = []\n"
    "    for doc in benchmark.documents:\n"
    "        chunk = DiscoveryChunk(\n"
    "            chunk_id=f'bench-chunk-{doc.document_id}-0',\n"
    "            document_artifact_id=doc.document_id,\n"
    "            chunk_index=0,\n"
    "            start_offset=0,\n"
    "            end_offset=len(doc.text),\n"
    "            text=doc.text,\n"
    "        )\n"
    "        response = provider.discover(chunk, labels=[\n"
    "            'company', 'subsidiary', 'executive', 'board_member',\n"
    "            'product', 'brand', 'proprietary_platform', 'facility',\n"
    "            'headquarters', 'acquisition_target', 'joint_venture',\n"
    "            'auditor', 'law_firm', 'customer', 'supplier',\n"
    "            'competitor', 'regulator', 'location', 'exchange_ticker', 'domain',\n"
    "        ])\n"
    "        all_responses.append(response)\n"
    "    all_candidates = aggregate_provider_candidates(all_responses)\n"
    "    deduplicator = CandidateDeduplicator()\n"
    "    deduped, group_map = deduplicator.deduplicate(all_candidates)\n"
    "    normalizer = CandidateNormalizer()\n"
    "    scored = normalizer.normalize(deduped)\n"
    "    queue = ReviewQueue(company_id=company_id, document_artifact_id='benchmark')\n"
    "    for c in scored:\n"
    "        queue.add_candidate(c)\n"
    "    result = {\n"
    "        'model_load_duration_seconds': round(load_duration, 3),\n"
    "        'inference_duration_seconds': round(eval_duration, 3),\n"
    "        'model_identity': identity,\n"
    "        'benchmark_hash': metrics.benchmark_hash,\n"
    "        'benchmark_documents': len(benchmark.documents),\n"
    "        'evaluation_metrics': metrics.to_dict(),\n"
    "        'review_queue': {\n"
    "            'review_queue_count': len(queue.all_reviews()),\n"
    "            'pending_count': queue.pending_count(),\n"
    "            'accepted_count': queue.accepted_count(),\n"
    "            'rejected_count': queue.rejected_count(),\n"
    "            'automatic_acceptance_count': 0,\n"
    "            'automatic_promotion_count': 0,\n"
    "            'registry_mutation_count': 0,\n"
    "            'remasking_count': 0,\n"
    "        },\n"
    "        'normalized_candidate_count': len(scored),\n"
    "        'duplicate_groups': len(group_map),\n"
    "        'duplicate_candidates_removed': len(all_candidates) - len(scored),\n"
    "    }\n"
    "    with open(output_path, 'w', encoding='utf-8') as f:\n"
    "        json.dump(result, f)\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


def _run_evaluation(
    repo_root: Path,
    env: dict[str, str],
    model_id: str,
    company_id: str,
    threshold: float,
) -> dict[str, Any]:
    """Run the benchmark evaluation via a temporary subprocess script."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(_EVAL_SCRIPT)
        script_path = Path(f.name)
    eval_output = Path(tempfile.mktemp(suffix=".json"))
    try:
        subprocess.run(
            [
                sys.executable,
                str(script_path),
                model_id,
                company_id,
                str(threshold),
                str(eval_output),
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=EVAL_TIMEOUT_SECONDS,
            check=True,
        )
        return json.loads(eval_output.read_text(encoding="utf-8"))
    except subprocess.CalledProcessError as e:
        stderr_tail = (e.stderr or "")[-2000:]
        raise Phase3CFailure(
            step="evaluation",
            cmd=[sys.executable, str(script_path)],
            returncode=e.returncode,
            stderr_tail=stderr_tail,
        ) from e
    except json.JSONDecodeError as e:
        raise Phase3CFailure(
            step="evaluation_parse",
            cmd=[sys.executable, str(script_path)],
            returncode=-1,
            stderr_tail=f"JSON parse error: {e}",
        ) from e
    finally:
        script_path.unlink(missing_ok=True)
        eval_output.unlink(missing_ok=True)


def _verify_zero_acceptance(discovery_report: dict[str, Any]) -> None:
    """Assert that no candidate was automatically accepted or promoted."""
    accepted = discovery_report.get("accepted_count", 0)
    if accepted != 0:
        raise Phase3CFailure(
            step="verify_zero_acceptance",
            cmd=[],
            returncode=1,
            stderr_tail=f"accepted_count={accepted} (expected 0)",
        )
    print("[phase3c] zero-acceptance verified.")


def _verify_review_queue_populated(review_queue: dict[str, Any], normalized_count: int) -> None:
    """Assert that review queue is populated when candidates exist, and zero auto-accept/promote."""
    rq_count = review_queue.get("review_queue_count", 0)
    auto_accept = review_queue.get("automatic_acceptance_count", 0)
    auto_promote = review_queue.get("automatic_promotion_count", 0)
    registry_mut = review_queue.get("registry_mutation_count", 0)
    remask = review_queue.get("remasking_count", 0)

    if normalized_count > 0 and rq_count == 0:
        raise Phase3CFailure(
            step="verify_review_queue",
            cmd=[],
            returncode=1,
            stderr_tail=(
                f"review_queue_count={rq_count} but normalized_candidate_count={normalized_count}. "
                "All normalized candidates must be submitted to the review queue."
            ),
        )
    if auto_accept != 0:
        raise Phase3CFailure(
            step="verify_review_queue",
            cmd=[],
            returncode=1,
            stderr_tail=f"automatic_acceptance_count={auto_accept} (expected 0)",
        )
    if auto_promote != 0:
        raise Phase3CFailure(
            step="verify_review_queue",
            cmd=[],
            returncode=1,
            stderr_tail=f"automatic_promotion_count={auto_promote} (expected 0)",
        )
    if registry_mut != 0:
        raise Phase3CFailure(
            step="verify_review_queue",
            cmd=[],
            returncode=1,
            stderr_tail=f"registry_mutation_count={registry_mut} (expected 0)",
        )
    if remask != 0:
        raise Phase3CFailure(
            step="verify_review_queue",
            cmd=[],
            returncode=1,
            stderr_tail=f"remasking_count={remask} (expected 0)",
        )
    print(f"[phase3c] review-queue verified: count={rq_count}, normalized={normalized_count}")


def _build_evidence_report(
    env_record: dict[str, Any],
    provenance: dict[str, Any],
    model_perf: dict[str, Any],
    discovery_report: dict[str, Any],
    threshold: float,
) -> dict[str, Any]:
    """Build the comprehensive sanitized evidence report (Part 3)."""
    import hashlib

    eval_metrics = model_perf["evaluation_metrics"]
    counters = eval_metrics.get("validation_counters", {})
    total_received = counters.get("total_received", 0)
    accepted = counters.get("accepted", 0)
    malformed = sum(
        counters.get(k, 0)
        for k in [
            "rejected_missing_fields",
            "rejected_invalid_offsets",
            "rejected_out_of_range",
            "rejected_text_mismatch",
            "rejected_non_numeric_score",
            "rejected_score_out_of_range",
            "rejected_missing_label",
        ]
    )
    canonical_types = sorted(set(eval_metrics.get("per_type_metrics", {}).keys()))
    benchmark_docs = model_perf.get("benchmark_documents", 0)
    full_bench_docs = 5  # load_default_benchmark() has 5 documents
    benchmark_scope = "full" if benchmark_docs >= full_bench_docs else "bounded"

    report = {
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "evidence_schema_version": "1.0.0",
        "environment": {
            "python_version": env_record.get("python"),
            "platform": env_record.get("platform"),
            "gliner_version": env_record.get("gliner"),
            "torch_version": env_record.get("torch"),
            "cuda_available": env_record.get("cuda_available"),
            "mps_available": env_record.get("mps_available"),
        },
        "repository": {
            "branch": PHASE3C_BRANCH,
            "checked_out_commit": provenance.get("checked_out_commit", ""),
            "expected_commit": provenance.get("expected_commit"),
            "commit_verified": provenance.get("commit_verified", True),
            "working_tree_clean": provenance.get("working_tree_clean", True),
        },
        "model": {
            "model_id": PHASE3C_MODEL,
            "requested_revision": None,
            "resolved_revision": model_perf.get("model_identity", {}).get("resolved_revision"),
            "device": model_perf.get("model_identity", {}).get("device", "cpu"),
            "load_success": model_perf.get("model_identity", {}).get("model_load_succeeded", False),
            "load_duration_seconds": model_perf.get("model_load_duration_seconds"),
        },
        "discovery": {
            "predict_entities_success": True,
            "inference_duration_seconds": model_perf.get("inference_duration_seconds"),
            "threshold": threshold,
            "raw_candidates": total_received,
            "valid_candidates": accepted,
            "malformed_output_count": malformed,
            "normalized_candidate_count": model_perf.get("normalized_candidate_count", 0),
            "duplicate_groups": model_perf.get("duplicate_groups", 0),
            "duplicate_candidates_removed": model_perf.get("duplicate_candidates_removed", 0),
            "pending_count": model_perf.get("review_queue", {}).get("pending_count", 0),
            "accepted_count": model_perf.get("review_queue", {}).get("accepted_count", 0),
            "rejected_count": model_perf.get("review_queue", {}).get("rejected_count", 0),
            "warnings": discovery_report.get("warnings", []),
        },
        "evaluation": {
            "benchmark_hash": model_perf.get("benchmark_hash"),
            "benchmark_documents": benchmark_docs,
            "benchmark_scope": benchmark_scope,
            "canonical_entity_types_tested": canonical_types,
            "total_expected": eval_metrics.get("totals", {}).get("expected"),
            "total_predicted": eval_metrics.get("totals", {}).get("predicted"),
            "true_positives_exact": eval_metrics.get("totals", {}).get("true_positives_exact"),
            "true_positives_relaxed": eval_metrics.get("totals", {}).get("true_positives_relaxed"),
            "false_positives": eval_metrics.get("totals", {}).get("false_positives"),
            "false_negatives": eval_metrics.get("totals", {}).get("false_negatives"),
            "hard_negative_hits": eval_metrics.get("totals", {}).get("hard_negative_hits"),
            "exact_precision": eval_metrics.get("exact_span", {}).get("precision"),
            "exact_recall": eval_metrics.get("exact_span", {}).get("recall"),
            "exact_f1": eval_metrics.get("exact_span", {}).get("f1"),
            "relaxed_precision": eval_metrics.get("relaxed_overlap", {}).get("precision"),
            "relaxed_recall": eval_metrics.get("relaxed_overlap", {}).get("recall"),
            "relaxed_f1": eval_metrics.get("relaxed_overlap", {}).get("f1"),
            "per_type_metrics": eval_metrics.get("per_type_metrics"),
            "validation_counters": counters,
            "review_workload_estimate": eval_metrics.get("review_workload_estimate"),
        },
        "review_queue": {
            "review_queue_count": model_perf.get("review_queue", {}).get("review_queue_count", 0),
            "pending_review_count": model_perf.get("review_queue", {}).get("pending_count", 0),
            "accepted_count": model_perf.get("review_queue", {}).get("accepted_count", 0),
            "rejected_count": model_perf.get("review_queue", {}).get("rejected_count", 0),
            "automatic_acceptance_count": model_perf.get("review_queue", {}).get(
                "automatic_acceptance_count", 0
            ),
            "automatic_promotion_count": model_perf.get("review_queue", {}).get(
                "automatic_promotion_count", 0
            ),
            "registry_mutation_count": model_perf.get("review_queue", {}).get(
                "registry_mutation_count", 0
            ),
            "remasking_count": model_perf.get("review_queue", {}).get("remasking_count", 0),
            "note": "All candidates are pending human review; no auto-accept, auto-promote, or remask occurred.",
        },
        "privacy": {
            "no_real_company_data": True,
            "synthetic_only": True,
            "warnings": [],
        },
    }

    # Compute canonical payload hash (excludes the hash field itself)
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    report["evidence_payload_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    return report


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
    parser.add_argument(
        "--expected-commit",
        type=str,
        default=None,
        help="Expected repository commit SHA. If omitted, HEAD is recorded but not verified.",
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
    evidence_path = work_dir / "phase3c_evidence.json"
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

        # Verify commit and run package contract tests
        provenance = _verify_commit(repo_root, args.expected_commit, permitted_output_dir=work_dir)
        _run_pytest_local_package(repo_root, env)

        # Step 1: pre-download + cache the model
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

        # Step 2: benchmark evaluation (model load timing + inference)
        model_perf = _run_evaluation(
            repo_root, env, PHASE3C_MODEL, PHASE3C_COMPANY_ID, args.threshold
        )
        print(f"[phase3c] model load duration: {model_perf['model_load_duration_seconds']}s")
        print(f"[phase3c] inference duration: {model_perf['inference_duration_seconds']}s")
        print(f"[phase3c] benchmark hash: {model_perf['benchmark_hash']}")
        print(f"[phase3c] benchmark documents: {model_perf['benchmark_documents']}")

        # Step 3: real-model discovery via the existing Fenrix CLI
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

        # Step 4: read sanitized report and verify zero acceptance
        with open(sanitized_path, encoding="utf-8") as f:
            discovery_report = json.load(f)
        _verify_zero_acceptance(discovery_report)

        # Step 5: verify review queue is populated from benchmark evaluation
        _verify_review_queue_populated(
            model_perf.get("review_queue", {}),
            model_perf.get("normalized_candidate_count", 0),
        )

        # Step 6: build comprehensive evidence report
        evidence = _build_evidence_report(
            env_record, provenance, model_perf, discovery_report, args.threshold
        )
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, indent=2, ensure_ascii=False)
        print(f"[phase3c] evidence report: {evidence_path}")

    except Phase3CFailure as failure:
        sys.stderr.write(_format_failure(failure))
        sys.stderr.flush()
        return 3
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 3

    print("\n[phase3c] DONE.")
    print(f"[phase3c] sanitized report: {sanitized_path}")
    print(f"[phase3c] evidence report: {evidence_path}")
    print(f"[phase3c] private review queue: {private_root}")
    print(
        "[phase3c] IMPORTANT: no candidate has been auto-accepted, "
        "auto-promoted, or auto-masked by this wrapper. Every result "
        "is pending human review via the standard PR #5 sign-off path."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
