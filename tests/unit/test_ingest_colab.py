"""Focused tests for `providers ingest-colab` CLI command.

BLOCKER 1 — CLICK ARGUMENT BINDING
BLOCKER 2 — BENIGN NOTE REJECTION
BLOCKER 3 — INGESTION ARTIFACT STRUCTURE
BLOCKER 4 — INGESTION-CODE PROVENANCE
BLOCKER 5 — EVIDENCE INTEGRITY
BLOCKER 6 — DUPLICATE METRIC SEMANTICS
BLOCKER 7 — REPRODUCE THE USER FAILURE
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from fenrix_synthetic.cli import cli

# Patch target for load_default_benchmark (imported locally inside providers_ingest_colab)
_BENCHMARK_PATCH = "fenrix_synthetic.discovery.providers.gliner.benchmark.load_default_benchmark"


def _make_valid_report(overrides: dict | None = None, mutate_hash: bool = False) -> dict:
    """Build a valid Phase 3C evidence report that passes all ingestion gates.

    Based on the successful clean Colab run with:
    - 31 valid predictions
    - 29 normalized candidates
    - 29 review entries
    - zero automatic acceptance/promotion/registry mutation/remasking
    """
    report = {
        "run_timestamp": "2024-06-19T12:00:00Z",
        "evidence_schema_version": "1.0.0",
        "environment": {
            "python_version": "3.12.13",
            "platform": "linux",
            "gliner_version": "0.2.27",
            "torch_version": "2.11.0+cu128",
            "cuda_available": True,
            "mps_available": False,
        },
        "repository": {
            "branch": "feature/local-gliner-adapter",
            "checked_out_commit": "abcdef1234567890",
            "expected_commit": "abcdef1234567890",
            "commit_verified": True,
            "working_tree_clean": True,
        },
        "model": {
            "model_id": "gliner-community/gliner_small-v2.5",
            "requested_revision": None,
            "resolved_revision": "v2.5",
            "device": "cpu",
            "load_success": True,
            "load_duration_seconds": 11.564,
        },
        "discovery": {
            "predict_entities_success": True,
            "inference_duration_seconds": 0.808,
            "threshold": 0.5,
            "raw_candidates": 31,
            "valid_candidates": 31,
            "malformed_output_count": 0,
            "normalized_candidate_count": 29,
            "duplicate_groups": 29,
            "duplicate_candidates_removed": 2,
            "pending_count": 29,
            "accepted_count": 0,
            "rejected_count": 0,
            "warnings": [],
        },
        "evaluation": {
            "benchmark_hash": "6bf3aed8c0d2a1e7",
            "benchmark_documents": 5,
            "benchmark_scope": "full",
            "canonical_entity_types_tested": [
                "company",
                "executive",
                "product",
                "location",
            ],
            "total_expected": 25,
            "total_predicted": 31,
            "true_positives_exact": 15,
            "true_positives_relaxed": 15,
            "false_positives": 15,
            "false_negatives": 10,
            "hard_negative_hits": 1,
            "exact_precision": 0.5,
            "exact_recall": 0.6,
            "exact_f1": 0.5454545454545454,
            "relaxed_precision": 0.5,
            "relaxed_recall": 0.6,
            "relaxed_f1": 0.5454545454545454,
            "per_type_metrics": {
                "company": {"expected": 10, "predicted": 12, "tp_exact": 6},
            },
            "validation_counters": {
                "total_received": 31,
                "accepted": 31,
                "rejected_missing_fields": 0,
                "rejected_invalid_offsets": 0,
                "rejected_out_of_range": 0,
                "rejected_text_mismatch": 0,
                "rejected_non_numeric_score": 0,
                "rejected_score_out_of_range": 0,
                "rejected_missing_label": 0,
            },
            "review_workload_estimate": {
                "high_confidence": 15,
                "medium_confidence": 10,
                "low_confidence": 4,
            },
        },
        "review_queue": {
            "review_queue_count": 29,
            "pending_review_count": 29,
            "accepted_count": 0,
            "rejected_count": 0,
            "automatic_acceptance_count": 0,
            "automatic_promotion_count": 0,
            "registry_mutation_count": 0,
            "remasking_count": 0,
            "note": "All candidates are pending human review; no auto-accept, auto-promote, or remask occurred.",
        },
        "privacy": {
            "no_real_company_data": True,
            "synthetic_only": True,
            "warnings": [],
        },
    }
    if overrides:
        _deep_update(report, overrides)
    # Compute canonical payload hash (excludes the hash field itself)
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    report["evidence_payload_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    if mutate_hash:
        report["evidence_payload_hash"] = "0000000000000000"
    return report


def _deep_update(base: dict, updates: dict) -> None:
    for k, v in updates.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def _write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


class TestIngestColabValid:
    """Prove the untouched fixture ingests successfully."""

    def _mock_git(self, commit: str = "abcdef1234567890", status: str = ""):
        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout=f"{commit}\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout=status, returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return MagicMock(stdout="/repo\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        return side_effect

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_valid_report_ingests(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
                "--expected-commit",
                "abcdef1234567890",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ACCEPTED" in result.output
        assert output_path.exists()
        artifact = json.loads(output_path.read_text())
        assert artifact["verification_status"] == "accepted"
        assert artifact["ingestion_schema_version"] == "1.0.0"
        assert "content_hash" in artifact
        assert "source_report" not in artifact
        assert artifact["review_queue_counts"]["review_queue_count"] == 29
        assert artifact["review_queue_counts"]["normalized_candidate_count"] == 29
        assert artifact["review_queue_counts"]["automatic_acceptance_count"] == 0
        assert artifact["review_queue_counts"]["automatic_promotion_count"] == 0
        assert artifact["review_queue_counts"]["registry_mutation_count"] == 0
        assert artifact["review_queue_counts"]["remasking_count"] == 0
        assert artifact["verification_checklist"]["evidence_payload_hash_verified"] is True

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_valid_report_without_expected_commit(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ACCEPTED" in result.output


class TestIngestColabClickBinding:
    """BLOCKER 1: Prove Click argument binding works end-to-end."""

    def _mock_git(self, commit: str = "abcdef1234567890", status: str = ""):
        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout=f"{commit}\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout=status, returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return MagicMock(stdout="/repo\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        return side_effect

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_exact_command_line_invocation(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
                "--expected-commit",
                "abcdef1234567890",
            ],
        )
        assert result.exit_code == 0, result.output
        assert output_path.exists()


class TestIngestColabPrivacy:
    """BLOCKER 2: Prove sanitized note is accepted; private fields are rejected."""

    def _mock_git(self):
        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout="abcdef1234567890\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout="", returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return MagicMock(stdout="/repo\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        return side_effect

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_legitimate_note_is_accepted(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report(
                {
                    "review_queue": {
                        "note": "All candidates are pending human review; no auto-accept, auto-promote, or remask occurred."
                    }
                }
            ),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ACCEPTED" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_candidate_text_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"review_queue": {"candidate_text": "Acme Corporation"}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "forbidden key 'candidate_text'" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_matched_text_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"discovery": {"matched_text": "Jane Smith"}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "forbidden key 'matched_text'" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_raw_response_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"evaluation": {"raw_response": "some raw text"}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "forbidden key 'raw_response'" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_nested_private_field_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report(
                {"evaluation": {"per_type_metrics": {"company": {"context": "some context"}}}}
            ),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "forbidden key 'context'" in result.output


class TestIngestColabProvenance:
    """BLOCKER 4: Ingestion-code provenance validation."""

    def _mock_git(self, commit: str = "abcdef1234567890", status: str = ""):
        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout=f"{commit}\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout=status, returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return MagicMock(stdout="/repo\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        return side_effect

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_clean_matching_checkout(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
                "--expected-commit",
                "abcdef1234567890",
            ],
        )
        assert result.exit_code == 0, result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_mismatched_head(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git(commit="different_commit")
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
                "--expected-commit",
                "abcdef1234567890",
            ],
        )
        assert result.exit_code == 1
        assert "ingestion commit mismatch" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_modified_tracked_cli_file(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git(status=" M src/fenrix_synthetic/cli.py\n")
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "ingestion working tree is dirty" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_deleted_tracked_file(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git(status=" D src/fenrix_synthetic/cli.py\n")
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "ingestion working tree is dirty" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_permitted_external_report_and_output(self, mock_run, mock_benchmark, tmp_path: Path):
        """Untracked report/output paths outside repo should not make tree dirty."""
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 0, result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_report_commit_does_not_match_local_head(
        self, mock_run, mock_benchmark, tmp_path: Path
    ):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git(commit="local_head_sha")
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"repository": {"checked_out_commit": "other_sha"}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "does not match local HEAD" in result.output


class TestIngestColabIntegrity:
    """BLOCKER 5: Evidence integrity mutation tests."""

    def _mock_git(self):
        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout="abcdef1234567890\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout="", returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return MagicMock(stdout="/repo\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        return side_effect

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_missing_evidence_payload_hash_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        report = _make_valid_report()
        del report["evidence_payload_hash"]
        _write_report(report_path, report)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "evidence_payload_hash missing" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_changed_metric_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        """A changed metric without recompute must be rejected by hash mismatch."""
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        report = _make_valid_report()
        report["discovery"]["normalized_candidate_count"] = 999
        # Do NOT recompute hash — stale hash should cause mismatch
        _write_report(report_path, report)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "evidence payload hash mismatch" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_removed_field_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        report = _make_valid_report()
        del report["review_queue"]["note"]
        _write_report(report_path, report)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "evidence payload hash mismatch" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_added_private_field_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        """An added non-forbidden private field without recompute must be rejected by hash mismatch."""
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        report = _make_valid_report()
        report["private_leak"] = "Acme Corporation"
        # Do NOT recompute hash — stale hash should cause mismatch
        _write_report(report_path, report)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "evidence payload hash mismatch" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_added_forbidden_field_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        """A forbidden key must be caught by privacy scan after a correct hash recompute."""
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        report = _make_valid_report()
        report["matched_text"] = "Acme Corporation"
        # Recompute hash correctly (exclude the hash field itself, matching ingestion logic)
        hashable = {k: v for k, v in report.items() if k != "evidence_payload_hash"}
        canonical = json.dumps(hashable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        report["evidence_payload_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        _write_report(report_path, report)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "forbidden key 'matched_text'" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_altered_note_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        """Any manual edit to the evidence, including the note, must cause rejection."""
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        report = _make_valid_report()
        report["review_queue"]["note"] = "Tampered note"
        # Recompute hash so it passes hash check
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        report["evidence_payload_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        _write_report(report_path, report)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "evidence payload hash mismatch" in result.output


class TestIngestColabDuplicateMetrics:
    """BLOCKER 6: Duplicate metric semantics and invariants."""

    def _mock_git(self):
        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout="abcdef1234567890\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout="", returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return MagicMock(stdout="/repo\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        return side_effect

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_duplicate_metric_invariant(self, mock_run, mock_benchmark, tmp_path: Path):
        """valid_candidates - duplicate_candidates_removed = normalized_candidate_count."""
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        report = _make_valid_report()
        # Invariant: valid_candidates (31) - duplicate_candidates_removed (2) = normalized (29)
        assert (
            report["discovery"]["valid_candidates"]
            - report["discovery"]["duplicate_candidates_removed"]
            == report["discovery"]["normalized_candidate_count"]
        )
        _write_report(report_path, report)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 0, result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_duplicate_groups_is_surviving_groups(self, mock_run, mock_benchmark, tmp_path: Path):
        """duplicate_groups should equal the number of surviving deduplicated groups."""
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        report = _make_valid_report()
        # For the observed Colab run: 29 duplicate_groups, 29 normalized candidates
        # This means each surviving group produced exactly one normalized candidate
        assert (
            report["discovery"]["duplicate_groups"]
            == report["discovery"]["normalized_candidate_count"]
        )
        _write_report(report_path, report)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 0, result.output


class TestIngestColabZeroAcceptance:
    """Prove zero acceptance, promotion, registry mutation, remasking."""

    def _mock_git(self):
        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout="abcdef1234567890\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout="", returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return MagicMock(stdout="/repo\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        return side_effect

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_nonzero_acceptance_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"review_queue": {"automatic_acceptance_count": 1}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "automatic_acceptance_count is non-zero" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_nonzero_promotion_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"review_queue": {"automatic_promotion_count": 1}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "automatic_promotion_count is non-zero" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_nonzero_registry_mutation_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"review_queue": {"registry_mutation_count": 1}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "registry_mutation_count is non-zero" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_nonzero_remasking_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"review_queue": {"remasking_count": 1}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "remasking_count is non-zero" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_zero_review_queue_with_nonzero_candidates_rejected(
        self, mock_run, mock_benchmark, tmp_path: Path
    ):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report(
                {
                    "review_queue": {"review_queue_count": 0},
                    "discovery": {"normalized_candidate_count": 29},
                }
            ),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "normalized candidates but review_queue_count=0" in result.output


class TestIngestColabReportProvenance:
    """Report-side provenance validation."""

    def _mock_git(self):
        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout="abcdef1234567890\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout="", returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return MagicMock(stdout="/repo\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        return side_effect

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_dirty_working_tree_in_report_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"repository": {"working_tree_clean": False}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "working_tree_clean=false" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_unverified_commit_in_report_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"repository": {"commit_verified": False}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "commit_verified=false" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_model_load_failure_in_report_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"model": {"load_success": False}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "model.load_success is false" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_predict_entities_failure_in_report_rejected(
        self, mock_run, mock_benchmark, tmp_path: Path
    ):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"discovery": {"predict_entities_success": False}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "predict_entities_success is false" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_no_real_data_false_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"privacy": {"no_real_company_data": False}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "no_real_company_data is false" in result.output

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_benchmark_hash_mismatch_rejected(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(
            report_path,
            _make_valid_report({"evaluation": {"benchmark_hash": "wrong_hash"}}),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 1
        assert "benchmark hash mismatch" in result.output


class TestIngestColabArtifactStructure:
    """BLOCKER 3: Verify ingestion artifact structure."""

    def _mock_git(self):
        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout="abcdef1234567890\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout="", returncode=0)
            if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return MagicMock(stdout="/repo\n", returncode=0)
            return MagicMock(stdout="", returncode=0)

        return side_effect

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_no_source_report_repr(self, mock_run, mock_benchmark, tmp_path: Path):
        """Artifact must not contain the entire source report as a Python repr string."""
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 0, result.output
        artifact = json.loads(output_path.read_text())
        assert "source_report" not in artifact
        assert "run_timestamp" not in artifact  # Should be in content_hash, not raw

    @patch(_BENCHMARK_PATCH)
    @patch("subprocess.run")
    def test_compact_summary_fields(self, mock_run, mock_benchmark, tmp_path: Path):
        mock_benchmark.return_value = MagicMock(benchmark_hash="6bf3aed8c0d2a1e7")
        mock_run.side_effect = self._mock_git()
        report_path = tmp_path / "phase3c_evidence.json"
        output_path = tmp_path / "phase3c_ingestion.json"
        _write_report(report_path, _make_valid_report())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "providers",
                "ingest-colab",
                "--report",
                str(report_path),
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 0, result.output
        artifact = json.loads(output_path.read_text())
        assert "ingestion_schema_version" in artifact
        assert "content_hash" in artifact
        assert "repository_commit" in artifact
        assert "benchmark_hash" in artifact
        assert "model_identifier" in artifact
        assert "principal_metrics" in artifact
        assert "review_queue_counts" in artifact
        assert "verification_checklist" in artifact
        assert "anonymity_disclaimer" in artifact
