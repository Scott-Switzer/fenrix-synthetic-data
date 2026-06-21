"""Regression tests for privacy release gate failures.

Covers all 8 failure categories from the NVDA Colab run analysis.
Uses synthetic data only — no real company content.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from fenrix_synthetic.pipeline.config import PipelineConfig
from fenrix_synthetic.pipeline.runner import (
    PipelineRunner,
    ReleaseGateResult,
    TickerStatus,
)


class TestTickerStatusExplicit:
    """Verify explicit statuses replace misleading 'completed'."""

    def test_completed_clean(self) -> None:
        """clean_run: zero leaks, no parse errors, coverage OK → completed_clean"""
        status = PipelineRunner._compute_ticker_status(
            exact_ids=0,
            unresolved=0,
            nvidia_status="disabled",
            nvidia_parse_errors=0,
            nvidia_correct_guess=False,
            coverage_report={
                "sec": {
                    "has_data": True,
                    "artifacts": [{"artifact_type": "companyfacts", "row_count": 50}],
                },
                "news": {"historical_10y_complete": True},
            },
            sec_archive_path=MagicMock(),
            sec_source_mode="network-only",
        )
        assert status == TickerStatus.COMPLETED_CLEAN.value

    def test_failed_privacy_exact_ids(self) -> None:
        """exact identifiers > 0 → failed_privacy"""
        status = PipelineRunner._compute_ticker_status(
            exact_ids=4919,
            unresolved=0,
            nvidia_status="disabled",
            nvidia_parse_errors=0,
            nvidia_correct_guess=False,
            coverage_report={
                "sec": {
                    "has_data": True,
                    "artifacts": [{"artifact_type": "companyfacts", "row_count": 50}],
                },
                "news": {"historical_10y_complete": True},
            },
            sec_archive_path=MagicMock(),
            sec_source_mode="archive-preferred",
        )
        assert status == TickerStatus.FAILED_PRIVACY.value

    def test_failed_privacy_unresolved(self) -> None:
        """unresolved candidates > 0 → failed_privacy"""
        status = PipelineRunner._compute_ticker_status(
            exact_ids=0,
            unresolved=44,
            nvidia_status="disabled",
            nvidia_parse_errors=0,
            nvidia_correct_guess=False,
            coverage_report={
                "sec": {
                    "has_data": True,
                    "artifacts": [{"artifact_type": "companyfacts", "row_count": 50}],
                },
                "news": {"historical_10y_complete": True},
            },
            sec_archive_path=MagicMock(),
            sec_source_mode="archive-preferred",
        )
        assert status == TickerStatus.FAILED_PRIVACY.value

    def test_failed_nvidia_parse_errors(self) -> None:
        """NVIDIA parse errors > 0 → failed_nvidia_review"""
        status = PipelineRunner._compute_ticker_status(
            exact_ids=0,
            unresolved=0,
            nvidia_status="completed",
            nvidia_parse_errors=3,
            nvidia_correct_guess=False,
            coverage_report={
                "sec": {
                    "has_data": True,
                    "artifacts": [{"artifact_type": "companyfacts", "row_count": 50}],
                },
                "news": {"historical_10y_complete": True},
            },
            sec_archive_path=MagicMock(),
            sec_source_mode="archive-preferred",
        )
        assert status == TickerStatus.FAILED_NVIDIA_REVIEW.value

    def test_failed_nvidia_correct_guess(self) -> None:
        """NVIDIA correctly identified the company → failed_nvidia_review"""
        status = PipelineRunner._compute_ticker_status(
            exact_ids=0,
            unresolved=0,
            nvidia_status="completed",
            nvidia_parse_errors=0,
            nvidia_correct_guess=True,
            coverage_report={
                "sec": {
                    "has_data": True,
                    "artifacts": [{"artifact_type": "companyfacts", "row_count": 50}],
                },
                "news": {"historical_10y_complete": True},
            },
            sec_archive_path=MagicMock(),
            sec_source_mode="archive-preferred",
        )
        assert status == TickerStatus.FAILED_NVIDIA_REVIEW.value

    def test_degraded_source_coverage_companyfacts_zero(self) -> None:
        """zero-row companyfacts → degraded_source_coverage"""
        status = PipelineRunner._compute_ticker_status(
            exact_ids=0,
            unresolved=0,
            nvidia_status="disabled",
            nvidia_parse_errors=0,
            nvidia_correct_guess=False,
            coverage_report={
                "sec": {
                    "has_data": True,
                    "artifacts": [{"artifact_type": "companyfacts", "row_count": 0}],
                },
                "news": {"historical_10y_complete": True},
            },
            sec_archive_path=MagicMock(),
            sec_source_mode="archive-preferred",
        )
        assert status == TickerStatus.DEGRADED_SOURCE_COVERAGE.value

    def test_degraded_source_coverage_news_not_10y(self) -> None:
        """news not 10-year complete → NOT a mandatory failure (disclosed but nonblocking)"""
        status = PipelineRunner._compute_ticker_status(
            exact_ids=0,
            unresolved=0,
            nvidia_status="disabled",
            nvidia_parse_errors=0,
            nvidia_correct_guess=False,
            coverage_report={
                "sec": {
                    "has_data": True,
                    "artifacts": [{"artifact_type": "companyfacts", "row_count": 50}],
                },
                "news": {"historical_10y_complete": False},
            },
            sec_archive_path=MagicMock(),
            sec_source_mode="archive-preferred",
        )
        # News coverage limitation is disclosed but NOT a mandatory failure
        assert status == TickerStatus.COMPLETED_CLEAN.value

    def test_degraded_archive_null_in_archive_mode(self) -> None:
        """null sec_archive_path in archive mode → degraded_source_coverage"""
        status = PipelineRunner._compute_ticker_status(
            exact_ids=0,
            unresolved=0,
            nvidia_status="disabled",
            nvidia_parse_errors=0,
            nvidia_correct_guess=False,
            coverage_report={
                "sec": {
                    "has_data": True,
                    "artifacts": [{"artifact_type": "companyfacts", "row_count": 50}],
                },
                "news": {"historical_10y_complete": True},
            },
            sec_archive_path=None,
            sec_source_mode="archive-preferred",
        )
        assert status == TickerStatus.DEGRADED_SOURCE_COVERAGE.value


class TestReleaseGateBlock:
    """Verify export ZIP is blocked when any ticker has non-clean status."""

    def test_block_on_failed_privacy(self) -> None:
        """ZIP creation should block (return blocked=True) on failed_privacy"""
        run_summary = {"tickers": {"SYNTH_001": {"status": TickerStatus.FAILED_PRIVACY.value}}}
        config = PipelineConfig.from_ticker("SYNTH_001", MagicMock())
        runner = PipelineRunner(config)
        gate = ReleaseGateResult()
        result = runner._create_export_bundle(gate, run_summary)
        assert result.get("export_blocked") is True
        assert "Release gate" in result.get("export_blocked_reason", "")

    def test_block_on_failed_nvidia(self) -> None:
        """ZIP creation should block on failed_nvidia_review"""
        run_summary = {
            "tickers": {"SYNTH_001": {"status": TickerStatus.FAILED_NVIDIA_REVIEW.value}}
        }
        config = PipelineConfig.from_ticker("SYNTH_001", MagicMock())
        runner = PipelineRunner(config)
        gate = ReleaseGateResult()
        result = runner._create_export_bundle(gate, run_summary)
        assert result.get("export_blocked") is True

    def test_block_on_degraded_coverage(self) -> None:
        """ZIP creation should block on degraded_source_coverage"""
        run_summary = {
            "tickers": {"SYNTH_001": {"status": TickerStatus.DEGRADED_SOURCE_COVERAGE.value}}
        }
        config = PipelineConfig.from_ticker("SYNTH_001", MagicMock())
        runner = PipelineRunner(config)
        gate = ReleaseGateResult()
        result = runner._create_export_bundle(gate, run_summary)
        assert result.get("export_blocked") is True

    def test_pass_on_clean(self, tmp_path) -> None:
        """ZIP creation should NOT block on completed_clean (gate must have explicit clean statuses)"""

        run_summary = {"tickers": {"SYNTH_001": {"status": TickerStatus.COMPLETED_CLEAN.value}}}
        config = PipelineConfig.from_ticker("SYNTH_001", tmp_path)
        runner = PipelineRunner(config)
        runner.run_dir.mkdir(parents=True, exist_ok=True)
        gate = ReleaseGateResult()
        # Set explicit passing statuses — fail-closed gate requires these
        gate.collection_status = "clean"
        gate.privacy_status = "clean"
        gate.format_status = "clean"
        gate.numeric_data_status = "complete"
        gate.coverage_status = "clean"
        gate.finalize()  # Compute release_safe
        assert gate.release_safe is True, f"Expected release_safe=True, got {gate.release_safe}"
        # Should not block a clean run
        try:
            result = runner._create_export_bundle(gate, run_summary)
            assert result.get("export_blocked", False) is False, (
                f"Expected export_blocked=False or absent, got {result}"
            )
        except Exception:
            pytest.fail("Release gate should not block on clean status")


class TestReleaseQASummary:
    """Verify sanitized release QA summary excludes raw data."""

    def test_summary_no_raw_values(self) -> None:
        """Sanitized summary must contain counts/hashes only, no raw values"""
        import hashlib

        run_summary = {
            "run_id": "test_run",
            "tickers": {
                "SYNTH_001": {
                    "status": TickerStatus.COMPLETED_CLEAN.value,
                    "original_artifacts": 10,
                    "anonymized_artifacts": 8,
                    "residual_exact_identifier_count": 0,
                    "unresolved_candidate_count": 0,
                    "nvidia_status": "disabled",
                    "nvidia_parse_errors": 0,
                    "nvidia_correct_guess": False,
                }
            },
        }
        qa = PipelineRunner._build_release_qa_summary(run_summary)
        assert qa["overall"]["all_clean"] is True
        assert "detailed_findings" not in json.dumps(qa)
        assert "raw_response" not in json.dumps(qa)
        # Keys use pseudonymous IDs now
        pseudo = f"COMPANY_{hashlib.sha256(b'SYNTH_001').hexdigest()[:12]}"
        ts = qa["tickers"][pseudo]
        assert ts["status"] == "completed_clean"
        assert ts["exact_identifier_count"] == 0
        # No raw ticker in JSON keys
        assert "SYNTH_001" not in json.dumps(qa)
        assert "NVDA" not in json.dumps(qa)
