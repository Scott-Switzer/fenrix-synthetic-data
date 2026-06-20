"""Phase 5A: feature-only release CLI commands.

Five small composable commands that wire the existing S3
transform/attack/gate/evaluator/harvest domains to a CLI surface:

- s3-transform
- s3-attack
- s3-assess
- evaluate-submission
- atlas-harvest

Each command adheres to the global privacy/release contract:

* JSON output via atomic write (write to .tmp + rename).
* Console summary echoes only counts, hashes, decision strings
  (never raw private text or per-period P&L).
* Stable exit codes: 0 success; 2 invalid input; 3 privacy fail /
  private-root violation; 4 execution error; 5 ineligible for release.
* Required attacks / ablations / unique periods are checked before
  completion. Failure exits with code 2.
* Ineligible variants (S0/S1/S2/S3A) raise IneligibleVariantError at
  the gate boundary (s3-assess) and exit code 5.
* All output paths must resolve under FENRIX_PRIVATE_ROOT and outside
  any tracked git repository.

Phase 5A CLI close-out (FailureModeSpec):
* Each command maps domain exceptions onto the four named
  `cli_errors.ClickException` subclasses (InvalidInputError,
  PrivacyFailureError, ExecutionFailureError, IneligibleVariantError)
  so Click terminates with the configured exit code.
* The outer catch-all in each command NEVER prints Python exception
  type names or exception message bodies, since they could leak
  internal paths, hashed identifiers, or per-period values.
* `evaluate-submission` fails closed when zero decisions survive
  lag alignment, when the private truth is empty, or when the
  evaluator returns an empty metric set.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
import orjson

from .cli_errors import (
    ExecutionFailureError,
    IneligibleVariantError,
    InvalidInputError,
    PrivacyFailureError,
)
from .release.eligibility import (
    IneligibleVariantError as DomainIneligibleVariantError,
)
from .release.eligibility import (
    assert_releasable_variant,
)

# ── Forbidden console-output keys (must never reach stdout) ─────────

_FORBIDDEN_CONSOLE_TOKENS = {
    "private_matched_text",
    "matched_text",
    "raw_response",
    "context_excerpt",
    "candidate_text",
    "private_hash",
    "source_alias",
    "secret",
    "accession",
}


def _resolve_private_root(
    private_root_arg: Path | None,
    ctx_obj: dict[str, Any] | None,
) -> Path:
    """Resolve the private root, allowing CLI flag, env, ctx.obj fallback."""
    candidates: list[Path] = []
    if private_root_arg is not None:
        candidates.append(private_root_arg)
    env_root = os.environ.get("FENRIX_PRIVATE_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root))
    if ctx_obj:
        for key in ("private_root", "data_root"):
            v = ctx_obj.get(key)
            if v is not None:
                candidates.append(Path(v) / "private")

    for cand in candidates:
        path = cand.resolve()
        if path.exists():
            return path
        # Last-resort: if path doesn't exist yet but is explicitly passed,
        # return the candidate's resolved path (parent must exist).
        if private_root_arg is not None:
            return path
    raise InvalidInputError("--private-root or FENRIX_PRIVATE_ROOT env required.")


def _enforce_private_subpath(target: Path, private_root: Path) -> Path:
    """Ensure target resolves under private_root AND outside tracked git."""
    target = target.resolve()
    private_root = private_root.resolve()
    if not target.is_relative_to(private_root):
        raise PrivacyFailureError(f"Output path is outside FENRIX_PRIVATE_ROOT ({private_root}).")
    # Git-tracked check (avoid writing private artifacts into repo)
    try:
        repo_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        repo_root_path = Path(repo_root).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        repo_root_path = None

    if repo_root_path and target.is_relative_to(repo_root_path):
        try:
            rel = target.relative_to(repo_root_path)
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", str(rel)],
                cwd=repo_root_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if tracked.returncode == 0:
                raise PrivacyFailureError("Output path is inside the tracked git repository.")
        except subprocess.SubprocessError:
            pass
    return target


def _atomic_write_json(file_path: Path, data: Any) -> None:
    """Atomic JSON write: write to .tmp.<pid> then replace.

    Cross-device renames are avoided by using `with_suffix` so the
    temporary file shares the target directory.
    """
    parent = file_path.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=file_path.name + ".", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(
                orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS, default=str)
            )
        os.replace(tmp_name, file_path)
    except Exception:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


def _write_text_atomic(file_path: Path, text: str) -> None:
    """Atomic text write used for human-readable summary lines."""
    parent = file_path.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=file_path.name + ".", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, file_path)
    except Exception:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


def _looks_like_private(value: Any) -> bool:
    """Best-effort check that a console summary string excludes private keys."""
    if isinstance(value, str):
        low = value.lower()
        for tok in _FORBIDDEN_CONSOLE_TOKENS:
            if tok in low:
                return True
    return False


def _default_artifact_path(private_root: Path, run_id: str | None, file_name: str) -> Path:
    """Resolve an artifact path under private_root/runs/<run_id>/private/."""
    rid = run_id or f"cli-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    return private_root / "runs" / rid / "private" / file_name


# ── 1) s3-transform ─────────────────────────────────────────────────


def _load_prices(prices_path: Path) -> list[Any]:
    """Load a JSON file of OHLCV records into a list of dicts."""
    try:
        data = json.loads(prices_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ExecutionFailureError("Failed to read prices file.") from exc
    if not isinstance(data, dict) or "records" not in data:
        raise InvalidInputError("Prices JSON must be an object with 'records' list.")
    records = data["records"]
    if not isinstance(records, list) or not records:
        raise InvalidInputError("Prices JSON must include a non-empty 'records' list.")
    return records


def _load_reference_returns(path: Path | None, n_records: int) -> list[float] | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(data, list):
        rets = data
    elif isinstance(data, dict) and isinstance(data.get("returns"), list):
        rets = data["returns"]
    else:
        return None
    if not rets:
        return None
    return (rets + [0.0] * n_records)[:n_records]


@click.command(name="s3-transform")
@click.option(
    "--private-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Private root (default: FENRIX_PRIVATE_ROOT env var).",
)
@click.option(
    "--variant",
    type=click.Choice(["s3a_daily_bucketed", "s3b_weekly_features", "s3c_block_features"]),
    required=True,
)
@click.option(
    "--prices",
    "prices_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to prices JSON (must contain 'records' list of OHLCV).",
)
@click.option("--market-reference", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--sector-reference", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--run-id", default="")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None)
def s3_transform(
    private_root: Path | None,
    variant: str,
    prices_path: Path,
    market_reference: Path | None,
    sector_reference: Path | None,
    run_id: str,
    output_path: Path | None,
) -> None:
    """Run an S3 feature-only transform (S3A / S3B / S3C).

    Reads OHLCV records from --prices, runs the chosen transform, writes
    a sanitized summary JSON to --output (or under private_root/runs/<id>/private/).
    """
    try:
        root = _resolve_private_root(private_root, click.get_current_context().obj)
        records_data = _load_prices(prices_path)
        from .transforms.feature_only import OhlcvRecord

        try:
            records = [OhlcvRecord(**r) for r in records_data]
        except (TypeError, ValueError) as exc:
            raise InvalidInputError("Invalid OHLCV record encountered in prices file.") from exc

        n = len(records)
        mkt = _load_reference_returns(market_reference, n)
        sec = _load_reference_returns(sector_reference, n)

        from .transforms.feature_only import (
            S3Variant,
            transform_s3a_daily_bucketed,
            transform_s3b_weekly_features,
            transform_s3c_block_features,
        )
        from .transforms.schemas import validate_feature_series

        if variant == "s3a_daily_bucketed":
            result = transform_s3a_daily_bucketed(records, market_returns=mkt, sector_returns=sec)
        elif variant == "s3b_weekly_features":
            result = transform_s3b_weekly_features(records, market_returns=mkt, sector_returns=sec)
        else:
            result = transform_s3c_block_features(records, market_returns=mkt, sector_returns=sec)

        validation = validate_feature_series(result.features, S3Variant(variant))
        if not validation.is_valid:
            raise InvalidInputError(f"Schema validation failed for {variant}: {validation.issues}")

        # Build sanitized summary JSON: NO per-row feature arrays exposed
        # at top level (caller can read the full .json file for features).
        summary = {
            "variant": variant,
            "series_id": result.series_id,
            "row_count": result.row_count,
            "release_marker": result.release_marker.value
            if hasattr(result.release_marker, "value")
            else str(result.release_marker),
            "parameter_hash": result.parameter_hash,
            "feature_schema_version": result.feature_schema_version,
            "validation_result": {
                "is_valid": validation.is_valid,
                "issues": validation.issues,
            },
            "warnings": result.warnings,
            "features": result.features,  # contains categorical only (no prices)
            "missing_periods": result.missing_periods,
            "forbidden_fields_detected": result.forbidden_fields_detected,
            "generated_at": datetime.now(UTC).isoformat(),
        }

        target = output_path or _default_artifact_path(
            root, run_id, f"{variant.split('_')[0]}_features.json"
        )
        target = _enforce_private_subpath(target, root)
        _atomic_write_json(target, summary)
        click.echo(
            f"s3-transform OK | variant={variant} | "
            f"rows={result.row_count} | "
            f"marker={summary['release_marker']} | "
            f"valid={validation.is_valid} | "
            f"param_hash={result.parameter_hash[:16]}"
        )
    except DomainIneligibleVariantError as exc:
        raise IneligibleVariantError(str(exc)) from exc
    except (InvalidInputError, PrivacyFailureError, IneligibleVariantError):
        raise
    except Exception as exc:
        # Sanitized fallback — never leak Python exception type or str(exc).
        raise ExecutionFailureError("s3-transform execution failed.") from exc


# ── 2) s3-attack ─────────────────────────────────────────────────────


def _load_candidate_universe(
    universe_path: Path,
    n_records: int,
    variant: str,
) -> dict[str, list[dict[str, Any]]]:
    """Load candidate universe and turn each candidate's price series into features.

    Returns: dict[candidate_id -> list[feature-dicts]].
    """
    try:
        data = json.loads(universe_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ExecutionFailureError("Failed to read candidate universe.") from exc
    if not isinstance(data, dict) or "candidates" not in data:
        raise InvalidInputError("Universe JSON must be an object with 'candidates' list.")

    from .transforms.feature_only import (
        OhlcvRecord,
        transform_s3b_weekly_features,
        transform_s3c_block_features,
    )

    candidates_out: dict[str, list[dict[str, Any]]] = {}
    for entry in data.get("candidates", []):
        cid = entry.get("candidate_id", "")
        prices = entry.get("prices", entry.get("returns", []))
        if not cid or not prices:
            continue
        # Build synthetic OHLCV records (no real OHLC; only close matters
        # for the S3 feature transforms).
        fake_records: list[OhlcvRecord] = []
        for _i, p in enumerate(prices[:n_records]):
            fake_records.append(
                OhlcvRecord(
                    date="",
                    open=p,
                    high=p * 1.01 if p else 1.0,
                    low=p * 0.99 if p else 1.0,
                    close=p,
                    volume=10000,
                )
            )
        try:
            if variant == "s3c_block_features":
                cand_result = transform_s3c_block_features(fake_records)
            else:
                cand_result = transform_s3b_weekly_features(fake_records)
            candidates_out[cid] = cand_result.features
        except Exception:
            continue
    return candidates_out


def _check_required(
    produced: list[dict[str, Any]],
    required_attacks: tuple[str, ...],
    required_ablations: tuple[str, ...],
    variant: str,
) -> list[str]:
    """Verify all required attack names + ablation groups are present."""
    missing: list[str] = []
    attacks_seen = {a.get("attack_name", "") for a in produced}
    ablations_seen = {a.get("ablation", "all") for a in produced}
    for req in required_attacks:
        if req not in attacks_seen:
            missing.append(f"attack={req} (variant={variant})")
    for req in required_ablations:
        if req not in ablations_seen:
            missing.append(f"ablation={req} (variant={variant})")
    return missing


@click.command(name="s3-attack")
@click.option("--private-root", type=click.Path(path_type=Path), default=None)
@click.option(
    "--variant",
    type=click.Choice(["s3b_weekly_features", "s3c_block_features"]),
    required=True,
    help="S3 variant. S3A is intentionally omitted (non-releasable).",
)
@click.option(
    "--source-features",
    "source_features_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Source features JSON (from `s3-transform`).",
)
@click.option(
    "--candidate-universe",
    "candidate_universe_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Candidate universe JSON (candidates: [{candidate_id, prices}]).",
)
@click.option(
    "--required-attacks",
    multiple=True,
    help="Attack names that MUST appear (e.g. exact, combined). Repeatable.",
)
@click.option(
    "--required-ablations",
    multiple=True,
    help="Ablation groups that MUST appear (e.g. direction, momentum).",
)
@click.option("--run-id", default="")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None)
def s3_attack(
    private_root: Path | None,
    variant: str,
    source_features_path: Path,
    candidate_universe_path: Path,
    required_attacks: tuple[str, ...],
    required_ablations: tuple[str, ...],
    run_id: str,
    output_path: Path | None,
) -> None:
    """Run categorical sequence re-identification attacks on S3 features.

    Reads source features + a candidate universe, generates features
    for each candidate, runs the full attack suite, canonicalizes
    results to CategoricalAttackEvidence, and writes an atomic JSON
    list (plus summary line).
    """
    try:
        root = _resolve_private_root(private_root, click.get_current_context().obj)
        assert_releasable_variant(variant)

        source_data = json.loads(source_features_path.read_text())
        if not isinstance(source_data, dict) or "features" not in source_data:
            raise InvalidInputError("Source-features JSON must include 'features'.")
        source_features = source_data["features"]

        # 252 trading days is a sensible default for fake-OHLCV construction.
        # Candidates without enough data points are silently skipped.
        n_records_hint = 252
        candidates_dict = _load_candidate_universe(candidate_universe_path, n_records_hint, variant)

        from .attacks.categorical_attacks import (
            categorical_attacks_to_canonical,
            run_s3_attack_suite,
        )

        attack_results = run_s3_attack_suite(source_features, candidates_dict, variant=variant)
        canonical = categorical_attacks_to_canonical(attack_results)
        serialized = [e.to_dict() for e in canonical]

        missing = _check_required(
            serialized,
            tuple(required_attacks) if required_attacks else (),
            tuple(required_ablations) if required_ablations else (),
            variant,
        )
        if missing:
            raise InvalidInputError("Required attacks/ablations missing: " + ", ".join(missing))

        target = output_path or _default_artifact_path(
            root, run_id, f"{variant.split('_')[0]}_attacks.json"
        )
        target = _enforce_private_subpath(target, root)
        payload = {
            "variant": variant,
            "attacks": serialized,
            "n_attacks": len(serialized),
            "n_candidates": len(candidates_dict),
            "produced_at": datetime.now(UTC).isoformat(),
        }
        _atomic_write_json(target, payload)
        # Summary: counts and hashes only — never per-attack rank data here.
        click.echo(
            f"s3-attack OK | variant={variant} | "
            f"n_attacks={len(serialized)} | "
            f"n_candidates={len(candidates_dict)} | "
            f"n_missing={len(missing)}"
        )
    except DomainIneligibleVariantError as exc:
        raise IneligibleVariantError(str(exc)) from exc
    except (InvalidInputError, PrivacyFailureError, IneligibleVariantError):
        raise
    except Exception as exc:
        raise ExecutionFailureError("s3-attack execution failed.") from exc


# ── 3) s3-assess ─────────────────────────────────────────────────────


SUPPORTED_ASSESS_VARIANTS = ("s3b_weekly_features", "s3c_block_features")


@click.command(name="s3-assess")
@click.option("--private-root", type=click.Path(path_type=Path), default=None)
@click.option(
    "--variant",
    type=click.Choice(list(SUPPORTED_ASSESS_VARIANTS) + ["s3a_daily_bucketed"]),
    required=True,
)
@click.option(
    "--attack-results",
    "attack_results_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Canonical attack evidence JSON (from `s3-attack`).",
)
@click.option(
    "--policy",
    "policy_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Policy YAML (top_k_fail, etc.). Overrides gate defaults if provided.",
)
@click.option("--run-id", default="")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None)
def s3_assess(
    private_root: Path | None,
    variant: str,
    attack_results_path: Path,
    policy_path: Path | None,
    run_id: str,
    output_path: Path | None,
) -> None:
    """Assess S3 attack evidence against the privacy gate policy.

    Eligibility check: S0/S1/S2/S3A exit code 5 (ineligible).
    PASS_CANDIDATE / REVIEW_REQUIRED -> exit 0. FAIL -> exit 3.
    """
    try:
        root = _resolve_private_root(private_root, click.get_current_context().obj)

        from .release.s3_gate import S3PrivacyGate

        gate = S3PrivacyGate()
        if policy_path is not None:
            try:
                import yaml as _yaml

                pol = _yaml.safe_load(policy_path.read_text()) or {}
                gate_kwargs: dict[str, Any] = {}
                for key in (
                    "top_k_fail",
                    "top_pct_fail",
                    "top_pct_review",
                    "min_universe_size",
                ):
                    if key in pol:
                        gate_kwargs[key] = pol[key]
                gate = S3PrivacyGate(**gate_kwargs)
            except Exception as exc:
                raise InvalidInputError(
                    "Policy load failed; ensure YAML has expected top_k/top_pct keys."
                ) from exc

        raw = json.loads(attack_results_path.read_text())
        if isinstance(raw, dict) and "attacks" in raw:
            evidence = raw["attacks"]
        elif isinstance(raw, list):
            evidence = raw
        else:
            raise InvalidInputError("Attack-results must be a list or {attacks: [...]}.")

        result = gate.evaluate(variant, evidence)

        # Map gate decision onto the CLI exit-code matrix.
        from .release.s3_gate import PrivacyDecision

        decision_value = (
            result.decision.value if hasattr(result.decision, "value") else str(result.decision)
        )
        # Incomplete evidence routes to exit 2 (invalid input), NOT exit 3
        # (privacy fail). Only the inner ineligibility check should map
        # to exit 5.
        if not result.is_complete:
            raise InvalidInputError(
                f"Attack evidence incomplete for {result.variant}: {result.blocking_reasons}"
            )
        if result.decision == PrivacyDecision.FAIL:
            # Route on the structured `is_ineligible` marker set by the
            # gate's eligibility guard, NOT via substring text matching.
            if result.is_ineligible:
                raise IneligibleVariantError(f"Variant {variant} is ineligible for release.")
            raise PrivacyFailureError(f"Gate FAIL for {variant}: {result.blocking_reasons}")

        # Sanitized summary: counts + reasons + gate hash. NO per-attack
        # rank data leaks to summary JSON.
        sanitized_evidence_summary = [
            {
                "attack_name": e.attack_type,
                "ablation_group": e.ablation_group,
                "in_top_10": e.in_top_10,
                "in_top_1_pct": e.in_top_1_pct,
            }
            for e in result.evidence
        ]
        payload = {
            "variant": result.variant,
            "decision": decision_value,
            "gate_hash": result.gate_hash,
            "policy_hash": gate.policy_hash,
            "is_final": result.is_final,
            "blocking_reasons": result.blocking_reasons,
            "review_reasons": result.review_reasons,
            "warnings": result.warnings,
            "evidence_summary": sanitized_evidence_summary,
            "evidence_count": len(result.evidence),
            "produced_at": datetime.now(UTC).isoformat(),
        }
        target = output_path or _default_artifact_path(
            root, run_id, f"{variant.split('_')[0]}_assess.json"
        )
        target = _enforce_private_subpath(target, root)
        _atomic_write_json(target, payload)
        click.echo(
            f"s3-assess | variant={variant} | decision={decision_value} | "
            f"gate_hash={result.gate_hash[:16]} | "
            f"blocking={len(result.blocking_reasons)} | "
            f"review={len(result.review_reasons)}"
        )
    except DomainIneligibleVariantError as exc:
        raise IneligibleVariantError(str(exc)) from exc
    except (InvalidInputError, PrivacyFailureError, IneligibleVariantError):
        raise
    except Exception as exc:
        raise ExecutionFailureError("s3-assess execution failed.") from exc


# ── 4) evaluate-submission ──────────────────────────────────────────


def _parse_csv_ints(name: str, raw: str) -> list[int]:
    try:
        values = [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError as exc:
        raise InvalidInputError(f"--{name} must be comma-separated integers.") from exc
    return values


def _is_bool_like(v: int) -> bool:
    """Return True for a value that, although an int, was typed as a Python bool.

    `bool` is a subclass of `int` in Python – we want strict integer-only input.
    """
    return isinstance(v, bool)


def _validate_submission_shapes(periods: list[int], actions: list[int]) -> None:
    if len(periods) != len(actions):
        raise InvalidInputError(
            "[SHAPE_MISMATCH] "
            f"Counts differ: periods ({len(periods)}) vs. actions ({len(actions)})."
        )
    if any(_is_bool_like(a) for a in actions):
        raise InvalidInputError(
            "[BOOL_ACTION] Boolean actions are not accepted; use integer 0 or 1."
        )
    if any(a not in (0, 1) for a in actions):
        raise InvalidInputError("[BINARY_VIOLATION] Binary actions must be 0 or 1 only.")
    if not periods:
        raise InvalidInputError("[EMPTY_SUBMISSION] Submission must include at least one decision.")
    if len(set(periods)) != len(periods):
        raise InvalidInputError(
            "[DUPLICATE_PERIOD] Relative periods must be unique; duplicates detected."
        )
    for idx, p in enumerate(periods):
        if not isinstance(p, int) or isinstance(p, bool):
            raise InvalidInputError(
                f"[SHAPE_MISMATCH] Period at index {idx} must be a plain integer."
            )
    for i in range(1, len(periods)):
        if periods[i] <= periods[i - 1]:
            raise InvalidInputError(
                f"[SHAPE_MISMATCH] Relative periods must be strictly increasing at index {i}."
            )


def _check_finite(value: float, label: str) -> None:
    if not math.isfinite(value):
        raise InvalidInputError(f"[NON_FINITE] Configuration value {label} must be finite.")


@click.command(name="evaluate-submission")
@click.option("--release-id", required=True)
@click.option("--run-id", required=True)
@click.option("--submission-id", required=True)
@click.option(
    "--relative-periods",
    required=True,
    help="Comma-separated relative period integers (strictly increasing, unique).",
)
@click.option(
    "--binary-actions",
    required=True,
    help="Comma-separated binary decisions (0=cash, 1=long).",
)
@click.option(
    "--private-truth",
    "private_truth_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Private truth JSON with period_returns list.",
)
@click.option("--private-root", type=click.Path(path_type=Path), default=None)
@click.option("--transaction-cost", type=float, default=0.001)
@click.option("--execution-lag", type=int, default=1)
@click.option("--in-sample-end", type=int, default=None)
@click.option("--run-id-output", "run_id_output", default="")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None)
def evaluate_submission(
    release_id: str,
    run_id: str,
    submission_id: str,
    relative_periods: str,
    binary_actions: str,
    private_truth_path: Path,
    private_root: Path | None,
    transaction_cost: float,
    execution_lag: int,
    in_sample_end: int | None,
    run_id_output: str,
    output_path: Path | None,
) -> None:
    """Evaluate a binary trade submission against the private return series.

    The CLI acknowledges no raw returns, dates, or per-period P&L. Only
    aggregate sanitized metrics are persisted.

    Failure modes (Phase 5A close-out):

    * Missing/incompatible submission shapes → exit 2
    * Empty private-truth series → exit 2
    * NaN/inf configuration → exit 2
    * Zero evaluable decisions after lag alignment → exit 2
    """
    # Validate configuration literals BEFORE doing anything else.
    if not release_id or not run_id or not submission_id:
        raise InvalidInputError("--release-id, --run-id, --submission-id are required.")
    _check_finite(transaction_cost, "transaction-cost")
    if execution_lag < 0:
        raise InvalidInputError("--execution-lag must be non-negative.")

    try:
        root = _resolve_private_root(private_root, click.get_current_context().obj)
        periods = _parse_csv_ints("relative-periods", relative_periods)
        actions = _parse_csv_ints("binary-actions", binary_actions)
        _validate_submission_shapes(periods, actions)

        try:
            truth = json.loads(private_truth_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise ExecutionFailureError("Failed to read private truth file.") from exc
        private_returns = (
            truth.get("period_returns")
            if isinstance(truth, dict)
            else truth
            if isinstance(truth, list)
            else None
        )
        if not isinstance(private_returns, list) or not private_returns:
            raise InvalidInputError(
                "[EMPTY_TRUTH] Private truth must contain a non-empty 'period_returns' list."
            )
        if any(not isinstance(r, (int, float)) or not math.isfinite(r) for r in private_returns):
            raise InvalidInputError(
                "[NON_FINITE] Private returns contain NaN or non-numeric values."
            )

        # Check that all submitted periods are within the available
        # private-returns range. Periods are used as positional indices
        # into the returns array, so they must be < len(private_returns).
        for p in periods:
            if p < 0 or p >= len(private_returns):
                raise InvalidInputError(
                    "[UNKNOWN_PERIOD] Period "
                    f"{p} is outside available range [0, {len(private_returns) - 1}]."
                )

        from .evaluation.backtest import (
            EvaluationRequest,
            PrivateBacktestEvaluator,
        )

        is_end = in_sample_end if in_sample_end is not None else max(0, len(private_returns) // 2)
        try:
            evaluator = PrivateBacktestEvaluator(
                actual_private_returns=private_returns,
                in_sample_end=is_end,
                transaction_cost=transaction_cost,
                execution_lag=execution_lag,
            )
        except Exception as exc:
            raise InvalidInputError(
                "Failed to initialize evaluator with the given parameters."
            ) from exc

        request = EvaluationRequest(
            run_id=run_id,
            release_id=release_id,
            model_submission_id=submission_id,
            relative_periods=periods,
            binary_actions=actions,
        )
        result = evaluator.evaluate(request)

        # Check validation errors BEFORE zero-decisions check so that
        # meaningful error tokens like [UNKNOWN_PERIOD] take priority
        # over the generic zero-decisions fallback.
        if not result.is_valid:
            raise InvalidInputError(f"Submission evaluation failed: {result.validation_errors}")

        # Failure-mode post-condition: zero evaluable decisions means the
        # evaluator produced no signal and the submission must NOT pass.
        # We check explicit decision-count fields first, falling back to
        # an "all metrics absent" sentinel only if neither is present.
        evaluable_count = getattr(result, "evaluable_decision_count", None)
        n_decisions = getattr(result, "n_decisions", None)
        trade_rate = getattr(result, "trade_rate", None)
        hit_rate = getattr(result, "hit_rate", None)
        if evaluable_count is not None and evaluable_count == 0:
            raise InvalidInputError(
                "[ZERO_DECISIONS] Submission produced zero evaluable decisions after lag alignment."
            )
        if n_decisions is not None and n_decisions == 0:
            raise InvalidInputError(
                "[ZERO_DECISIONS] Submission produced zero lag-aligned decisions to evaluate."
            )
        # Sentinel: if the evaluator exposes neither a count nor any
        # metric, refuse to write. Belt-and-braces for evaluators that
        # do not implement `evaluable_decision_count`.
        if (
            evaluable_count is None
            and n_decisions is None
            and trade_rate is None
            and hit_rate is None
        ):
            raise InvalidInputError(
                "[ZERO_DECISIONS] Evaluator returned no decision-count metric and no trade/hit rate; refusing."
            )

        # Defense-in-depth: refuse to write sanitized output if the
        # evaluator returned no usable metrics.
        sanitized = result.to_sanitized_dict()
        if not sanitized or all(
            v is None or v == "" or (isinstance(v, (list, dict)) and len(v) == 0)
            for v in sanitized.values()
        ):
            raise InvalidInputError("Evaluator returned an empty metric set; refusing to write.")

        target = output_path or _default_artifact_path(
            root,
            run_id_output or run_id,
            f"evaluate_{submission_id}.json",
        )
        target = _enforce_private_subpath(target, root)
        _atomic_write_json(target, sanitized)
        click.echo(
            f"evaluate-submission OK | submission={submission_id} | "
            f"trade_rate={result.trade_rate} | "
            f"hit_rate={result.hit_rate} | "
            f"evaluator_hash={result.evaluator_hash[:16]}"
        )
    except (InvalidInputError, PrivacyFailureError, ExecutionFailureError):
        raise
    except Exception as exc:
        raise ExecutionFailureError("evaluate-submission execution failed.") from exc


# ── 5) atlas-harvest ────────────────────────────────────────────────


@click.command(name="atlas-harvest")
@click.option("--private-root", type=click.Path(path_type=Path), default=None)
@click.option(
    "--document",
    "document_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
)
@click.option(
    "--metadata", "metadata_path", type=click.Path(exists=True, path_type=Path), default=None
)
@click.option("--run-id", default="")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None)
def atlas_harvest(
    private_root: Path | None,
    document_path: Path,
    metadata_path: Path | None,
    run_id: str,
    output_path: Path | None,
) -> None:
    """Harvest candidate identity values from a document.

    All harvested candidates are explicitly marked
    is_auto_accepted=False. The CLI does not mutate the registry.
    """
    try:
        root = _resolve_private_root(private_root, click.get_current_context().obj)
        text = document_path.read_text(encoding="utf-8", errors="replace")
        metadata: dict[str, Any] = {}
        if metadata_path is not None:
            try:
                if metadata_path.suffix.lower() in (".yaml", ".yml"):
                    import yaml as _yaml

                    metadata = _yaml.safe_load(metadata_path.read_text()) or {}
                else:
                    metadata = json.loads(metadata_path.read_text())
                if not isinstance(metadata, dict):
                    metadata = {}
            except Exception as exc:
                raise InvalidInputError("Metadata YAML/JSON could not be parsed.") from exc

        from .masking.harvesting import AtlasHarvester

        harvester = AtlasHarvester()
        result = harvester.harvest(text, metadata=metadata)

        # Defense in depth: ensure no candidate is auto-accepted.
        for c in result.candidates:
            if c.is_auto_accepted:
                raise PrivacyFailureError(
                    "Harvester returned an auto-accepted candidate; aborting."
                )

        payload = {
            "document": str(document_path),
            "total_harvested": result.total_harvested,
            "by_category": result.by_category,
            "warnings": result.warnings,
            "candidates": [
                {
                    "value": c.value,
                    "category": c.category,
                    "source": c.source,
                    "start": c.start,
                    "end": c.end,
                    "context": c.context,
                    "confidence": c.confidence,
                    "is_auto_accepted": c.is_auto_accepted,
                }
                for c in result.candidates
            ],
            "registry_mutation_count": 0,
            "automatic_acceptance_count": 0,
            "automatic_promotion_count": 0,
            "produced_at": datetime.now(UTC).isoformat(),
        }

        target = output_path or _default_artifact_path(root, run_id, "atlas_candidates.json")
        target = _enforce_private_subpath(target, root)
        _atomic_write_json(target, payload)
        click.echo(
            f"atlas-harvest OK | total={result.total_harvested} | "
            f"categories={sorted(result.by_category.keys())}"
        )
    except (InvalidInputError, PrivacyFailureError, IneligibleVariantError):
        raise
    except Exception as exc:
        raise ExecutionFailureError("atlas-harvest execution failed.") from exc


# ── Public re-exports ───────────────────────────────────────────────

__all__ = [
    "atlas_harvest",
    "evaluate_submission",
    "s3_assess",
    "s3_attack",
    "s3_transform",
]
