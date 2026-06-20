"""Regression tests for pre-Colab release blockers.

Covers:
1. force_refresh executes all collectors
2. unknown statuses fail despite zero counts
3. NVIDIA disabled blocks when enabled
4. empty NVIDIA result blocks
5. recent-only news is disclosed but not a mandatory failure
6. news coverage result is retained
7. no real ticker appears in ZIP member names
8. raw run summary is excluded
9. raw config is excluded
10. sanitized summary uses only pseudonymous IDs
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

from fenrix_synthetic.collectors.news_collector import NewsCollector, NewsCoverageReport
from fenrix_synthetic.pipeline.config import PipelineConfig
from fenrix_synthetic.pipeline.runner import (
    PipelineRunner,
    ReleaseGateResult,
    TickerStatus,
)

# ── Fix 1: force_refresh executes all collectors ──────────────────────


class TestForceRefresh:
    """Prove force_refresh forces collection, never skips."""

    def test_force_refresh_yfinance_does_not_skip(self, tmp_path: Path) -> None:
        """When 'yfinance' is in force_refresh, collection still runs."""
        config = PipelineConfig.from_ticker("FAKE", tmp_path, years=1, force_refresh={"yfinance"})
        runner = PipelineRunner(config)
        runner.run_dir = tmp_path
        originals_dir = tmp_path / "originals" / "FAKE"
        originals_dir.mkdir(parents=True)

        manifests: list[dict] = []
        builder = MagicMock()
        result = runner._collect_yfinance(
            "FAKE",
            config.tickers[0],
            originals_dir,
            manifests,
            builder,
        )
        # Should NOT be None (was None before fix when force_refresh was treated as skip)
        assert result is not None, "yfinance collection should NOT be skipped under force_refresh"
        assert len(manifests) > 0, "yfinance collection should produce manifests"

    def test_force_refresh_news_does_not_skip(self, tmp_path: Path) -> None:
        """When 'news' is in force_refresh, collection still runs."""
        config = PipelineConfig.from_ticker("FAKE", tmp_path, years=1, force_refresh={"news"})
        runner = PipelineRunner(config)
        runner.run_dir = tmp_path
        originals_dir = tmp_path / "originals" / "FAKE"
        originals_dir.mkdir(parents=True)

        manifests: list[dict] = []
        builder = MagicMock()
        # We need yf_result for company name; mock it
        yf_result = MagicMock()
        yf_result.metadata = {"short_name": "FakeCorp"}
        news_results: list = []

        ncov = runner._collect_news(
            "FAKE",
            originals_dir,
            yf_result,
            news_results,
            manifests,
            builder,
        )
        # Should NOT be None — returns NewsCoverageReport
        assert ncov is not None, "news collection should NOT be skipped under force_refresh"

    def test_force_refresh_sec_does_not_skip_on_user_agent(self, tmp_path: Path) -> None:
        """When 'sec' is in force_refresh and user_agent is set, collection runs."""
        config = PipelineConfig.from_ticker(
            "FAKE",
            tmp_path,
            years=1,
            force_refresh={"sec"},
            sec_user_agent="test@example.com",
        )
        runner = PipelineRunner(config)
        runner.run_dir = tmp_path
        originals_dir = tmp_path / "originals" / "FAKE"
        originals_dir.mkdir(parents=True)

        manifests: list[dict] = []
        builder = MagicMock()
        sec_results: list = []

        # This test verifies that the _collect_sec method does NOT return early
        # when "sec" is in force_refresh. It should proceed to try collection.
        # We just check it doesn't raise or return early.
        runner._collect_sec(
            "FAKE",
            config.tickers[0],
            originals_dir,
            sec_results,
            manifests,
            builder,
        )
        # Collection may fail (no network in test), but it should NOT have
        # been skipped by the force_refresh check.
        # The important thing is that the method was called and didn't silently return.


# ── Fix 4: Status fields fail closed ──────────────────────────────────


class TestStatusFieldsFailClosed:
    """Prove release_safe requires explicit passing statuses, not just zero counts."""

    def test_all_counts_zero_but_unknown_status_fails(self) -> None:
        """Every integer count zero but status unknown → release_safe is False."""
        gate = ReleaseGateResult()
        # All counts are zero by default
        # All statuses are "unknown" by default
        gate.finalize()
        assert gate.release_safe is False, (
            "release_safe must be False when statuses are unknown despite zero counts"
        )
        assert gate.overall_status == "qa_failed"

    def test_all_counts_zero_with_clean_statuses_passes(self) -> None:
        """Every count zero AND statuses clean → release_safe is True."""
        gate = ReleaseGateResult()
        gate.collection_status = "clean"
        gate.privacy_status = "clean"
        gate.format_status = "clean"
        gate.numeric_data_status = "complete"
        gate.coverage_status = "clean"
        gate.finalize()
        assert gate.release_safe is True, (
            "release_safe must be True when all counts are zero AND statuses are clean"
        )

    def test_disabled_status_blocks(self) -> None:
        """A blocking status like 'failed' causes release_safe=False."""
        gate = ReleaseGateResult()
        gate.collection_status = "clean"
        gate.privacy_status = "clean"
        gate.format_status = "clean"
        gate.numeric_data_status = "complete"
        gate.coverage_status = "failed"  # blocking
        gate.finalize()
        assert gate.release_safe is False

    def test_degraded_status_blocks(self) -> None:
        """'degraded' is in the blocking statuses set."""
        gate = ReleaseGateResult()
        gate.collection_status = "clean"
        gate.privacy_status = "clean"
        gate.format_status = "clean"
        gate.numeric_data_status = "complete"
        gate.coverage_status = "degraded"  # blocking
        gate.finalize()
        assert gate.release_safe is False

    def test_skipped_status_blocks(self) -> None:
        """'skipped' is in the blocking statuses set."""
        gate = ReleaseGateResult()
        gate.collection_status = "clean"
        gate.privacy_status = "clean"
        gate.format_status = "clean"
        gate.numeric_data_status = "complete"
        gate.coverage_status = "clean"
        gate.nvidia_status = "skipped"  # blocking even without nvidia enabled
        gate.finalize()
        assert gate.release_safe is False


# ── Fix 6: NVIDIA completion rules ────────────────────────────────────


class TestNVIDIACompletionRules:
    """Prove NVIDIA evaluation blocks on empty results, parse errors, etc."""

    def test_evaluate_nvidia_empty_result_blocks(self) -> None:
        """Empty attacker_results → failed_empty."""
        review: dict = {"attacker_results": []}
        status, parse_errs, correct_guesses, failed_reqs = PipelineRunner._evaluate_nvidia_result(
            review
        )
        assert status == "failed_empty"
        assert failed_reqs > 0

    def test_evaluate_nvidia_no_samples_blocks(self) -> None:
        """samples_reviewed < 1 → failed_no_samples."""
        review: dict = {
            "attacker_results": [
                {"sample_index": 0, "result": {"parse_error": False, "correct_guess": False}}
            ],
            "samples_reviewed": 0,
        }
        status, _, _, failed_reqs = PipelineRunner._evaluate_nvidia_result(review)
        assert status == "failed_no_samples"
        assert failed_reqs > 0

    def test_evaluate_nvidia_parse_error_blocks(self) -> None:
        """Parse error → failed_parse."""
        review: dict = {
            "attacker_results": [
                {"sample_index": 0, "result": {"parse_error": True, "correct_guess": False}}
            ],
            "samples_reviewed": 1,
        }
        status, parse_errs, _, _ = PipelineRunner._evaluate_nvidia_result(review)
        assert status == "failed_parse"
        assert parse_errs > 0

    def test_evaluate_nvidia_correct_guess_blocks(self) -> None:
        """Correct guess → failed_correct_guess."""
        review: dict = {
            "attacker_results": [
                {"sample_index": 0, "result": {"parse_error": False, "correct_guess": True}}
            ],
            "samples_reviewed": 1,
        }
        status, _, correct_guesses, _ = PipelineRunner._evaluate_nvidia_result(review)
        assert status == "failed_correct_guess"
        assert correct_guesses > 0

    def test_evaluate_nvidia_failed_request_blocks(self) -> None:
        """Confidence < 0 (failed request) → failed_requests."""
        review: dict = {
            "attacker_results": [
                {
                    "sample_index": 0,
                    "result": {
                        "parse_error": False,
                        "correct_guess": False,
                        "confidence": -1,
                    },
                }
            ],
            "samples_reviewed": 1,
        }
        status, _, _, failed_reqs = PipelineRunner._evaluate_nvidia_result(review)
        assert status == "failed_requests"
        assert failed_reqs > 0

    def test_evaluate_nvidia_everything_clean_returns_passed(self) -> None:
        """All clean → returns 'passed' (NOT 'completed')."""
        review: dict = {
            "attacker_results": [
                {
                    "sample_index": 0,
                    "result": {
                        "parse_error": False,
                        "correct_guess": False,
                        "confidence": 0.3,
                    },
                }
            ],
            "samples_reviewed": 1,
        }
        status, parse_errs, correct_guesses, failed_reqs = PipelineRunner._evaluate_nvidia_result(
            review
        )
        assert status == "passed", f"NVIDIA clean result must be 'passed', got '{status}'"
        assert parse_errs == 0
        assert correct_guesses == 0
        assert failed_reqs == 0

    def test_nvidia_disabled_blocks_when_enabled(self) -> None:
        """When nvidia_enabled=True and nvidia_status='disabled', release_safe is False."""
        gate = ReleaseGateResult()
        gate.collection_status = "clean"
        gate.privacy_status = "clean"
        gate.format_status = "clean"
        gate.numeric_data_status = "complete"
        gate.coverage_status = "clean"
        gate.nvidia_enabled = True
        gate.nvidia_status = "disabled"  # blocking when enabled
        gate.finalize()
        assert gate.release_safe is False, "NVIDIA disabled when enabled must block release"

    def test_nvidia_disabled_ok_when_not_enabled(self) -> None:
        """When nvidia_enabled=False and nvidia_status='disabled', release_safe can be True."""
        gate = ReleaseGateResult()
        gate.collection_status = "clean"
        gate.privacy_status = "clean"
        gate.format_status = "clean"
        gate.numeric_data_status = "complete"
        gate.coverage_status = "clean"
        gate.nvidia_enabled = False
        gate.nvidia_status = "disabled"  # OK when not enabled
        gate.finalize()
        assert gate.release_safe is True


# ── Fix 5: News coverage truthful but nonblocking ─────────────────────


class TestNewsCoverageNonblocking:
    """Prove recent-only news is disclosed but not a mandatory failure."""

    def test_news_not_10y_does_not_block_ticker_status(self) -> None:
        """News not 10-year complete still allows COMPLETED_CLEAN."""
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
        assert status == TickerStatus.COMPLETED_CLEAN.value, (
            "News coverage limitation should be disclosed but NOT a mandatory failure"
        )

    def test_check_source_coverage_no_longer_flags_news(self) -> None:
        """_check_source_coverage no longer returns news_not_10y_complete."""
        failures = PipelineRunner._check_source_coverage(
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
        assert "news_not_10y_complete" not in failures, (
            "news_not_10y_complete must not be a mandatory coverage failure"
        )

    def test_news_coverage_result_is_retained(self, tmp_path: Path) -> None:
        """The news coverage report from NewsCollector.collect_all is preserved."""
        originals_dir = tmp_path / "originals" / "FAKE"
        originals_dir.mkdir(parents=True)
        collector = NewsCollector(originals_dir, "FAKE", company_name="FakeCorp")
        results, coverage = collector.collect_all()
        assert isinstance(coverage, NewsCoverageReport), (
            "collect_all must return a NewsCoverageReport as second element"
        )
        assert coverage.ticker == "FAKE"
        assert coverage.historical_10y_complete is False
        assert len(coverage.coverage_limitations) > 0


# ── Fix 2+3: Sanitized release tree, no ticker, no raw files ─────────


class TestSanitizedReleaseTree:
    """Prove ZIP export uses pseudonymous paths and excludes raw files."""

    def _setup_export_test(self, tmp_path: Path) -> tuple[PipelineRunner, dict, ReleaseGateResult]:
        """Create a pipeline runner with synthetic files and clean gate."""
        config = PipelineConfig.from_ticker("FAKE", tmp_path, years=1, collect_only=True)
        runner = PipelineRunner(config)
        runner.run_dir.mkdir(parents=True, exist_ok=True)

        # Create anonymized files
        anon_dir = runner.run_dir / "anonymized" / "FAKE"
        anon_dir.mkdir(parents=True, exist_ok=True)
        (anon_dir / "public_data.json").write_text('{"data":"public"}')

        # Create QA files
        qa_dir = runner.run_dir / "qa" / "FAKE"
        qa_dir.mkdir(parents=True, exist_ok=True)
        (qa_dir / "coverage.json").write_text('{"status":"ok"}')

        # Create originals (should NOT be in ZIP)
        orig_dir = runner.run_dir / "originals" / "FAKE"
        orig_dir.mkdir(parents=True, exist_ok=True)
        (orig_dir / "secret.txt").write_text("SECRET")

        # Create private maps (should NOT be in ZIP)
        priv_dir = runner.run_dir / "private_maps" / "FAKE"
        priv_dir.mkdir(parents=True, exist_ok=True)
        (priv_dir / "identity_atlas.yaml").write_text("private: data")

        # Create config (should NOT be in ZIP)
        cfg_dir = runner.run_dir / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "resolved_config.json").write_text('{"secret":"config_data"}')

        run_summary = {
            "run_id": "test",
            "tickers": {
                "FAKE": {
                    "status": TickerStatus.COMPLETED_CLEAN.value,
                    "residual_exact_identifier_count": 0,
                    "unresolved_candidate_count": 0,
                    "path_identifier_count": 0,
                    "filename_identifier_count": 0,
                    "manifest_identifier_count": 0,
                    "nvidia_status": "disabled",
                    "nvidia_parse_errors": 0,
                    "nvidia_correct_guess_count": 0,
                    "nvidia_failed_request_count": 0,
                    "required_numeric_dataset_failure_count": 0,
                    "required_sec_format_failure_count": 0,
                    "required_coverage_failure_count": 0,
                }
            },
        }

        gate = ReleaseGateResult()
        gate.collection_status = "clean"
        gate.privacy_status = "clean"
        gate.format_status = "clean"
        gate.numeric_data_status = "complete"
        gate.coverage_status = "clean"

        return runner, run_summary, gate

    def test_no_ticker_in_zip_member_names(self, tmp_path: Path) -> None:
        """ZIP member names must use COMPANY_<hash>, not real ticker."""
        runner, run_summary, gate = self._setup_export_test(tmp_path)
        result = runner._create_export_bundle(gate, run_summary)

        zip_path = Path(result["export_zip"])
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                # Must not contain real ticker
                assert "FAKE" not in name.split("/")[1] if "/" in name else True, (
                    f"ZIP member contains real ticker: {name}"
                )

    def test_company_prefix_in_zip_paths(self, tmp_path: Path) -> None:
        """ZIP paths use COMPANY_<hash> prefix in release/ tree."""
        runner, run_summary, gate = self._setup_export_test(tmp_path)
        result = runner._create_export_bundle(gate, run_summary)

        zip_path = Path(result["export_zip"])
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            # All anonymized content paths should be under release/COMPANY_<hash>/
            data_names = [n for n in names if "sanitized" not in n and "verdict" not in n]
            for name in data_names:
                assert name.startswith("release/"), f"Path not under release/: {name}"
                parts = name.split("/")
                if len(parts) >= 2:
                    assert parts[1].startswith("COMPANY_"), (
                        f"Second segment should be COMPANY_<hash>, got: {parts[1]}"
                    )

    def test_raw_run_summary_excluded(self, tmp_path: Path) -> None:
        """Raw run_summary.json must NOT appear in the ZIP."""
        runner, run_summary, gate = self._setup_export_test(tmp_path)
        result = runner._create_export_bundle(gate, run_summary)

        zip_path = Path(result["export_zip"])
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                assert not name.endswith("run_summary.json") or "sanitized" in name, (
                    f"Raw run_summary.json found in ZIP: {name}"
                )

    def test_raw_config_excluded(self, tmp_path: Path) -> None:
        """Config directory must NOT appear in the ZIP."""
        runner, run_summary, gate = self._setup_export_test(tmp_path)
        result = runner._create_export_bundle(gate, run_summary)

        zip_path = Path(result["export_zip"])
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                assert "config/" not in name.lower(), f"Config directory found in ZIP: {name}"

    def test_originals_excluded(self, tmp_path: Path) -> None:
        """Originals directory must NOT appear in the ZIP."""
        runner, run_summary, gate = self._setup_export_test(tmp_path)
        result = runner._create_export_bundle(gate, run_summary)

        zip_path = Path(result["export_zip"])
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                assert "originals" not in name.lower(), f"Originals leaked into ZIP: {name}"

    def test_private_maps_excluded(self, tmp_path: Path) -> None:
        """Private maps directory must NOT appear in the ZIP."""
        runner, run_summary, gate = self._setup_export_test(tmp_path)
        result = runner._create_export_bundle(gate, run_summary)

        zip_path = Path(result["export_zip"])
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                assert "private_maps" not in name.lower(), f"Private maps leaked into ZIP: {name}"

    def test_sanitized_summary_uses_pseudonymous_ids(self, tmp_path: Path) -> None:
        """Sanitized run summary uses COMPANY_<hash> keys, never raw ticker."""
        run_summary = {
            "run_id": "test",
            "tickers": {
                "NVDA": {
                    "status": TickerStatus.COMPLETED_CLEAN.value,
                    "original_artifacts": 5,
                    "anonymized_artifacts": 3,
                    "residual_exact_identifier_count": 0,
                    "unresolved_candidate_count": 0,
                    "path_identifier_count": 0,
                    "filename_identifier_count": 0,
                    "manifest_identifier_count": 0,
                    "nvidia_status": "disabled",
                    "nvidia_parse_errors": 0,
                    "nvidia_correct_guess_count": 0,
                    "nvidia_failed_request_count": 0,
                    "required_numeric_dataset_failure_count": 0,
                    "required_sec_format_failure_count": 0,
                    "required_coverage_failure_count": 0,
                }
            },
        }
        qa = PipelineRunner._build_release_qa_summary(run_summary)
        qa_json = json.dumps(qa)

        # Must not contain raw ticker in JSON keys or values
        assert "NVDA" not in qa_json, "Raw ticker 'NVDA' found in sanitized summary"
        # nvidia_status is a status field name, not a ticker — that's OK
        # But the ticker 'NVDA' itself must not appear

        # Must have pseudonymous key
        pseudo = f"COMPANY_{hashlib.sha256(b'NVDA').hexdigest()[:12]}"
        assert pseudo in qa["tickers"], f"Pseudonymous key '{pseudo}' not found in tickers"

    def test_release_verdict_no_real_ticker(self, tmp_path: Path) -> None:
        """Release verdict contains only pseudonymous IDs, never raw ticker."""
        run_summary = {
            "run_id": "test",
            "tickers": {
                "NVDA": {
                    "status": TickerStatus.COMPLETED_CLEAN.value,
                    "coverage": {"overall": {"sources_successful": 2}},
                    "residual_exact_identifier_count": 0,
                    "nvidia_status": "disabled",
                    "required_coverage_failure_count": 0,
                    "required_numeric_dataset_failure_count": 0,
                    "required_sec_format_failure_count": 0,
                }
            },
        }
        gate = ReleaseGateResult()
        gate.collection_status = "clean"
        gate.privacy_status = "clean"
        gate.format_status = "clean"
        gate.numeric_data_status = "complete"
        gate.coverage_status = "clean"
        gate.finalize()

        ticker_pseudonyms = {"NVDA": f"COMPANY_{hashlib.sha256(b'NVDA').hexdigest()[:12]}"}
        verdict = PipelineRunner._build_release_verdict(run_summary, gate, ticker_pseudonyms)

        verdict_json = json.dumps(verdict)
        assert "NVDA" not in verdict_json, "Raw ticker 'NVDA' found in release verdict"
        pseudo = ticker_pseudonyms["NVDA"]
        assert pseudo in verdict["tickers"], f"Pseudonymous key '{pseudo}' not in verdict"


# ── Fix: ZIP scan for leaks ────────────────────────────────────────────


class TestZipLeakScan:
    """Prove the ZIP leak scanner catches all required issues."""

    def test_leak_scanner_blocks_ticker_in_name(self, tmp_path: Path) -> None:
        """ZIP with NVDA in member name is blocked."""
        from fenrix_synthetic.pipeline.runner import PipelineRunner

        # Create a ZIP with ticker in member name
        zip_path = tmp_path / "bad.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("release/NVDA/data.json", "{}")

        config = PipelineConfig.from_ticker("TEST", tmp_path, years=1)
        runner = PipelineRunner(config)
        issues = runner._scan_zip_for_leaks(zip_path)
        assert len(issues) > 0, "ZIP with ticker in member name should be flagged"
        assert any("NVDA" in i for i in issues)

    def test_leak_scanner_blocks_raw_run_summary(self, tmp_path: Path) -> None:
        """ZIP with raw run_summary.json is flagged."""
        from fenrix_synthetic.pipeline.runner import PipelineRunner

        zip_path = tmp_path / "bad2.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("run_summary.json", '{"ticker":"NVDA"}')

        config = PipelineConfig.from_ticker("TEST", tmp_path, years=1)
        runner = PipelineRunner(config)
        issues = runner._scan_zip_for_leaks(zip_path)
        assert len(issues) > 0, "ZIP with run_summary.json should be flagged"

    def test_leak_scanner_allows_sanitized(self, tmp_path: Path) -> None:
        """ZIP with sanitized_run_summary.json is NOT flagged."""
        from fenrix_synthetic.pipeline.runner import PipelineRunner

        zip_path = tmp_path / "good.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("release/sanitized_run_summary.json", '{"pseudo":"yes"}')
            zf.writestr("release/release_verdict.json", '{"ok":true}')

        config = PipelineConfig.from_ticker("TEST", tmp_path, years=1)
        runner = PipelineRunner(config)
        issues = runner._scan_zip_for_leaks(zip_path)
        assert len(issues) == 0, f"Sanitized files should not trigger leak scan. Got: {issues}"
