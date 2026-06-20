"""Pipeline runner orchestrating collection, anonymization, manifests, and QA."""

from __future__ import annotations

import logging
import zipfile
from datetime import UTC, datetime
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


class PipelineRunner:
    """Orchestrate a full pipeline run for one or more tickers."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.run_dir = config.output_root / config.run_id

    def run(self) -> dict[str, Any]:
        """Execute the full pipeline."""
        summary: dict[str, Any] = {
            "run_id": self.config.run_id,
            "start_time": datetime.now(UTC).isoformat(),
            "config": self.config.to_dict(),
            "tickers": {},
        }

        for ticker_cfg in self.config.tickers:
            if not ticker_cfg.enabled:
                continue
            try:
                ticker_summary = self._run_ticker(ticker_cfg)
                summary["tickers"][ticker_cfg.ticker] = ticker_summary
            except Exception as exc:
                logger.error("Pipeline failed for %s: %s", ticker_cfg.ticker, exc, exc_info=True)
                summary["tickers"][ticker_cfg.ticker] = {
                    "status": "failed",
                    "error": str(exc),
                }

        summary["end_time"] = datetime.now(UTC).isoformat()

        # Save run summary
        summary_path = self.run_dir / "run_summary.json"
        atomic_write_json(summary_path, summary)

        # Create export ZIP
        if not self.config.dry_run and not self.config.collect_only:
            export_path = self._create_export_bundle()
            summary["export_zip"] = str(export_path)
            summary["export_zip_sha256"] = hash_file(export_path) if export_path.exists() else ""

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

        originals_dir.mkdir(parents=True, exist_ok=True)
        anonymized_dir.mkdir(parents=True, exist_ok=True)
        private_maps_dir.mkdir(parents=True, exist_ok=True)
        qa_dir.mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)

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
            # YFinance
            if "yfinance" not in self.config.force_refresh:
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

            # SEC
            if "sec" not in self.config.force_refresh and self.config.sec_user_agent:
                logger.info("Collecting SEC data for %s", ticker)

                # Check for archive usage
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
                    # Inventory and coverage report (informational)
                    _inv = archive_collector.inventory()
                    logger.info(
                        "Archive inventory: %d files for %s",
                        len(_inv),
                        ticker,
                    )
                    coverage_rep = archive_collector.coverage_report()
                    cov_path = originals_dir / "sec" / "archive_coverage.json"
                    cov_path.parent.mkdir(parents=True, exist_ok=True)
                    import orjson as _orjson

                    cov_path.write_bytes(
                        _orjson.dumps(
                            coverage_rep,
                            option=_orjson.OPT_SORT_KEYS | _orjson.OPT_INDENT_2,
                        )
                    )

                    # Collect from archive
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

                    # If archive-preferred, skip live SEC if archive had data
                    if self.config.sec_source_mode == "archive-preferred" and any(
                        r.status.value == "success" for r in archive_results
                    ):
                        logger.info(
                            "Archive-preferred: skipping live SEC for %s (archive had data)",
                            ticker,
                        )
                    else:
                        # Fall through to live SEC when archive didn't have data
                        self._collect_sec_live(
                            ticker,
                            ticker_cfg,
                            originals_dir,
                            sec_results,
                            original_manifests,
                            manifest_builder,
                        )
                else:
                    self._collect_sec_live(
                        ticker,
                        ticker_cfg,
                        originals_dir,
                        sec_results,
                        original_manifests,
                        manifest_builder,
                    )

            # News
            if "news" not in self.config.force_refresh:
                logger.info("Collecting news for %s", ticker)
                company_name = yf_result.metadata.get("short_name") if yf_result else None
                news_collector = NewsCollector(originals_dir, ticker, company_name=company_name)
                news_results, news_coverage = news_collector.collect_all()
                for r in news_results:
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
                "status": "collected" if self.config.collect_only else "dry_run",
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

        # Save run-level manifest
        run_manifest = manifest_builder.build_run_manifest(
            original_manifests, anonymized_manifests, qa_manifests
        )
        manifest_path = self.run_dir / "manifests" / f"{ticker}_run_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(
            orjson.dumps(run_manifest, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )

        # ── NVIDIA REVIEW (optional) ──
        nvidia_status = "disabled"
        if self.config.enable_nvidia:
            from ..providers.nvidia_review import NVIDIAReviewAdapter

            adapter = NVIDIAReviewAdapter()
            if adapter.is_configured():
                logger.info("Running NVIDIA review for %s", ticker)
                try:
                    review = adapter.review_batch(anonymized_dir, ticker)
                    nvidia_status = "completed"
                    nvidia_path = qa_dir / "nvidia_reviews" / "review_result.json"
                    nvidia_path.parent.mkdir(parents=True, exist_ok=True)
                    nvidia_path.write_bytes(
                        orjson.dumps(review, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
                    )
                except Exception as exc:
                    logger.warning("NVIDIA review failed for %s: %s", ticker, exc)
                    nvidia_status = "failed"
            else:
                nvidia_status = "not_configured"

        return {
            "status": "completed",
            "original_artifacts": len(original_manifests),
            "anonymized_artifacts": len(anonymized_manifests),
            "qa_manifests": len(qa_manifests),
            "residual_exact_identifier_count": scan_result.get("exact_identifier_count", 0),
            "unresolved_candidate_count": scan_result.get("unresolved_candidates", 0),
            "nvidia_status": nvidia_status,
            "coverage": coverage_report,
        }

    def _collect_sec_live(
        self,
        ticker: str,
        ticker_cfg: TickerConfig,
        originals_dir: Path,
        sec_results: list[Any],
        original_manifests: list[dict[str, Any]],
        manifest_builder: ManifestBuilder,
    ) -> None:
        """Collect SEC data via live EDGAR access."""
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

    def _create_export_bundle(self) -> Path:
        """Create anonymized export ZIP excluding originals and private maps."""
        export_path = self.run_dir / "exports" / "anonymized_bundle.zip"
        export_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Only include anonymized artifacts
            anon_dir = self.run_dir / "anonymized"
            if anon_dir.exists():
                for file_path in anon_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = str(file_path.relative_to(self.run_dir))
                        zf.write(file_path, arcname)

            # Include sanitized QA reports only
            qa_dir = self.run_dir / "qa"
            if qa_dir.exists():
                for qf in qa_dir.rglob("*.json"):
                    arcname = str(qf.relative_to(self.run_dir))
                    zf.write(qf, arcname)

            # Include run summary
            summary_path = self.run_dir / "run_summary.json"
            if summary_path.exists():
                zf.write(summary_path, "run_summary.json")

            # Include config
            config_dir = self.run_dir / "config"
            if config_dir.exists():
                for cf in config_dir.rglob("*"):
                    if cf.is_file():
                        arcname = str(cf.relative_to(self.run_dir))
                        zf.write(cf, arcname)

        return export_path
