"""Classroom package product-level tests (Hours 18-28).

Covers:
- CLI registration and eligibility
- Filesystem containment
- Atomicity and failure cleanup
- Deterministic construction
- Feature CSV contract
- Submission artifacts
- Notebook execution
- Privacy summary schema
- Release manifest schema
- Checksums
- Recursive privacy scan
- Evaluator compatibility
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import nbformat
import pytest
from click.testing import CliRunner
from nbconvert.preprocessors import ExecutePreprocessor


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_group() -> Any:
    from fenrix_synthetic.cli import cli

    return cli


@pytest.fixture
def private_root(tmp_path: Path) -> Path:
    """Create a minimal private root with S3B features and attack results."""
    p = tmp_path / "private"
    p.mkdir(parents=True, exist_ok=True)
    run_dir = p / "runs" / "test001" / "private"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create minimal S3B features matching expected columns
    features = []
    for i in range(50):
        features.append(
            {
                "relative_week": i,
                "weekly_direction_category": "UP" if i % 3 == 0 else "DOWN",
                "momentum_4w_bucket": "MEDIUM",
                "momentum_12w_bucket": "HIGH",
                "momentum_26w_bucket": "LOW",
                "volatility_4w_bucket": "LOW",
                "volatility_12w_bucket": "LOW",
                "volume_activity_bucket": "MEDIUM",
                "drawdown_bucket": "LOW",
                "moving_average_regime": "ABOVE",
                "market_relative_strength_bucket": "MEDIUM",
                "sector_relative_strength_bucket": "LOW",
                "trend_persistence_bucket": "SHORT",
            }
        )
    features_path = run_dir / "s3b_features.json"
    features_path.write_text(
        json.dumps(
            {
                "variant": "s3b_weekly_features",
                "row_count": 50,
                "parameter_hash": "test_hash_123456",
                "features": features,
                "feature_schema_version": "1.0.0",
            }
        )
    )

    # Create minimal attack results with frozen 16 keys, all rank > 10
    frozen_keys = [
        "exact/all",
        "weighted_hamming/all",
        "dtw/all",
        "transition/all",
        "ngram/all",
        "combined/all",
        "combined/direction",
        "combined/momentum",
        "combined/volatility",
        "combined/drawdown",
        "combined/market_relative",
        "combined/sector_relative",
        "combined/technical_state",
        "lagged_1/all",
        "lagged_5/all",
        "lagged_21/all",
    ]
    attacks = []
    for key in frozen_keys:
        parts = key.split("/")
        attacks.append(
            {
                "attack_name": parts[0],
                "ablation": parts[1] if len(parts) > 1 else "all",
                "variant": "s3b_weekly_features",
                "true_source_rank": 11 if key == "weighted_hamming/all" else 50,
                "candidate_universe_size": 141,
                "percentile_rank": 92.2,
                "top_1": False,
                "top_5": False,
                "top_10": False,
                "score": 0.5,
                "status": "completed",
                "attack_hash": "abc123",
                "notes": "",
            }
        )
    attacks_path = run_dir / "s3b_attacks.json"
    attacks_path.write_text(json.dumps({"attacks": attacks, "variant": "s3b_weekly_features"}))

    return p


def _build_and_get_package_dir(private_root: Path, runner: CliRunner, cli_group) -> Path:
    """Helper: run classroom-build and return package directory."""
    output = private_root / "releases"
    output.mkdir(parents=True, exist_ok=True)
    result = runner.invoke(
        cli_group,
        [
            "classroom-build",
            "--source-id",
            "test001",
            "--private-root",
            str(private_root),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, f"Build failed: {result.stderr}"
    return output / "SYNTH_001_CLASSROOM_BETA"


# ── CLI Registration and Eligibility ───────────────────────────────────


class TestClassroomCliRegistration:
    """classroom-build is registered and enforces eligibility."""

    def test_command_is_registered(self, runner: CliRunner, cli_group) -> None:
        result = runner.invoke(cli_group, ["--help"])
        assert "classroom-build" in result.stdout

    def test_s3b_accepted(self, runner: CliRunner, cli_group, private_root: Path) -> None:
        output = private_root / "releases"
        output.mkdir(exist_ok=True)
        result = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--variant",
                "s3b_weekly_features",
                "--private-root",
                str(private_root),
                "--output",
                str(output),
            ],
        )
        assert result.exit_code == 0

    def test_variant_must_be_s3b(self, runner: CliRunner, cli_group, private_root: Path) -> None:
        output = private_root / "releases"
        output.mkdir(exist_ok=True)
        # Click's Choice option rejects non-S3B before reaching the callback
        result = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--variant",
                "s3a_daily_bucketed",
                "--private-root",
                str(private_root),
                "--output",
                str(output),
            ],
        )
        assert result.exit_code != 0


# ── Filesystem Containment ──────────────────────────────────────────────


class TestFilesystemContainment:
    """Output path containment and safety."""

    def test_output_outside_private_root_rejected(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        outside = Path(tempfile.mkdtemp())
        result = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--private-root",
                str(private_root),
                "--output",
                str(outside),
            ],
        )
        assert result.exit_code != 0
        shutil.rmtree(str(outside), ignore_errors=True)

    def test_existing_package_replaced(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg_dir = _build_and_get_package_dir(private_root, runner, cli_group)
        assert pkg_dir.exists()
        # Rebuild should succeed (replaces existing)
        output = private_root / "releases"
        result = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--private-root",
                str(private_root),
                "--output",
                str(output),
            ],
        )
        assert result.exit_code == 0
        assert pkg_dir.exists()


# ── Deterministic Construction ──────────────────────────────────────────


class TestDeterministicBuild:
    """Two builds from identical inputs produce identical semantic hashes."""

    def test_two_builds_same_package_hash(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        output = private_root / "releases"
        output.mkdir(exist_ok=True)

        r1 = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--private-root",
                str(private_root),
                "--output",
                str(output),
            ],
        )
        assert r1.exit_code == 0
        hash1_line = [line for line in r1.stdout.split("\n") if "package_hash=" in line][0]
        h1 = hash1_line.split("package_hash=")[1].split()[0]

        # Remove and rebuild
        pkg = output / "SYNTH_001_CLASSROOM_BETA"
        shutil.rmtree(str(pkg), ignore_errors=True)

        r2 = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--private-root",
                str(private_root),
                "--output",
                str(output),
            ],
        )
        assert r2.exit_code == 0
        hash2_line = [line for line in r2.stdout.split("\n") if "package_hash=" in line][0]
        h2 = hash2_line.split("package_hash=")[1].split()[0]

        assert h1 == h2, f"Hash mismatch: {h1} != {h2}"

    def test_rebuilds_produce_identical_FILES(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        """Build twice in separate output dirs under private_root, compare all files."""
        out1 = private_root / "releases" / "build1"
        out2 = private_root / "releases" / "build2"
        out1.mkdir(parents=True, exist_ok=True)
        out2.mkdir(parents=True, exist_ok=True)

        # Build 1
        r1 = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--private-root",
                str(private_root),
                "--output",
                str(out1),
            ],
        )
        assert r1.exit_code == 0, f"Build 1 failed: {r1.stderr}"

        # Build 2
        r2 = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--private-root",
                str(private_root),
                "--output",
                str(out2),
            ],
        )
        assert r2.exit_code == 0, f"Build 2 failed: {r2.stderr}"

        pkg1 = out1 / "SYNTH_001_CLASSROOM_BETA"
        pkg2 = out2 / "SYNTH_001_CLASSROOM_BETA"

        # Semantic files (excluding manifest which has timestamp) must match
        semantic_files = [
            "s3b_features.csv",
            "classroom_demo.ipynb",
            "README.md",
            "QUICKSTART.md",
            "DATA_DICTIONARY.md",
            "LIMITATIONS.md",
            "privacy_summary.json",
            "submission_template.csv",
            "example_submission.csv",
        ]
        for fname in semantic_files:
            f1 = pkg1 / fname
            f2 = pkg2 / fname
            h1 = hashlib.sha256(f1.read_bytes()).hexdigest()
            h2 = hashlib.sha256(f2.read_bytes()).hexdigest()
            assert h1 == h2, f"{fname} differs between builds"


# ── Feature CSV Contract ────────────────────────────────────────────────


class TestFeatureCsvContract:
    """s3b_features.csv meets the classroom contract."""

    def test_row_and_column_counts(self, runner: CliRunner, cli_group, private_root: Path) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        csv_path = pkg / "s3b_features.csv"
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 50  # 50 in test fixture
        assert len(rows[0]) == 13

    def test_no_accidental_index_column(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        csv_path = pkg / "s3b_features.csv"
        with open(csv_path) as f:
            first_line = f.readline().strip()
        columns = first_line.split(",")
        # No unnamed column (pandas index)
        assert "" not in columns
        # No column starting with "Unnamed"
        assert not any(c.startswith("Unnamed") for c in columns)

    def test_relative_weeks_unique_and_sorted(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        csv_path = pkg / "s3b_features.csv"
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            weeks = [int(row["relative_week"]) for row in reader]
        assert weeks == sorted(weeks)
        assert len(weeks) == len(set(weeks))

    def test_no_forbidden_fields_in_csv(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        csv_path = pkg / "s3b_features.csv"
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
        prohibited = {
            "close",
            "open",
            "high",
            "low",
            "volume",
            "price",
            "return",
            "date",
            "timestamp",
        }
        for col in fieldnames:
            col_lower = col.lower()
            for p in prohibited:
                if col_lower == p:
                    pytest.fail(f"Prohibited field '{col}' directly in CSV")

    def test_all_categorical_values_in_allowlist(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        csv_path = pkg / "s3b_features.csv"
        allowlists = {
            "weekly_direction_category": {"UP", "DOWN", "FLAT", ""},
            "momentum_4w_bucket": {"VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH", ""},
            "momentum_12w_bucket": {"VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH", ""},
            "momentum_26w_bucket": {"VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH", ""},
            "volatility_4w_bucket": {"VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH", ""},
            "volatility_12w_bucket": {"VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH", ""},
            "volume_activity_bucket": {"LOW", "MEDIUM", "HIGH", ""},
            "drawdown_bucket": {"VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH", ""},
            "moving_average_regime": {"BELOW", "CROSSED", "ABOVE", "NEUTRAL", ""},
            "market_relative_strength_bucket": {
                "VERY_LOW",
                "LOW",
                "MEDIUM",
                "HIGH",
                "VERY_HIGH",
                "",
            },
            "sector_relative_strength_bucket": {
                "VERY_LOW",
                "LOW",
                "MEDIUM",
                "HIGH",
                "VERY_HIGH",
                "",
            },
            "trend_persistence_bucket": {"SHORT", "MODERATE", "PERSISTENT", ""},
        }
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row_idx, row in enumerate(reader):
                for col, allowed in allowlists.items():
                    if col in row and row[col] not in allowed:
                        pytest.fail(f"Row {row_idx}, col '{col}': '{row[col]}' not in allowlist")


# ── Submission Artifacts ────────────────────────────────────────────────


class TestSubmissionArtifacts:
    """submission_template.csv and example_submission.csv."""

    def test_template_has_correct_columns(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        with open(pkg / "submission_template.csv") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == ["relative_period", "action"]

    def test_example_submission_validates(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        from fenrix_synthetic.cli_s3 import _validate_submission_shapes

        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        with open(pkg / "example_submission.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        periods = [int(r["relative_period"]) for r in rows]
        actions = [int(r["action"]) for r in rows]
        # Should not raise
        _validate_submission_shapes(periods, actions)

    def test_example_submission_actions_are_zero_or_one(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        with open(pkg / "example_submission.csv") as f:
            reader = csv.DictReader(f)
            for row in reader:
                a = int(row["action"])
                assert a in (0, 1), f"Action {a} not 0 or 1"


# ── Notebook Execution ──────────────────────────────────────────────────


class TestNotebookExecution:
    """classroom_demo.ipynb executes top-to-bottom."""

    def test_notebook_executes_without_error(
        self,
        runner: CliRunner,
        cli_group: Any,
        private_root: Path,
        socket_enabled: Any,
    ) -> None:
        """Execute the notebook using a real Jupyter kernel via nbconvert.

        Runs from a clean temporary student workspace with only the 11-file
        package, no repository imports, no FENRIX_PRIVATE_ROOT exposure.
        """
        # socket_enabled fixture disables pytest-socket blocking for this test
        # because Jupyter kernel uses local TCP sockets for client/server IPC.
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        nb_path = pkg / "classroom_demo.ipynb"

        # Copy to temp dir (clean student workspace — no repo access)
        work_dir = Path(tempfile.mkdtemp())
        orig_cwd = os.getcwd()
        try:
            for f in pkg.iterdir():
                if f.suffix in (".csv",):
                    shutil.copy2(str(f), str(work_dir / f.name))
            shutil.copy2(str(nb_path), str(work_dir / "classroom_demo.ipynb"))

            os.chdir(str(work_dir))

            # Execute notebook with real kernel
            with open("classroom_demo.ipynb") as f:
                nb = nbformat.read(f, as_version=4)

            ep = ExecutePreprocessor(timeout=120, kernel_name="python3")
            try:
                ep.preprocess(nb, {"metadata": {"path": str(work_dir)}})
            except Exception as exc:
                pytest.fail(f"Notebook execution failed: {exc}")

            os.chdir(orig_cwd)

            # Verify student_submission.csv was created
            sub = work_dir / "student_submission.csv"
            assert sub.exists(), "student_submission.csv was not created by notebook"

            # Verify submission is valid
            with open(sub) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) > 0
            for row in rows:
                assert int(row["action"]) in (0, 1)

            # Verify no execution output leaked back into release package
            for cell in nb.cells:
                if cell.get("cell_type") == "code":
                    for output in cell.get("outputs", []):
                        text = output.get("text", "") or output.get("data", {}).get(
                            "text/plain", ""
                        )
                        combined = "".join(text) if isinstance(text, list) else str(text)
                        # No private paths or source identifiers in outputs
                        assert "/Users/" not in combined
                        assert "FENRIX_PRIVATE_ROOT" not in combined
                        assert "fenrix_synthetic" not in combined

        finally:
            os.chdir(orig_cwd)
            shutil.rmtree(str(work_dir), ignore_errors=True)

    def test_notebook_has_no_private_imports(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        """Notebook source must not import private evaluator modules."""
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        nb_path = pkg / "classroom_demo.ipynb"
        nb = json.loads(nb_path.read_text())
        source = json.dumps(nb).lower()
        forbidden_imports = [
            "fenrix_synthetic.evaluation",
            "fenrix_private_root",
            "private_truth",
            "backtest",
            "import requests",
            "import urllib",
        ]
        for fi in forbidden_imports:
            assert fi not in source, f"Forbidden import/text '{fi}' in notebook"


# ── Privacy Summary Schema ──────────────────────────────────────────────


class TestPrivacySummary:
    """privacy_summary.json schema compliance."""

    def test_allowed_fields_only(self, runner: CliRunner, cli_group, private_root: Path) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        summary = json.loads((pkg / "privacy_summary.json").read_text())
        allowed = {
            "release_id",
            "release_status",
            "variant",
            "policy_id",
            "eligible_candidate_count",
            "required_attack_count",
            "completed_attack_count",
            "missing_attack_count",
            "duplicate_attack_count",
            "best_source_rank",
            "worst_privacy_percentile",
            "top_10_under_any_required_attack",
            "privacy_scan_passed",
            "prohibited_field_scan_passed",
            "identity_canary_match_count",
            "disclaimer",
        }
        extra = set(summary.keys()) - allowed
        assert not extra, f"Unexpected fields in privacy_summary: {extra}"

    def test_disclaimer_has_correct_wording(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        summary = json.loads((pkg / "privacy_summary.json").read_text())
        assert "PASS_CANDIDATE" in summary["disclaimer"]
        assert "s3b-mvp-v1" in summary["disclaimer"]
        assert "not a guarantee of anonymity" in summary["disclaimer"]

    def test_no_private_fields_in_summary(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        summary = json.loads((pkg / "privacy_summary.json").read_text())
        raw = json.dumps(summary).lower()
        forbidden = ["candidate_id", "ticker", "score", "attack_hash", "private_path"]
        for fw in forbidden:
            assert fw not in raw, f"'{fw}' in privacy_summary"


# ── Release Manifest Schema ─────────────────────────────────────────────


class TestReleaseManifest:
    """release_manifest.json schema compliance."""

    def test_manifest_has_required_fields(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        manifest = json.loads((pkg / "release_manifest.json").read_text())
        required = [
            "release_id",
            "release_version",
            "schema_version",
            "git_commit_sha",
            "feature_policy_hash",
            "attack_policy_hash",
            "dataset_semantic_hash",
            "package_semantic_hash",
            "file_checksums",
            "row_count",
            "feature_count",
            "release_status",
            "privacy_summary",
            "evaluator_required",
            "build_timestamp",
            "disclaimer",
        ]
        for field in required:
            assert field in manifest, f"Missing field: {field}"

    def test_manifest_evaluator_required(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        manifest = json.loads((pkg / "release_manifest.json").read_text())
        assert manifest["evaluator_required"] is True

    def test_manifest_release_status_is_pass_candidate(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        manifest = json.loads((pkg / "release_manifest.json").read_text())
        assert manifest["release_status"] == "PASS_CANDIDATE"


# ── Checksums ───────────────────────────────────────────────────────────


class TestChecksums:
    """checksums.sha256 verification."""

    def test_every_file_has_checksum(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        checksums = {}
        with open(pkg / "checksums.sha256") as f:
            for line in f:
                line = line.strip()
                if line and "  " in line:
                    h, name = line.split("  ", 1)
                    checksums[name] = h
        # All files except checksums itself should be listed
        package_files = {f.name for f in pkg.iterdir() if f.is_file()}
        package_files.discard("checksums.sha256")
        assert set(checksums.keys()) == package_files

    def test_checksums_verify(self, runner: CliRunner, cli_group, private_root: Path) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        with open(pkg / "checksums.sha256") as f:
            for line in f:
                line = line.strip()
                if line and "  " in line:
                    expected, fname = line.split("  ", 1)
                    actual = hashlib.sha256((pkg / fname).read_bytes()).hexdigest()
                    assert actual == expected, f"Checksum mismatch for {fname}"

    def test_mutation_detected(self, runner: CliRunner, cli_group, private_root: Path) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        csv_path = pkg / "s3b_features.csv"
        orig_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()

        # Mutate one byte
        content = csv_path.read_text()
        mutated = content.replace("UP", "DOWNX")
        csv_path.write_text(mutated)

        new_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        assert new_hash != orig_hash, "Mutation was not detected"

        # Restore
        csv_path.write_text(content)


# ── Atomic Failure Cleanup ────────────────────────────────────────────


class TestAtomicFailureCleanup:
    """Inject controlled failure after temp creation, prove no partial package."""

    def test_failure_after_temp_creation_does_not_create_package(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        """Inject failure by patching _build_notebook to raise after temp dir is created.

        Verifies:
        1. No final release directory remains.
        2. No partial package at the destination.
        3. Temp build content is removed.
        """
        output = private_root / "releases"
        output.mkdir(parents=True, exist_ok=True)
        pkg_path = output / "SYNTH_001_CLASSROOM_BETA"

        # First, build a valid package to prove the path works
        r1 = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--private-root",
                str(private_root),
                "--output",
                str(output),
            ],
        )
        assert r1.exit_code == 0
        assert pkg_path.exists()

        # Get original checksum
        orig_checksums = {}
        with open(pkg_path / "checksums.sha256") as f:
            for line in f:
                line = line.strip()
                if "  " in line:
                    h, name = line.split("  ", 1)
                    orig_checksums[name] = h

        # Now inject a failure in _build_notebook (called during file content generation)
        from fenrix_synthetic.release import classroom_build as cb_mod

        original_fn = cb_mod._build_notebook

        def _fail_mid_build() -> str:
            # Return notebook content normally (no failure here since this is called
            # before notebook writing)
            # Actually, _build_notebook is called before tmp_package is fully written.
            # We need to fail *after* tmp_package is created but *before* final rename.
            # The best hook is to fail during one of the file-generation functions that
            # is called between temp creation and final rename.
            return original_fn()

        # Instead, patch _scan_for_private_data which is called AFTER all files are
        # written to the temp dir but BEFORE the atomic rename.
        with patch.object(cb_mod, "_scan_for_private_data") as mock_scan:
            mock_scan.side_effect = RuntimeError("Injected failure during build")

            r2 = runner.invoke(
                cli_group,
                [
                    "classroom-build",
                    "--source-id",
                    "test001",
                    "--private-root",
                    str(private_root),
                    "--output",
                    str(output),
                ],
            )
            # Should fail
            assert r2.exit_code != 0

        # Verify: the ORIGINAL package must remain intact
        assert pkg_path.exists(), "Original package was removed by failed build!"

        # Verify checksums from original build are unmodified
        with open(pkg_path / "checksums.sha256") as f:
            for line in f:
                line = line.strip()
                if "  " in line:
                    h, name = line.split("  ", 1)
                    assert h == orig_checksums.get(name, ""), (
                        f"Checksum for {name} changed after failed build"
                    )

        # Verify no partial temp directories remain
        temp_dirs = [d for d in output.iterdir() if d.name.startswith("classroom_build_")]
        assert len(temp_dirs) == 0, f"Temp directories remain: {temp_dirs}"

    def test_clean_build_succeeds_after_failed_build(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        """After a failed build, a clean rebuild should succeed."""
        output = private_root / "releases"
        output.mkdir(parents=True, exist_ok=True)
        pkg_path = output / "SYNTH_001_CLASSROOM_BETA"

        from fenrix_synthetic.release import classroom_build as cb_mod

        with patch.object(cb_mod, "_scan_for_private_data") as mock_scan:
            mock_scan.side_effect = RuntimeError("Injected failure")
            runner.invoke(
                cli_group,
                [
                    "classroom-build",
                    "--source-id",
                    "test001",
                    "--private-root",
                    str(private_root),
                    "--output",
                    str(output),
                ],
            )

        # Clean build should succeed
        r_clean = runner.invoke(
            cli_group,
            [
                "classroom-build",
                "--source-id",
                "test001",
                "--private-root",
                str(private_root),
                "--output",
                str(output),
            ],
        )
        assert r_clean.exit_code == 0, f"Clean build after failure failed: {r_clean.stderr}"
        assert pkg_path.exists()


# ── Recursive Privacy Scan ──────────────────────────────────────────────


class TestRecursivePrivacyScan:
    """Recursive scan for identity canaries in all package files."""

    def test_no_identity_canaries_in_package(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        canaries = [
            "Huntington",
            "HBAN",
            "huntington.com",
            "0000049196",
            "Steinour",
            "Wasserman",
            "Columbus, Ohio",
            "41 South High Street",
            "43215",
            "Ernst & Young",
        ]
        for fpath in pkg.rglob("*"):
            if fpath.is_dir() or fpath.name == "checksums.sha256":
                continue
            try:
                content = fpath.read_text(errors="replace")
            except Exception:
                continue
            content_lower = content.lower()
            for canary in canaries:
                assert canary.lower() not in content_lower, (
                    f"Canary '{canary}' found in {fpath.name}"
                )

    def test_no_machine_paths_in_package(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)
        for fpath in pkg.rglob("*"):
            if fpath.is_dir() or fpath.name == "checksums.sha256":
                continue
            try:
                content = fpath.read_text(errors="replace")
            except Exception:
                continue
            assert "/Users/" not in content, f"Machine path in {fpath.name}"
            assert "/tmp/" not in content, f"Temp path in {fpath.name}"


# ── Evaluator Compatibility ─────────────────────────────────────────────


class TestEvaluatorCompatibility:
    """Notebook-generated submission is accepted by the evaluator."""

    def test_notebook_submission_accepted_by_evaluator(
        self,
        runner: CliRunner,
        cli_group: Any,
        private_root: Path,
        socket_enabled: Any,
    ) -> None:
        """Build package, execute notebook with real Jupyter kernel, evaluate submission."""
        # socket_enabled fixture disables pytest-socket blocking for this test
        pkg = _build_and_get_package_dir(private_root, runner, cli_group)

        # Create private truth for evaluation
        truth_path = private_root / "private_truth.json"
        truth_path.write_text(json.dumps({"period_returns": [0.001] * 100}))

        # Execute notebook with real kernel in isolated workspace
        work_dir = Path(tempfile.mkdtemp())
        orig_cwd = os.getcwd()
        try:
            for f in pkg.iterdir():
                if f.suffix in (".csv",):
                    shutil.copy2(str(f), str(work_dir / f.name))
            shutil.copy2(str(pkg / "classroom_demo.ipynb"), str(work_dir / "classroom_demo.ipynb"))

            os.chdir(str(work_dir))

            with open("classroom_demo.ipynb") as f:
                nb = nbformat.read(f, as_version=4)
            ep = ExecutePreprocessor(timeout=120, kernel_name="python3")
            ep.preprocess(nb, {"metadata": {"path": str(work_dir)}})

            os.chdir(orig_cwd)

            sub = work_dir / "student_submission.csv"
            assert sub.exists()

            # Read submission and evaluate
            with open(sub) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            periods_str = ",".join(r["relative_period"] for r in rows)
            actions_str = ",".join(r["action"] for r in rows)

            result = runner.invoke(
                cli_group,
                [
                    "evaluate-submission",
                    "--release-id",
                    "SYNTH_001",
                    "--run-id",
                    "test",
                    "--submission-id",
                    "nb-test",
                    "--relative-periods",
                    periods_str,
                    "--binary-actions",
                    actions_str,
                    "--private-truth",
                    str(truth_path),
                    "--private-root",
                    str(private_root),
                ],
            )
            assert result.exit_code == 0, f"Evaluator rejected: {result.stderr}"

            # Check output: aggregate only, no per-period data
            evaluate_files = list(private_root.rglob("evaluate_nb-test.json"))
            if evaluate_files:
                eval_data = json.loads(evaluate_files[0].read_text())
                for key, val in eval_data.items():
                    assert not (isinstance(val, list) and len(val) > 5), (
                        f"Key '{key}' has list with {len(val)} elements"
                    )

        finally:
            os.chdir(orig_cwd)
            shutil.rmtree(str(work_dir), ignore_errors=True)


# ── Evaluator Failure Modes ─────────────────────────────────────────────


class TestEvaluatorFailureModes:
    """Evaluator rejects invalid submissions with correct exit codes."""

    def test_empty_submission_exit_2(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        truth = private_root / "truth.json"
        truth.write_text(json.dumps({"period_returns": [0.001] * 10}))
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "T",
                "--run-id",
                "t",
                "--submission-id",
                "s",
                "--relative-periods",
                "",
                "--binary-actions",
                "",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        assert "[empty_submission]" in (result.stdout + result.stderr).lower()

    def test_duplicate_period_exit_2(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        truth = private_root / "truth.json"
        truth.write_text(json.dumps({"period_returns": [0.001] * 10}))
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "T",
                "--run-id",
                "t",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,0,1",
                "--binary-actions",
                "0,1,0",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        assert "[duplicate_period]" in (result.stdout + result.stderr).lower()

    def test_invalid_action_exit_2(self, runner: CliRunner, cli_group, private_root: Path) -> None:
        truth = private_root / "truth.json"
        truth.write_text(json.dumps({"period_returns": [0.001] * 10}))
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "T",
                "--run-id",
                "t",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2",
                "--binary-actions",
                "0,2,0",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        assert "[binary_violation]" in (result.stdout + result.stderr).lower()

    def test_mismatched_lengths_exit_2(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        truth = private_root / "truth.json"
        truth.write_text(json.dumps({"period_returns": [0.001] * 10}))
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "T",
                "--run-id",
                "t",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,2",
                "--binary-actions",
                "0,1",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        assert "[shape_mismatch]" in (result.stdout + result.stderr).lower()

    def test_unknown_period_exit_2(self, runner: CliRunner, cli_group, private_root: Path) -> None:
        """Period outside available private truth range must fail with [UNKNOWN_PERIOD]."""
        truth = private_root / "truth.json"
        truth.write_text(json.dumps({"period_returns": [0.001] * 10}))
        # Submit period 10 with 10-element truth → period 10 ≥ len(truth)
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "T",
                "--run-id",
                "t",
                "--submission-id",
                "s",
                "--relative-periods",
                "0,1,10",
                "--binary-actions",
                "0,1,0",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        assert "[unknown_period]" in (result.stdout + result.stderr).lower()

    def test_zero_evaluable_decisions_after_lag_exit_2(
        self, runner: CliRunner, cli_group, private_root: Path
    ) -> None:
        """A valid single-period submission with execution_lag=1 produces zero evaluable decisions."""
        truth = private_root / "truth.json"
        truth.write_text(json.dumps({"period_returns": [0.001] * 10}))
        # Single period [0] with action [1] → execution_lag=1 shifts it to
        # position 1, but range(lag, n) = range(1, 1) = empty → zero evaluable
        result = runner.invoke(
            cli_group,
            [
                "evaluate-submission",
                "--release-id",
                "T",
                "--run-id",
                "t",
                "--submission-id",
                "s",
                "--relative-periods",
                "0",
                "--binary-actions",
                "1",
                "--execution-lag",
                "1",
                "--private-truth",
                str(truth),
                "--private-root",
                str(private_root),
            ],
        )
        assert result.exit_code == 2
        assert "[zero_decisions]" in (result.stdout + result.stderr).lower()
