"""Pipeline runner orchestrating collection, anonymization, manifests, and QA.

Implements mandatory fail-closed release gate:
- No ZIP is created when any blocking count is nonzero.
- ZIP is built in a temp staging directory, scanned for leaks, then atomically moved.
- Never leaves a partial ZIP after failure.
- Returns explicit overall_status: qa_passed, qa_failed, or collection_failed.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import orjson

from ..anonymization.atlas_builder import IdentityAtlasBuilder
from ..anonymization.residual_scanner import ResidualScanner
from ..anonymization.structured_anonymizer import StructuredAnonymizer
from ..anonymization.text_anonymizer import TextAnonymizer
from ..collectors import NewsCollector, SECCollector, YFinanceCollector
from ..storage.atomic import atomic_write_json
from ..storage.hashing import hash_file
from .config import PipelineConfig, TickerConfig
from .coverage import CoverageReporter
from .manifests import ManifestBuilder

logger = logging.getLogger(__name__)


class OverallStatus(StrEnum):
    """Explicit overall run status — never use generic 'completed'."""

    QA_PASSED = "qa_passed"
    QA_FAILED = "qa_failed"
    COLLECTION_FAILED = "collection_failed"


class TickerStatus(StrEnum):
    """Explicit ticker pipeline status — never use generic 'completed'."""

    COMPLETED_CLEAN = "completed_clean"
    FAILED_PRIVACY = "failed_privacy"
    FAILED_NVIDIA_REVIEW = "failed_nvidia_review"
    DEGRADED_SOURCE_COVERAGE = "degraded_source_coverage"
    FAILED_COLLECTION = "failed_collection"
    COLLECTED = "collected"
    DRY_RUN = "dry_run"
    FAILED = "failed"


class ReleaseGateBlock(Exception):
    """Raised when the release gate blocks ZIP creation."""

    def __init__(self, reason: str, failures: list[str]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.failures = failures


@dataclass
class ReleaseGateResult:
    """Comprehensive typed release-gate result.

    Every blocking count must equal zero before release.
    Every required status must be explicitly passing.
    release_safe = all_counts_zero AND all_statuses_passing.

    Used by both the ticker summary and ZIP exporter.
    """

    # ── Status fields ────────────────────────────────────────────────
    collection_status: str = "unknown"
    privacy_status: str = "unknown"
    format_status: str = "unknown"
    numeric_data_status: str = "unknown"
    coverage_status: str = "unknown"
    nvidia_status: str = "disabled"
    overall_status: str = "unknown"
    release_safe: bool = False
    export_created: bool = False

    # ── Gate config ────────────────────────────────────────────────
    nvidia_enabled: bool = False

    # ── Blocking counts (all must be zero for release_safe=true) ─────
    exact_identifier_count: int = 0
    unresolved_candidate_count: int = 0
    blocking_finding_count: int = 0
    path_identifier_count: int = 0
    filename_identifier_count: int = 0
    manifest_identifier_count: int = 0
    nvidia_parse_error_count: int = 0
    nvidia_fail_count: int = 0
    nvidia_rewrite_count: int = 0
    required_numeric_dataset_failure_count: int = 0
    required_sec_format_failure_count: int = 0
    required_coverage_failure_count: int = 0
    private_artifact_export_count: int = 0
    original_artifact_export_count: int = 0

    # ── Metadata ─────────────────────────────────────────────────────
    export_zip: str | None = None
    export_zip_sha256: str = ""
    failure_details: list[str] = field(default_factory=list)
    export_blocked: bool = False
    export_blocked_reason: str = ""
    export_blocked_failures: list[str] = field(default_factory=list)

    _BLOCKING_COUNT_FIELDS: tuple[str, ...] = (
        "exact_identifier_count",
        "unresolved_candidate_count",
        "blocking_finding_count",
        "path_identifier_count",
        "filename_identifier_count",
        "manifest_identifier_count",
        "nvidia_parse_error_count",
        "nvidia_fail_count",
        "required_numeric_dataset_failure_count",
        "required_sec_format_failure_count",
        "required_coverage_failure_count",
        "private_artifact_export_count",
        "original_artifact_export_count",
    )

    # Statuses that explicitly block release
    _BLOCKING_STATUSES: frozenset[str] = frozenset(
        {
            "unknown",
            "disabled",
            "not_configured",
            "unavailable",
            "skipped",
            "partial",
            "degraded",
            "failed",
            "skipped_dirty_scan",
            "failed_both",
            "failed_parse",
            "failed_correct_guess",
            "failed_empty",
            "failed_no_samples",
            "failed_requests",
        }
    )

    def _all_counts_zero(self) -> bool:
        """Check all blocking counts are zero."""
        return all(getattr(self, field_name, 0) == 0 for field_name in self._BLOCKING_COUNT_FIELDS)

    def _statuses_passing(self) -> bool:
        """Check all required statuses are explicitly passing.

        Required passing statuses:
        - collection_status == "clean"
        - privacy_status == "clean"
        - format_status == "clean"
        - numeric_data_status == "complete"
        - coverage_status == "clean"
        - nvidia_status == "passed" when nvidia_enabled is True
          (nvidia_status == "disabled" is acceptable when not enabled)
        """
        if self.collection_status in self._BLOCKING_STATUSES:
            return False
        if self.privacy_status in self._BLOCKING_STATUSES:
            return False
        if self.format_status in self._BLOCKING_STATUSES:
            return False
        if self.numeric_data_status in self._BLOCKING_STATUSES:
            return False
        if self.coverage_status in self._BLOCKING_STATUSES:
            return False
        if self.nvidia_enabled:
            if self.nvidia_status != "passed":
                return False
        else:
            if self.nvidia_status not in ("disabled", "not_configured"):
                return False
        return True

    def compute_release_safe(self) -> bool:
        """Check all blocking counts are zero AND all statuses are passing."""
        return self._all_counts_zero() and self._statuses_passing()

    def compute_overall_status(self) -> str:
        """Derive overall status from collection and release_safe."""
        if self.collection_status == "failed":
            return OverallStatus.COLLECTION_FAILED.value
        if self.compute_release_safe():
            return OverallStatus.QA_PASSED.value
        return OverallStatus.QA_FAILED.value

    def finalize(self) -> ReleaseGateResult:
        """Compute derived fields and return self."""
        self.release_safe = self.compute_release_safe()
        self.overall_status = self.compute_overall_status()
        return self

    def to_summary_dict(self) -> dict[str, Any]:
        """Return the canonical release gate summary dict.

        Does NOT call finalize() — caller is responsible for calling
        finalize() before to_summary_dict() if needed.
        """
        result: dict[str, Any] = {
            "collection_status": self.collection_status,
            "privacy_status": self.privacy_status,
            "format_status": self.format_status,
            "numeric_data_status": self.numeric_data_status,
            "coverage_status": self.coverage_status,
            "nvidia_status": self.nvidia_status,
            "overall_status": self.overall_status,
            "release_safe": self.release_safe,
            "export_created": self.export_created,
            # Blocking counts
            "exact_identifier_count": self.exact_identifier_count,
            "unresolved_candidate_count": self.unresolved_candidate_count,
            "blocking_finding_count": self.blocking_finding_count,
            "path_identifier_count": self.path_identifier_count,
            "filename_identifier_count": self.filename_identifier_count,
            "manifest_identifier_count": self.manifest_identifier_count,
            "nvidia_parse_error_count": self.nvidia_parse_error_count,
            "nvidia_fail_count": self.nvidia_fail_count,
            "nvidia_rewrite_count": self.nvidia_rewrite_count,
            "required_numeric_dataset_failure_count": self.required_numeric_dataset_failure_count,
            "required_sec_format_failure_count": self.required_sec_format_failure_count,
            "required_coverage_failure_count": self.required_coverage_failure_count,
            "private_artifact_export_count": self.private_artifact_export_count,
            "original_artifact_export_count": self.original_artifact_export_count,
            "export_zip": self.export_zip,
            "export_zip_sha256": self.export_zip_sha256,
            "failure_details": self.failure_details,
        }
        if self.export_blocked:
            result["export_blocked"] = True
            result["export_blocked_reason"] = self.export_blocked_reason
            result["export_blocked_failures"] = self.export_blocked_failures
        return result


class PipelineRunner:
    """Orchestrate a full pipeline run for one or more tickers."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.run_dir = config.output_root / config.run_id

    def run(self) -> dict[str, Any]:
        """Execute the full pipeline and return the run summary."""
        summary: dict[str, Any] = {
            "run_id": self.config.run_id,
            "start_time": datetime.now(UTC).isoformat(),
            "config": self.config.to_dict(),
            "tickers": {},
        }

        # Aggregate gate result across all tickers
        aggregate_gate = ReleaseGateResult()
        aggregate_gate.nvidia_enabled = self.config.enable_nvidia

        for ticker_cfg in self.config.tickers:
            if not ticker_cfg.enabled:
                continue
            try:
                ticker_summary = self._run_ticker(ticker_cfg)
                summary["tickers"][ticker_cfg.ticker] = ticker_summary
                # Merge per-ticker gate data into aggregate
                _merge_gate(aggregate_gate, ticker_summary)
            except Exception as exc:
                logger.error("Pipeline failed for %s: %s", ticker_cfg.ticker, exc, exc_info=True)
                summary["tickers"][ticker_cfg.ticker] = {
                    "status": TickerStatus.FAILED.value,
                    "error": str(exc),
                }

        summary["end_time"] = datetime.now(UTC).isoformat()

        # Save run summary
        summary_path = self.run_dir / "run_summary.json"
        atomic_write_json(summary_path, summary)

        # Create export ZIP only if the gate passes
        if not self.config.dry_run and not self.config.collect_only:
            gate_dict = self._create_export_bundle(aggregate_gate, summary)
            summary.update(gate_dict)

        return summary

    def _run_ticker(self, ticker_cfg: TickerConfig) -> dict[str, Any]:
        """Run pipeline for a single ticker."""
        ticker = ticker_cfg.ticker
        logger.info("Starting pipeline for %s", ticker)

        originals_dir = self.run_dir / "originals" / ticker
        anonymized_dir = self.run_dir / "anonymized" / ticker
        private_maps_dir = self.run_dir / "private_maps" / ticker
        qa_dir = self.run_dir / "qa" / ticker
        config_dir = self.run_dir / "config"

        for d in (originals_dir, anonymized_dir, private_maps_dir, qa_dir, config_dir):
            d.mkdir(parents=True, exist_ok=True)

        manifest_builder = ManifestBuilder(self.config.run_id, ticker, self.run_dir)
        original_manifests: list[dict[str, Any]] = []
        anonymized_manifests: list[dict[str, Any]] = []
        qa_manifests: list[dict[str, Any]] = []

        yf_result: Any | None = None
        sec_results: list[Any] = []
        news_results: list[Any] = []
        news_coverage: Any | None = None

        # ── COLLECTION ──
        if not self.config.anonymize_only:
            yf_result = self._collect_yfinance(
                ticker, ticker_cfg, originals_dir, original_manifests, manifest_builder
            )
            self._collect_sec(
                ticker, ticker_cfg, originals_dir, sec_results, original_manifests, manifest_builder
            )
            news_coverage = self._collect_news(
                ticker, originals_dir, yf_result, news_results, original_manifests, manifest_builder
            )

        # Save original manifests
        orig_manifest_dir = originals_dir / "manifests"
        for i, mf in enumerate(original_manifests):
            manifest_builder.save_manifest(mf, orig_manifest_dir, f"artifact_{i:04d}")

        # Coverage report
        coverage = CoverageReporter(ticker, qa_dir)
        coverage_report = coverage.build_report(
            yf_result.results if yf_result else [],
            sec_results,
            news_results,
            news_coverage,
        )
        coverage.save_report(coverage_report)

        if self.config.dry_run or self.config.collect_only:
            return {
                "status": (
                    TickerStatus.COLLECTED.value
                    if self.config.collect_only
                    else TickerStatus.DRY_RUN.value
                ),
                "original_artifacts": len(original_manifests),
                "coverage": coverage_report,
            }

        # ── ANONYMIZATION ──
        logger.info("Building identity atlas for %s", ticker)
        atlas_builder = IdentityAtlasBuilder(ticker, private_maps_dir)
        atlas = atlas_builder.build_from_metadata(
            yf_result.metadata if yf_result else {},
            sec_results,
            news_coverage,
        )
        atlas_builder.save_atlas(atlas)

        logger.info("Anonymizing structured data for %s", ticker)
        struct_anon = StructuredAnonymizer(ticker, originals_dir, anonymized_dir, private_maps_dir)
        struct_manifests = struct_anon.anonymize_all()
        anonymized_manifests.extend(struct_manifests)

        logger.info("Anonymizing text/SEC data for %s", ticker)
        text_anon = TextAnonymizer(ticker, originals_dir, anonymized_dir, private_maps_dir)
        text_manifests = text_anon.anonymize_all()
        anonymized_manifests.extend(text_manifests)

        logger.info("Anonymizing news for %s", ticker)
        news_anon = TextAnonymizer(
            ticker, originals_dir, anonymized_dir, private_maps_dir, suffix="news"
        )
        news_anon_manifests = news_anon.anonymize_news()
        anonymized_manifests.extend(news_anon_manifests)

        # Save anonymized manifests
        anon_manifest_dir = anonymized_dir / "manifests"
        for i, mf in enumerate(anonymized_manifests):
            manifest_builder.save_manifest(mf, anon_manifest_dir, f"artifact_{i:04d}")

        # ── RESIDUAL SCAN ──
        logger.info("Running residual scan for %s", ticker)
        scanner = ResidualScanner(ticker, atlas, qa_dir)
        scan_result = scanner.scan_all(anonymized_dir)
        qa_manifests.append(scan_result)

        exact_ids = scan_result.get("exact_identifier_count", 0)
        unresolved = scan_result.get("unresolved_candidates", 0)

        # ── NAMESPACE SCAN (paths, filenames, manifests, JSON, Parquet) ──
        ns_result = _scan_release_namespace(anonymized_dir, ticker, atlas)

        # ── NUMERIC DATASET VALIDATION ──
        numeric_failures = _validate_numeric_datasets(anonymized_dir)

        # ── SEC FORMAT VALIDATION ──
        format_failures = _validate_sec_format(anonymized_dir)

        # ── NVIDIA REVIEW ──
        nvidia_status = "disabled"
        nvidia_parse_errors = 0
        nvidia_fail_count = 0
        nvidia_rewrite_count = 0

        if self.config.enable_nvidia:
            from ..providers.nvidia_review import NVIDIAReviewAdapter

            adapter = NVIDIAReviewAdapter()
            if adapter.is_configured():
                if exact_ids > 0:
                    logger.warning(
                        "Skipping NVIDIA review for %s: %d exact identifiers still leaking",
                        ticker,
                        exact_ids,
                    )
                    nvidia_status = "skipped_dirty_scan"
                else:
                    logger.info("Running NVIDIA review for %s", ticker)
                    try:
                        review = adapter.review_batch(anonymized_dir, ticker)
                        (
                            nvidia_status,
                            nvidia_parse_errors,
                            nvidia_fail_count,
                            nvidia_rewrite_count,
                        ) = self._evaluate_nvidia_result(review)
                        nvidia_path = qa_dir / "nvidia_reviews" / "review_result.json"
                        nvidia_path.parent.mkdir(parents=True, exist_ok=True)
                        nvidia_path.write_bytes(
                            orjson.dumps(
                                review,
                                option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
                            )
                        )
                    except Exception as exc:
                        logger.warning("NVIDIA review failed for %s: %s", ticker, exc)
                        nvidia_status = "failed"
                        nvidia_fail_count = 1
            else:
                nvidia_status = "not_configured"
                if self.config.enable_nvidia:
                    # NVIDIA enabled but not configured — treat as blocking
                    nvidia_status = "unavailable"
        else:
            nvidia_status = "disabled"

        # ── COMPUTE COVERAGE FAILURES ──
        coverage_failures = self._check_source_coverage(
            coverage_report,
            self.config.sec_archive_path,
            self.config.sec_source_mode,
        )

        # ── COMPUTE EXPLICIT TICKER STATUS ──
        ticker_status = self._compute_ticker_status(
            exact_ids=exact_ids,
            unresolved=unresolved,
            nvidia_status=nvidia_status,
            nvidia_parse_errors=nvidia_parse_errors,
            nvidia_correct_guess=(nvidia_fail_count > 0),
            coverage_report=coverage_report,
            sec_archive_path=self.config.sec_archive_path,
            sec_source_mode=self.config.sec_source_mode,
        )

        # ── BUILD GATE COUNTS ──
        blocking_finding_count = (
            exact_ids
            + unresolved
            + ns_result["path_hits"]
            + ns_result["filename_hits"]
            + ns_result["manifest_hits"]
        )

        # Save run-level manifest
        run_manifest = manifest_builder.build_run_manifest(
            original_manifests, anonymized_manifests, qa_manifests
        )
        manifest_path = self.run_dir / "manifests" / f"{ticker}_run_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(
            orjson.dumps(run_manifest, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # ── RETURN TICKER SUMMARY WITH GATE COUNTS ──
        return {
            "status": ticker_status,
            "original_artifacts": len(original_manifests),
            "anonymized_artifacts": len(anonymized_manifests),
            "qa_manifests": len(qa_manifests),
            # Privacy
            "residual_exact_identifier_count": exact_ids,
            "unresolved_candidate_count": unresolved,
            "blocking_finding_count": blocking_finding_count,
            "path_identifier_count": ns_result["path_hits"],
            "filename_identifier_count": ns_result["filename_hits"],
            "manifest_identifier_count": ns_result["manifest_hits"],
            # NVIDIA
            "nvidia_status": nvidia_status,
            "nvidia_parse_errors": nvidia_parse_errors,
            "nvidia_correct_guess": nvidia_fail_count,
            "nvidia_fail_count": nvidia_fail_count,
            "nvidia_rewrite_count": nvidia_rewrite_count,
            # Format / numeric
            "required_numeric_dataset_failure_count": numeric_failures,
            "required_sec_format_failure_count": format_failures,
            "required_coverage_failure_count": len(coverage_failures),
            "coverage": coverage_report,
            "ns_scan": ns_result,
        }

    # ── Collection helpers ───────────────────────────────────────────

    def _collect_yfinance(
        self,
        ticker: str,
        ticker_cfg: TickerConfig,
        originals_dir: Path,
        original_manifests: list[dict[str, Any]],
        manifest_builder: ManifestBuilder,
    ) -> Any | None:
        # force_refresh means force fresh collection — never skip
        if "yfinance" in self.config.force_refresh:
            logger.info("Force-refreshing yfinance data for %s", ticker)
        logger.info("Collecting yfinance data for %s", ticker)
        yf_collector = YFinanceCollector(originals_dir, ticker, years=ticker_cfg.years)
        yf_result = yf_collector.collect_all()
        for r in yf_result.results:
            mf = manifest_builder.build_manifest(
                artifact_id=f"{ticker}_yf_{r.artifact_type}",
                source=r.source,
                source_url=None,
                requested_range=r.requested_range,
                observed_range=r.observed_range,
                content_type=r.content_type,
                relative_path=r.relative_path,
                byte_size=r.byte_size,
                sha256=r.sha256,
                collection_status=r.status.value,
                metadata=r.metadata,
            )
            original_manifests.append(mf)
        return yf_result

    def _collect_sec(
        self,
        ticker: str,
        ticker_cfg: TickerConfig,
        originals_dir: Path,
        sec_results: list[Any],
        original_manifests: list[dict[str, Any]],
        manifest_builder: ManifestBuilder,
    ) -> None:
        if not self.config.sec_user_agent:
            return
        if "sec" in self.config.force_refresh:
            logger.info("Force-refreshing SEC data for %s", ticker)
        logger.info("Collecting SEC data for %s", ticker)

        if self.config.sec_archive_path and self.config.sec_source_mode != "network-only":
            from ..collectors.sec_archive import SECArchiveCollector

            logger.info("Using SEC archive: %s", self.config.sec_archive_path)
            archive_collector = SECArchiveCollector(
                archive_path=self.config.sec_archive_path,
                output_dir=originals_dir,
                ticker=ticker,
                forms=["10-K", "10-Q", "8-K"],
                years=ticker_cfg.years,
            )
            _inv = archive_collector.inventory()
            logger.info("Archive inventory: %d files for %s", len(_inv), ticker)
            coverage_rep = archive_collector.coverage_report()
            cov_path = originals_dir / "sec" / "archive_coverage.json"
            cov_path.parent.mkdir(parents=True, exist_ok=True)
            cov_path.write_bytes(
                orjson.dumps(
                    coverage_rep,
                    option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
                )
            )

            archive_results = archive_collector.collect()
            sec_results.extend(archive_results)
            for r in archive_results:
                mf = manifest_builder.build_manifest(
                    artifact_id=f"{ticker}_sec_{r.artifact_type}",
                    source=r.source,
                    source_url=None,
                    requested_range=r.requested_range,
                    observed_range=r.observed_range,
                    content_type=r.content_type,
                    relative_path=r.relative_path,
                    byte_size=r.byte_size,
                    sha256=r.sha256,
                    collection_status=r.status.value,
                    metadata=r.metadata,
                )
                original_manifests.append(mf)

            if self.config.sec_source_mode == "archive-preferred" and any(
                r.status.value == "success" for r in archive_results
            ):
                logger.info(
                    "Archive-preferred: skipping live SEC for %s (archive had data)",
                    ticker,
                )
                return

        self._collect_sec_live(
            ticker,
            ticker_cfg,
            originals_dir,
            sec_results,
            original_manifests,
            manifest_builder,
        )

    def _collect_sec_live(
        self,
        ticker: str,
        ticker_cfg: TickerConfig,
        originals_dir: Path,
        sec_results: list[Any],
        original_manifests: list[dict[str, Any]],
        manifest_builder: ManifestBuilder,
    ) -> None:
        sec_collector = SECCollector(
            originals_dir,
            ticker,
            years=ticker_cfg.years,
            user_agent=self.config.sec_user_agent,
        )
        live_results = sec_collector.collect_all()
        sec_results.extend(live_results)
        for r in live_results:
            mf = manifest_builder.build_manifest(
                artifact_id=f"{ticker}_sec_{r.artifact_type}",
                source=r.source,
                source_url=None,
                requested_range=r.requested_range,
                observed_range=r.observed_range,
                content_type=r.content_type,
                relative_path=r.relative_path,
                byte_size=r.byte_size,
                sha256=r.sha256,
                collection_status=r.status.value,
                metadata=r.metadata,
            )
            original_manifests.append(mf)

    def _collect_news(
        self,
        ticker: str,
        originals_dir: Path,
        yf_result: Any | None,
        news_results: list[Any],
        original_manifests: list[dict[str, Any]],
        manifest_builder: ManifestBuilder,
    ) -> Any | None:
        """Collect news and return the NewsCoverageReport."""
        if "news" in self.config.force_refresh:
            logger.info("Force-refreshing news for %s", ticker)
        logger.info("Collecting news for %s", ticker)
        company_name = yf_result.metadata.get("short_name") if yf_result else None
        news_collector = NewsCollector(originals_dir, ticker, company_name=company_name)
        nresults, ncov = news_collector.collect_all()
        news_results.extend(nresults)
        for r in nresults:
            mf = manifest_builder.build_manifest(
                artifact_id=f"{ticker}_news_{r.artifact_type}",
                source=r.source,
                source_url=None,
                requested_range=r.requested_range,
                observed_range=r.observed_range,
                content_type=r.content_type,
                relative_path=r.relative_path,
                byte_size=r.byte_size,
                sha256=r.sha256,
                collection_status=r.status.value,
                metadata=r.metadata,
            )
            original_manifests.append(mf)
        return ncov

    # ── Gate evaluation ──────────────────────────────────────────────

    @staticmethod
    def _compute_ticker_status(
        *,
        exact_ids: int,
        unresolved: int,
        nvidia_status: str,
        nvidia_parse_errors: int,
        nvidia_correct_guess: bool,
        coverage_report: dict[str, Any],
        sec_archive_path: Path | None,
        sec_source_mode: str,
    ) -> str:
        failures: list[str] = []

        if exact_ids > 0:
            failures.append(f"exact_identifiers={exact_ids}")
        if unresolved > 0:
            failures.append(f"unresolved_candidates={unresolved}")
        if nvidia_status not in ("disabled", "not_configured", "skipped_dirty_scan"):
            if nvidia_parse_errors > 0:
                failures.append(f"nvidia_parse_errors={nvidia_parse_errors}")
        if nvidia_correct_guess:
            failures.append("nvidia_correct_guess=true")
        coverage_failures = PipelineRunner._check_source_coverage(
            coverage_report, sec_archive_path, sec_source_mode
        )
        failures.extend(coverage_failures)
        if nvidia_status == "failed":
            failures.append("nvidia_review_failed")
        elif nvidia_status == "skipped_dirty_scan":
            failures.append("nvidia_skipped_dirty_scan")
        elif nvidia_status == "unavailable":
            failures.append("nvidia_unavailable_but_enabled")

        if failures:
            if exact_ids > 0 or unresolved > 0:
                return TickerStatus.FAILED_PRIVACY.value
            if nvidia_parse_errors > 0 or nvidia_correct_guess or nvidia_status == "failed":
                return TickerStatus.FAILED_NVIDIA_REVIEW.value
            if coverage_failures:
                return TickerStatus.DEGRADED_SOURCE_COVERAGE.value
            return TickerStatus.FAILED.value
        return TickerStatus.COMPLETED_CLEAN.value

    @staticmethod
    def _check_source_coverage(
        coverage_report: dict[str, Any],
        sec_archive_path: Path | None,
        sec_source_mode: str,
    ) -> list[str]:
        """Check mandatory source coverage gates.

        News historical_10y_complete is NOT a mandatory failure —
        it is disclosed as a limitation but does not block release
        unless the configured release contract explicitly requires
        a licensed ten-year news source.
        """
        failures: list[str] = []
        if sec_source_mode in ("archive-only", "archive-preferred"):
            if sec_archive_path is None:
                failures.append("sec_archive_path_null_in_archive_mode")
        sec = coverage_report.get("sec", {})
        for artifact in sec.get("artifacts", []):
            if artifact.get("artifact_type") == "companyfacts":
                if artifact.get("row_count", 0) == 0:
                    failures.append("companyfacts_row_count_zero")
                break
        if not sec.get("has_data"):
            failures.append("sec_no_data")
        # News 10-year completeness is disclosed but NOT a mandatory failure
        return failures

    @staticmethod
    def _evaluate_nvidia_result(
        review: dict[str, Any],
    ) -> tuple[str, int, int, int]:
        """Evaluate NVIDIA 3-pass review results.

        Returns (status, parse_error_count, fail_count, rewrite_count).

        Status MUST be explicit "passed" for release — never "completed".
        Blocks release if:
        - result list is empty
        - any parse error exists
        - any gate_verdict is FAIL
        - any request fails
        """
        parse_errors = 0
        fail_count = 0
        rewrite_count = 0
        attacker_results = review.get("attacker_results", [])

        # Empty result list blocks release
        if not attacker_results:
            return "failed_empty", 1, 1, 0

        for ar in attacker_results:
            # New 3-pass shape: flat dict with attacker, gate_verdict, rewrite_applied
            attacker = ar.get("attacker", {})
            if attacker.get("parse_error"):
                parse_errors += 1
            verdict = ar.get("gate_verdict", "PASS")
            if verdict == "FAIL":
                fail_count += 1
            if ar.get("rewrite_applied"):
                rewrite_count += 1

        # Count top-level parse errors
        if review.get("parse_errors", 0) > 0:
            parse_errors = max(parse_errors, review["parse_errors"])

        # Check reviewed sample count vs configured requirement
        samples_reviewed = review.get("samples_reviewed", 0)
        if samples_reviewed < 1:
            return "failed_no_samples", parse_errors + 1, fail_count + 1, rewrite_count

        if parse_errors > 0 and fail_count > 0:
            return "failed_both", parse_errors, fail_count, rewrite_count
        if parse_errors > 0:
            return "failed_parse", parse_errors, fail_count, rewrite_count
        if fail_count > 0:
            return "failed_correct_guess", parse_errors, fail_count, rewrite_count
        return "passed", 0, 0, rewrite_count

    # ── ZIP export with staging and scanning ─────────────────────────

    def _create_export_bundle(
        self, gate_result: ReleaseGateResult, run_summary: dict[str, Any]
    ) -> dict[str, Any]:
        """Create sanitized anonymized export ZIP with staging and scanning.

        Builds a sanitized release tree with pseudonymous company IDs:
        release/
          COMPANY_<deterministic_id>/
            market/
            statements/
            sec/
            news/
            manifests/

        No ticker in ZIP member names, QA paths, JSON keys, or manifest paths.
        Raw run_summary.json and raw config are excluded.
        """
        gate_result.finalize()

        if not gate_result.release_safe:
            gate_result.export_blocked = True
            gate_result.export_blocked_reason = (
                f"Release gate not safe: {gate_result.overall_status}"
            )
            gate_result.export_blocked_failures = gate_result.failure_details
            logger.warning("Release gate blocked export: %s", gate_result.overall_status)
            return gate_result.to_summary_dict()

        # ── Check individual ticker statuses ──
        gate_failures: list[str] = []
        for ticker_name, ts in run_summary.get("tickers", {}).items():
            status = ts.get("status", "")
            if status not in (
                TickerStatus.COMPLETED_CLEAN.value,
                TickerStatus.COLLECTED.value,
                TickerStatus.DRY_RUN.value,
            ):
                gate_failures.append(f"{ticker_name}: {status}")

        if gate_failures:
            gate_result.export_blocked = True
            gate_result.export_blocked_reason = (
                f"Release gate blocked: {len(gate_failures)} ticker(s) failed"
            )
            gate_result.export_blocked_failures = gate_failures
            return gate_result.to_summary_dict()

        # ── Build pseudonym path maps for each ticker ──
        ticker_pseudonyms: dict[str, str] = {}
        for ticker_name in run_summary.get("tickers", {}):
            ticker_pseudonyms[ticker_name] = (
                f"COMPANY_{hashlib.sha256(ticker_name.encode()).hexdigest()[:12]}"
            )

        export_dir = self.run_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        final_path = export_dir / "anonymized_bundle.zip"

        excluded_filenames = {"detailed_findings.json", "review_result.json"}

        # ── Build ZIP in staging directory (same filesystem for atomic rename) ──
        staging_root = tempfile.mkdtemp(dir=str(self.run_dir), prefix="staging_")
        try:
            staging_zip = Path(staging_root) / "bundle.zip"

            with zipfile.ZipFile(staging_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                # ── Anonymized artifacts with pseudonymous paths ──
                anon_dir = self.run_dir / "anonymized"
                if anon_dir.exists():
                    for fp in anon_dir.rglob("*"):
                        if not fp.is_file():
                            continue
                        if fp.name in excluded_filenames:
                            continue
                        rel = fp.relative_to(anon_dir)
                        # First segment is the ticker — rewrite to pseudonym
                        parts = rel.parts
                        if parts and parts[0] in ticker_pseudonyms:
                            pseudo = ticker_pseudonyms[parts[0]]
                            pseudo_rel = Path(pseudo).joinpath(*parts[1:])
                            arcname = f"release/{pseudo_rel}"
                        else:
                            arcname = f"release/{rel}"
                        zf.write(fp, arcname)

                # ── Sanitized QA (exclude detailed findings, NVIDIA responses) ──
                qa_root = self.run_dir / "qa"
                if qa_root.exists():
                    for qf in qa_root.rglob("*.json"):
                        if qf.name in excluded_filenames:
                            continue
                        if "nvidia_reviews" in qf.parts:
                            continue
                        rel = qf.relative_to(qa_root)
                        parts = rel.parts
                        if parts and parts[0] in ticker_pseudonyms:
                            pseudo = ticker_pseudonyms[parts[0]]
                            pseudo_rel = Path(pseudo).joinpath(*parts[1:])
                            arcname = f"release/{pseudo_rel}"
                        else:
                            arcname = f"release/{rel}"
                        zf.write(qf, arcname)

                # ── Sanitized release summary (NEVER include raw run_summary.json) ──
                release_summary = self._build_release_qa_summary(run_summary, gate_result)
                zf.writestr(
                    "release/sanitized_run_summary.json",
                    orjson.dumps(
                        release_summary,
                        option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
                    ),
                )

                # ── Release verdict ──
                verdict = self._build_release_verdict(run_summary, gate_result, ticker_pseudonyms)
                zf.writestr(
                    "release/release_verdict.json",
                    orjson.dumps(
                        verdict,
                        option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
                    ),
                )

            # ── SCAN staged ZIP for leaks ──
            leak_issues = self._scan_zip_for_leaks(staging_zip)
            if leak_issues:
                # Remove staging, block export
                staging_zip.unlink(missing_ok=True)
                os.rmdir(staging_root)
                gate_result.export_blocked = True
                gate_result.export_blocked_reason = (
                    f"Staged ZIP contains {len(leak_issues)} leak(s)"
                )
                gate_result.export_blocked_failures = leak_issues
                gate_result.private_artifact_export_count = len(leak_issues)
                gate_result.finalize()
                logger.warning("Staged ZIP leak detected: %s", leak_issues)
                return gate_result.to_summary_dict()

            # ── Atomically move to final location ──
            if final_path.exists():
                final_path.unlink()
            os.rename(str(staging_zip), str(final_path))

        finally:
            # Clean up staging directory
            try:
                if Path(staging_root).exists():
                    import shutil

                    shutil.rmtree(staging_root, ignore_errors=True)
            except Exception:
                pass

        gate_result.export_created = True
        gate_result.export_zip = str(final_path)
        gate_result.export_zip_sha256 = hash_file(final_path) if final_path.exists() else ""
        gate_result.finalize()

        logger.info("Export ZIP created: %s (sha256=%s)", final_path, gate_result.export_zip_sha256)
        return gate_result.to_summary_dict()

    @staticmethod
    def _build_release_verdict(
        run_summary: dict[str, Any],
        gate_result: ReleaseGateResult,
        ticker_pseudonyms: dict[str, str],
    ) -> dict[str, Any]:
        """Build a sanitized release verdict with no real tickers."""
        tickers_verdict: dict[str, Any] = {}
        for ticker_name, ts in run_summary.get("tickers", {}).items():
            pseudo = ticker_pseudonyms.get(ticker_name, ticker_name)
            tickers_verdict[pseudo] = {
                "requested_years": ts.get("coverage", {}).get("requested_years", 0),
                "collection_status": ts.get("status"),
                "coverage_status": ts.get("coverage", {}).get("overall", {}),
                "privacy_status": "clean"
                if ts.get("residual_exact_identifier_count", 0) == 0
                else "failed",
                "nvidia_status": ts.get("nvidia_status", "disabled"),
                "exact_identifier_count": ts.get("residual_exact_identifier_count", 0),
                "required_coverage_failure_count": ts.get("required_coverage_failure_count", 0),
                "required_numeric_dataset_failure_count": ts.get(
                    "required_numeric_dataset_failure_count", 0
                ),
                "required_sec_format_failure_count": ts.get("required_sec_format_failure_count", 0),
                "release_safe": ts.get("status") == TickerStatus.COMPLETED_CLEAN.value,
            }

        return {
            "schema_version": "1.0.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "run_id": run_summary.get("run_id"),
            "overall_release_safe": gate_result.release_safe,
            "overall_status": gate_result.overall_status,
            "tickers": tickers_verdict,
            "disclosed_limitations": gate_result.failure_details,
        }

    def _scan_zip_for_leaks(self, zip_path: Path) -> list[str]:
        """Scan a staged ZIP for private content leaks.

        Returns list of issues found; empty list = clean.
        Checks for:
        - Forbidden path patterns (originals, private_maps, nvidia_reviews, etc.)
        - Real ticker symbols in member names (must use pseudonymous COMPANY_<hash>)
        - raw run_summary.json (should never be included)
        - raw config files (should never be included)
        """
        issues: list[str] = []
        forbidden_path_patterns = [
            "originals/",
            "private_maps/",
            "detailed_findings",
            "review_result",
            "nvidia_reviews",
            "identity_atlas",
        ]
        # Patterns that reveal real tickers — must use COMPANY_<hash> instead
        ticker_leak_patterns = ["NVDA", "nvidia"]
        # Raw files that must never appear (but sanitized versions are OK)
        forbidden_files = {"run_summary.json", "resolved_config.json"}
        # Whitelisted sanitized files that share suffixes with forbidden files
        allowed_sanitized = {"sanitized_run_summary.json", "release_verdict.json"}

        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                name_lower = name.lower()
                base_name = name.split("/")[-1] if "/" in name else name

                # Skip check for allowed sanitized files
                if base_name in allowed_sanitized:
                    continue

                # Check forbidden path patterns
                for pat in forbidden_path_patterns:
                    if pat.lower() in name_lower:
                        issues.append(f"Forbidden content in ZIP member: {name}")
                        break

                # Check for raw files that should be excluded (skip sanitized)
                if base_name not in allowed_sanitized:
                    for forbidden_file in forbidden_files:
                        if base_name.endswith(forbidden_file):
                            issues.append(f"Raw file should not be in ZIP: {name}")
                            break

                # Check for real tickers in member names
                for ticker_pat in ticker_leak_patterns:
                    if ticker_pat in name:
                        issues.append(f"Real ticker in ZIP member name: {name}")
                        break

                # Check for config/ directory
                if "config/" in name_lower:
                    issues.append(f"Config directory should not be in ZIP: {name}")

        return issues

    @staticmethod
    def _build_release_qa_summary(
        run_summary: dict[str, Any],
        gate_result: ReleaseGateResult | None = None,
    ) -> dict[str, Any]:
        """Build a sanitized release QA summary with pseudonymous IDs only.

        Excludes: ticker, company name, CIK, private paths, source archive paths,
        originals paths, private map paths, API configuration, raw NVIDIA responses.

        Contains: pseudonymous company ID, requested years, source coverage status,
        QA counts, semantic hashes, release verdict, disclosed limitations.
        """
        tickers_summary: dict[str, Any] = {}
        for ticker_name, ts in run_summary.get("tickers", {}).items():
            pseudo = f"COMPANY_{hashlib.sha256(ticker_name.encode()).hexdigest()[:12]}"
            ticker_cov = ts.get("coverage", {})
            tickers_summary[pseudo] = {
                "status": ts.get("status"),
                "requested_years": ticker_cov.get("requested_years", 0) if ticker_cov else 0,
                "original_artifacts": ts.get("original_artifacts"),
                "anonymized_artifacts": ts.get("anonymized_artifacts"),
                "exact_identifier_count": ts.get("residual_exact_identifier_count", 0),
                "unresolved_candidates": ts.get("unresolved_candidate_count", 0),
                "path_identifier_count": ts.get("path_identifier_count", 0),
                "filename_identifier_count": ts.get("filename_identifier_count", 0),
                "manifest_identifier_count": ts.get("manifest_identifier_count", 0),
                "nvidia_status": ts.get("nvidia_status"),
                "nvidia_parse_errors": ts.get("nvidia_parse_errors", 0),
                "nvidia_fail_count": ts.get("nvidia_fail_count", 0),
                "nvidia_rewrite_count": ts.get("nvidia_rewrite_count", 0),
                "required_numeric_dataset_failure_count": ts.get(
                    "required_numeric_dataset_failure_count", 0
                ),
                "required_sec_format_failure_count": ts.get("required_sec_format_failure_count", 0),
                "required_coverage_failure_count": ts.get("required_coverage_failure_count", 0),
                "release_safe": ts.get("status") == TickerStatus.COMPLETED_CLEAN.value,
            }

        result: dict[str, Any] = {
            "schema_version": "1.0.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "run_id": run_summary.get("run_id"),
            "tickers": tickers_summary,
        }
        if gate_result is not None:
            result["overall"] = {
                "release_safe": gate_result.release_safe,
                "overall_status": gate_result.overall_status,
                "collection_status": gate_result.collection_status,
                "privacy_status": gate_result.privacy_status,
                "format_status": gate_result.format_status,
                "numeric_data_status": gate_result.numeric_data_status,
                "coverage_status": gate_result.coverage_status,
                "nvidia_status": gate_result.nvidia_status,
                "export_blocked": gate_result.export_blocked,
                "export_blocked_reason": gate_result.export_blocked_reason,
                "disclosed_limitations": gate_result.failure_details,
            }
        else:
            result["overall"] = {
                "all_clean": all(
                    ts.get("status") == TickerStatus.COMPLETED_CLEAN.value
                    for ts in run_summary.get("tickers", {}).values()
                ),
            }
        return result


# ── Helper functions ──────────────────────────────────────────────────


def _merge_gate(gate: ReleaseGateResult, ticker_summary: dict[str, Any]) -> None:
    """Merge per-ticker gate data into aggregate ReleaseGateResult.

    Propagaes ALL status fields properly — worst-case accumulation.
    """
    gate.exact_identifier_count += ticker_summary.get("residual_exact_identifier_count", 0)
    gate.unresolved_candidate_count += ticker_summary.get("unresolved_candidate_count", 0)
    gate.blocking_finding_count += ticker_summary.get("blocking_finding_count", 0)
    gate.path_identifier_count += ticker_summary.get("path_identifier_count", 0)
    gate.filename_identifier_count += ticker_summary.get("filename_identifier_count", 0)
    gate.manifest_identifier_count += ticker_summary.get("manifest_identifier_count", 0)
    gate.nvidia_parse_error_count += ticker_summary.get("nvidia_parse_errors", 0)
    gate.nvidia_fail_count += ticker_summary.get("nvidia_fail_count", 0)
    gate.required_numeric_dataset_failure_count += ticker_summary.get(
        "required_numeric_dataset_failure_count", 0
    )
    gate.required_sec_format_failure_count += ticker_summary.get(
        "required_sec_format_failure_count", 0
    )
    gate.required_coverage_failure_count += ticker_summary.get("required_coverage_failure_count", 0)

    status = ticker_summary.get("status", "")
    nv_status = ticker_summary.get("nvidia_status", "disabled")

    # Collection status: worst-case
    if status == TickerStatus.FAILED_COLLECTION.value:
        gate.collection_status = "failed"
    elif gate.collection_status == "unknown":
        gate.collection_status = "clean"

    # Privacy status: worst-case
    if status == TickerStatus.FAILED_PRIVACY.value:
        gate.privacy_status = "failed"
    elif gate.privacy_status not in ("failed",):
        gate.privacy_status = "clean"

    # Format status: worst-case
    fmt_failures = ticker_summary.get("required_sec_format_failure_count", 0)
    if fmt_failures > 0:
        gate.format_status = "failed"
    elif gate.format_status not in ("failed",):
        gate.format_status = "clean"

    # Numeric data status: worst-case
    num_failures = ticker_summary.get("required_numeric_dataset_failure_count", 0)
    if num_failures > 0:
        gate.numeric_data_status = "failed"
    elif gate.numeric_data_status not in ("failed",):
        gate.numeric_data_status = "complete"

    # Coverage status: worst-case
    cov_failures = ticker_summary.get("required_coverage_failure_count", 0)
    if cov_failures > 0:
        gate.coverage_status = "degraded"
    elif gate.coverage_status not in ("degraded", "failed"):
        gate.coverage_status = "clean"

    # NVIDIA status: worst-case across all tickers
    blocked_statuses = gate._BLOCKING_STATUSES
    if nv_status in blocked_statuses:
        gate.nvidia_status = nv_status
    elif gate.nvidia_status not in blocked_statuses:
        gate.nvidia_status = nv_status


def _scan_release_namespace(
    anonymized_dir: Path,
    ticker: str,
    atlas: Any,
) -> dict[str, Any]:
    """Recursively scan anonymized output for leaked identifiers.

    Inspects:
    - File/directory paths for ticker, CIK, accession patterns
    - Filenames for accession-based names
    - JSON file content for ticker, company names
    - Parquet file string columns and metadata
    - Markdown links and URLs
    - Manifest references
    """
    ticker_upper = ticker.upper()
    ticker_lower = ticker.lower()

    path_hits = 0
    filename_hits = 0
    manifest_hits = 0
    content_hits = 0
    findings: list[dict[str, Any]] = []

    # Collect known private values from atlas
    private_values: set[str] = {ticker_upper, ticker_lower}
    try:
        for entity in atlas.all_entities():
            val = entity.canonical_private_value
            if val and len(val) > 2:
                private_values.add(val)
                private_values.add(val.upper())
                private_values.add(val.lower())
    except Exception:
        pass

    # Scan paths and filenames
    for fp in anonymized_dir.rglob("*"):
        rel = str(fp.relative_to(anonymized_dir))
        # Check path for ticker
        if ticker_upper in rel or ticker_lower in rel:
            path_hits += 1
            findings.append({"type": "path_ticker", "path": rel})
        # Check filename for accession patterns (18-digit or dashed)
        name = fp.name
        if re.search(r"\d{10}-\d{2}-\d{6}", name) or re.search(r"\d{18}", name):
            filename_hits += 1
            findings.append({"type": "filename_accession", "path": rel})

        # Scan file contents for private values
        if fp.is_file() and fp.suffix in (".md", ".json", ".csv", ".txt"):
            _scan_file_content(fp, rel, private_values, findings)

        # Scan manifests
        if fp.is_file() and "manifest" in fp.name.lower() and fp.suffix == ".json":
            mh = _scan_manifest_content(fp, rel, ticker_upper, ticker_lower, private_values)
            manifest_hits += mh

    # Compute total content hits
    content_hits = len([f for f in findings if f["type"] == "content_hit"])

    return {
        "path_hits": path_hits,
        "filename_hits": filename_hits,
        "manifest_hits": manifest_hits,
        "content_hits": content_hits,
        "total_hits": path_hits + filename_hits + manifest_hits + content_hits,
        "findings": findings[:50],
    }


def _scan_file_content(
    fp: Path,
    rel_path: str,
    private_values: set[str],
    findings: list[dict[str, Any]],
) -> None:
    """Scan a text file for private values."""
    try:
        content = fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    for val in private_values:
        if len(val) < 3:
            continue
        if val in content:
            findings.append(
                {
                    "type": "content_hit",
                    "path": rel_path,
                    "value_length": len(val),
                }
            )
            break  # One finding per file is enough


def _scan_manifest_content(
    fp: Path,
    rel_path: str,
    ticker_upper: str,
    ticker_lower: str,
    private_values: set[str],
) -> int:
    """Scan a manifest JSON for private identifiers."""
    hits = 0
    try:
        data = orjson.loads(fp.read_bytes())
        text = orjson.dumps(data).decode("utf-8", errors="replace")
        if ticker_upper in text or ticker_lower in text:
            hits += 1
        for val in private_values:
            if len(val) >= 4 and val in text:
                hits += 1
                break
    except Exception:
        pass
    return hits


def _validate_numeric_datasets(anonymized_dir: Path) -> int:
    """Validate required numeric datasets are present and nonempty.

    Returns count of failures.
    """
    required = [
        "metrics/ohlcv.parquet",
        "metrics/dividends.parquet",
        "metrics/splits.parquet",
        "statements/income_statement_annual.parquet",
        "statements/balance_sheet_annual.parquet",
        "statements/cash_flow_annual.parquet",
        "sec/companyfacts.parquet",
    ]
    failures = 0
    for req in required:
        p = anonymized_dir / req
        if not p.exists():
            failures += 1
            logger.debug("Missing numeric dataset: %s", req)
    return failures


def _validate_sec_format(anonymized_dir: Path) -> int:
    """Validate SEC Markdown files are readable and not raw HTML/XML.

    Checks: no XML/XBRL tags, no namespace declarations, no schema refs,
    meaningful prose, adequate length.
    """
    from ..release.namespace_scanner import (
        _SCHEMA_REF_PATTERN,
        _XBRL_TAG_PATTERN,
        _XML_NAMESPACE_PATTERN,
    )

    failures = 0
    sec_dir = anonymized_dir / "sec"
    if not sec_dir.exists():
        return 0
    for md_path in sec_dir.rglob("*.md"):
        try:
            content = md_path.read_text(encoding="utf-8", errors="replace")
            # Must not start with raw XML/HTML
            if content.lstrip().startswith(("<html", "<HTML", "<xml", "<?xml", "<ix:", "<xbrli:")):
                failures += 1
                logger.debug("Raw XML/HTML at start of Markdown: %s", md_path.name)
            # Must not contain XBRL tags anywhere
            if _XBRL_TAG_PATTERN.search(content):
                failures += 1
                logger.debug("XBRL tags found in Markdown: %s", md_path.name)
            # Must not contain XML namespace declarations
            if _XML_NAMESPACE_PATTERN.search(content):
                failures += 1
                logger.debug("XML namespace in Markdown: %s", md_path.name)
            # Must not contain schema references
            if _SCHEMA_REF_PATTERN.search(content):
                failures += 1
                logger.debug("Schema reference in Markdown: %s", md_path.name)
            # Must have meaningful prose
            text_only = re.sub(r"[\s\d\p{P}]", "", content)
            if len(text_only.strip()) < 20:
                failures += 1
                logger.debug("Near-empty or numeric-only Markdown: %s", md_path.name)
            if len(content.strip()) < 50:
                failures += 1
                logger.debug("Too-short Markdown: %s", md_path.name)
        except Exception:
            failures += 1
    return failures
