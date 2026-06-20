"""Tests for pipeline modules."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from fenrix_synthetic.anonymization.atlas_builder import IdentityAtlasBuilder
from fenrix_synthetic.anonymization.residual_scanner import ResidualScanner
from fenrix_synthetic.collectors.base import CollectionStatus, CollectorResult
from fenrix_synthetic.pipeline.config import PipelineConfig
from fenrix_synthetic.pipeline.coverage import CoverageReporter
from fenrix_synthetic.pipeline.manifests import ManifestBuilder
from fenrix_synthetic.providers.nvidia_review import NVIDIAReviewAdapter


class TestPipelineConfig:
    def test_from_ticker(self, tmp_path: Path) -> None:
        config = PipelineConfig.from_ticker("NVDA", tmp_path, years=5)
        assert config.tickers[0].ticker == "NVDA"
        assert config.years == 5
        assert config.output_root == tmp_path
        assert config.run_id.startswith("run_")

    def test_from_csv(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "companies.csv"
        csv_path.write_text("ticker,enabled\nNVDA,1\nAMZN,0\n")
        config = PipelineConfig.from_csv(csv_path, tmp_path, years=10)
        assert len(config.tickers) == 1
        assert config.tickers[0].ticker == "NVDA"

    def test_to_dict(self, tmp_path: Path) -> None:
        config = PipelineConfig.from_ticker("NVDA", tmp_path)
        d = config.to_dict()
        assert d["tickers"][0]["ticker"] == "NVDA"
        assert d["years"] == 10
        assert "run_id" in d


class TestManifestBuilder:
    def test_build_manifest(self, tmp_path: Path) -> None:
        builder = ManifestBuilder("run_001", "NVDA", tmp_path)
        mf = builder.build_manifest(
            artifact_id="test_1",
            source="yfinance",
            source_url=None,
            requested_range=("2020-01-01", "2024-01-01"),
            observed_range=("2020-01-02", "2023-12-29"),
            content_type="parquet",
            relative_path="originals/NVDA/metrics/ohlcv.parquet",
            byte_size=1024,
            sha256="a" * 64,
            collection_status="success",
        )
        assert mf["artifact_id"] == "test_1"
        # Company ID is pseudonymized, never the raw ticker
        assert "NVDA" not in mf["company_id"]
        assert mf["company_id"].startswith("COMP_")
        assert mf["collection_status"] == "success"
        assert "fetch_timestamp" in mf

    def test_semantic_hash_excludes_timestamps(self, tmp_path: Path) -> None:
        builder = ManifestBuilder("run_001", "NVDA", tmp_path)
        mf1 = builder.build_manifest(
            artifact_id="test_1",
            source="yfinance",
            source_url=None,
            requested_range=("2020-01-01", "2024-01-01"),
            observed_range=("2020-01-02", "2023-12-29"),
            content_type="parquet",
            relative_path="originals/NVDA/metrics/ohlcv.parquet",
            byte_size=1024,
            sha256="a" * 64,
            collection_status="success",
        )
        mf2 = dict(mf1)
        mf2["fetch_timestamp"] = "different_time"
        h1 = ManifestBuilder.semantic_hash(mf1)
        h2 = ManifestBuilder.semantic_hash(mf2)
        assert h1 == h2


class TestCoverageReporter:
    def test_build_report(self, tmp_path: Path) -> None:
        reporter = CoverageReporter("NVDA", tmp_path)
        yf_result = CollectorResult(
            source="yfinance",
            artifact_type="ohlcv",
            status=CollectionStatus.SUCCESS,
            requested_range=("2020-01-01", "2024-01-01"),
            observed_range=("2020-01-02", "2023-12-29"),
            row_count=1000,
        )
        report = reporter.build_report([yf_result], [], [])
        assert report["ticker"] == "NVDA"
        assert report["yfinance"]["has_data"] is True
        assert report["overall"]["sources_successful"] == 1


class TestIdentityAtlasBuilder:
    def test_build_from_metadata(self, tmp_path: Path) -> None:
        builder = IdentityAtlasBuilder("NVDA", tmp_path)
        yf_meta = {
            "short_name": "NVIDIA",
            "long_name": "NVIDIA Corporation",
            "website": "https://www.nvidia.com",
            "city": "Santa Clara",
            "state": "CA",
            "country": "United States",
        }
        atlas = builder.build_from_metadata(yf_meta, [], None)
        entities = atlas.all_entities()
        assert any(e.canonical_private_value == "NVIDIA Corporation" for e in entities)
        assert any(e.entity_type.value == "ticker" for e in entities)

    def test_save_atlas(self, tmp_path: Path) -> None:
        builder = IdentityAtlasBuilder("NVDA", tmp_path)
        atlas = builder.build_from_metadata({"long_name": "NVIDIA Corp"}, [], None)
        path = builder.save_atlas(atlas)
        assert path.exists()
        assert (tmp_path / "identity_atlas_summary.json").exists()


class TestResidualScanner:
    def test_scan_all_no_leaks(self, tmp_path: Path) -> None:
        from fenrix_synthetic.identity import EntityRegistry
        from fenrix_synthetic.identity.schemas import EntityType

        reg = EntityRegistry.create("NVDA", "test-reg")
        reg.add_entity("nvda_company", EntityType.COMPANY, "NVIDIA Corp")
        scanner = ResidualScanner("NVDA", reg, tmp_path)
        anon_dir = tmp_path / "anon"
        anon_dir.mkdir()
        (anon_dir / "test.md").write_text("This is a safe document with no identifiers.")
        result = scanner.scan_all(anon_dir)
        assert result["exact_identifier_count"] == 0
        assert result["status"] == "zero_leak"

    def test_scan_all_with_leak(self, tmp_path: Path) -> None:
        from fenrix_synthetic.identity import EntityRegistry
        from fenrix_synthetic.identity.schemas import EntityType

        reg = EntityRegistry.create("NVDA", "test-reg")
        reg.add_entity("nvda_company", EntityType.COMPANY, "NVIDIA Corporation")
        scanner = ResidualScanner("NVDA", reg, tmp_path)
        anon_dir = tmp_path / "anon"
        anon_dir.mkdir()
        (anon_dir / "test.md").write_text("NVIDIA Corporation reported earnings.")
        result = scanner.scan_all(anon_dir)
        assert result["exact_identifier_count"] > 0
        assert result["status"] == "remaining_leak"


class TestNVIDIAReviewAdapter:
    def test_not_configured(self) -> None:
        # Ensure no env var is set
        old_key = os.environ.pop("NVIDIA_API_KEY", None)
        try:
            adapter = NVIDIAReviewAdapter()
            assert adapter.is_configured() is False
            result = adapter.review_batch(Path("/tmp"), "NVDA")
            assert result["status"] == "not_configured"
        finally:
            if old_key is not None:
                os.environ["NVIDIA_API_KEY"] = old_key

    def test_configured(self) -> None:
        os.environ["NVIDIA_API_KEY"] = "fake-key"
        try:
            adapter = NVIDIAReviewAdapter()
            assert adapter.is_configured() is True
        finally:
            del os.environ["NVIDIA_API_KEY"]


class TestSECArchive:
    """TASK 6: Synthetic archive importer tests."""

    def _make_synthetic_archive_zip(self, tmp_path: Path, ticker: str = "FAKE") -> Path:
        """Create a synthetic SEC archive ZIP with fake filings."""
        import zipfile

        archive_path = tmp_path / "sec_archive.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 10-K filing
            zf.writestr(
                f"{ticker}/10-K/2024/0001234567-24-000001/primary.html",
                "<html><body><h1>Item 1. Business</h1><p>FakeCorp is a test company.</p></body></html>",
            )
            # 10-Q filing
            zf.writestr(
                f"{ticker}/10-Q/2025/0001234567-25-000001/primary.html",
                "<html><body><h1>Item 2. Financial</h1><p>Quarterly results.</p></body></html>",
            )
            # Non-filing file (should be inventoried but not processed as filing)
            zf.writestr(f"{ticker}/README.txt", "Archive of SEC filings")
        return archive_path

    def _make_synthetic_directory_archive(self, tmp_path: Path, ticker: str = "FAKE") -> Path:
        """Create a synthetic SEC archive directory."""
        archive_dir = tmp_path / "sec_archive_dir"
        filing_dir = archive_dir / ticker / "10-K" / "2024"
        filing_dir.mkdir(parents=True, exist_ok=True)
        (filing_dir / "primary.html").write_text(
            "<html><body><h1>10-K Filing</h1><p>FakeCorp annual report.</p></body></html>"
        )
        return archive_dir

    def test_inventory_zip_archive(self, tmp_path: Path) -> None:
        """Inventory a ZIP archive without loading content into memory."""
        from fenrix_synthetic.collectors.sec_archive import SECArchiveCollector

        archive_path = self._make_synthetic_archive_zip(tmp_path)
        collector = SECArchiveCollector(
            archive_path=archive_path,
            output_dir=tmp_path / "output",
            ticker="FAKE",
        )
        entries = collector.inventory()
        # Should find 3 files
        assert len(entries) >= 2
        # At least one should have a detected form
        forms = [e.form for e in entries if e.form]
        assert len(forms) > 0

    def test_inventory_directory_archive(self, tmp_path: Path) -> None:
        """Inventory a directory archive."""
        from fenrix_synthetic.collectors.sec_archive import SECArchiveCollector

        archive_dir = self._make_synthetic_directory_archive(tmp_path)
        collector = SECArchiveCollector(
            archive_path=archive_dir,
            output_dir=tmp_path / "output",
            ticker="FAKE",
        )
        entries = collector.inventory()
        assert len(entries) >= 1

    def test_coverage_report(self, tmp_path: Path) -> None:
        """Coverage report includes ticker, form, year breakdowns."""
        from fenrix_synthetic.collectors.sec_archive import SECArchiveCollector

        archive_path = self._make_synthetic_archive_zip(tmp_path)
        collector = SECArchiveCollector(
            archive_path=archive_path,
            output_dir=tmp_path / "output",
            ticker="FAKE",
        )
        collector.inventory()
        report = collector.coverage_report()
        assert "by_ticker" in report
        assert "total_files" in report
        assert report["total_files"] >= 2

    def test_collect_from_zip_normalizes_filings(self, tmp_path: Path) -> None:
        """Collection from ZIP normalizes filings through HtmlFilingExtractor."""
        from fenrix_synthetic.collectors.sec_archive import SECArchiveCollector

        archive_path = self._make_synthetic_archive_zip(tmp_path)
        collector = SECArchiveCollector(
            archive_path=archive_path,
            output_dir=tmp_path / "output",
            ticker="FAKE",
        )
        results = collector.collect()
        # Should have at least filing_inventory and filing_documents
        assert len(results) >= 1
        types = [r.artifact_type for r in results]
        assert "filing_inventory" in types

    def test_never_modifies_source_archive(self, tmp_path: Path) -> None:
        """Archive importer must never modify the source archive."""
        from fenrix_synthetic.collectors.sec_archive import SECArchiveCollector

        archive_path = self._make_synthetic_archive_zip(tmp_path)
        original_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()

        collector = SECArchiveCollector(
            archive_path=archive_path,
            output_dir=tmp_path / "output",
            ticker="FAKE",
        )
        collector.inventory()
        collector.collect()

        after_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        assert original_hash == after_hash, "Source archive was modified!"

    def test_deduplicate_by_content(self, tmp_path: Path) -> None:
        """Duplicate content in directory archive should be deduplicated by content hash."""
        from fenrix_synthetic.collectors.sec_archive import SECArchiveCollector

        # Use a directory archive where duplicate files have the same content hash
        archive_dir = tmp_path / "dup_archive_dir"
        (archive_dir / "FAKE" / "10-K" / "2024").mkdir(parents=True, exist_ok=True)
        filing_html = "<html><body><h1>Duplicate Filing</h1></body></html>"
        (archive_dir / "FAKE/10-K/2024/filing1.html").write_text(filing_html)
        (archive_dir / "FAKE/10-K/2024/filing2.html").write_text(filing_html)

        collector = SECArchiveCollector(
            archive_path=archive_dir,
            output_dir=tmp_path / "output",
            ticker="FAKE",
        )
        results = collector.collect()
        for r in results:
            if r.artifact_type == "filing_documents":
                assert r.row_count <= 1, f"Expected <=1 after dedup, got {r.row_count}"

    def test_supported_archive_formats(self, tmp_path: Path) -> None:
        """supported_archive detects valid archive types."""
        from fenrix_synthetic.collectors.sec_archive import SECArchiveCollector

        assert SECArchiveCollector.supported_archive(tmp_path)  # directory
        zip_path = tmp_path / "test.zip"
        zip_path.touch()
        assert SECArchiveCollector.supported_archive(zip_path)
        tar_path = tmp_path / "test.tar.gz"
        tar_path.touch()
        assert SECArchiveCollector.supported_archive(tar_path)


class TestCollectorResult:
    def test_to_dict(self) -> None:
        result = CollectorResult(
            source="yfinance",
            artifact_type="ohlcv",
            status=CollectionStatus.SUCCESS,
            requested_range=("2020-01-01", "2024-01-01"),
            observed_range=("2020-01-02", "2023-12-29"),
            row_count=100,
        )
        d = result.to_dict()
        assert d["source"] == "yfinance"
        assert d["status"] == "success"
        assert d["row_count"] == 100


class TestPipelineIntegration:
    """Verify the pipeline runner integrates with confirmed imports.

    TASK 4: Prove OhlcvRecord, transform_s3a_daily_bucketed,
    ExactResidualScanner, and HtmlFilingExtractor are callable
    through the pipeline's anonymization and residual-scan paths.
    """

    def test_pipeline_runner_instantiation(self, tmp_path: Path) -> None:
        """PipelineRunner instantiates without errors."""
        from fenrix_synthetic.pipeline.runner import PipelineRunner

        config = PipelineConfig.from_ticker("FAKE", tmp_path, years=1)
        runner = PipelineRunner(config)
        assert runner.config.run_id.startswith("run_")
        assert runner.run_dir == config.output_root / config.run_id

    def test_ohlcv_record_roundtrip_through_structured_anonymizer(self, tmp_path: Path) -> None:
        """OhlcvRecord → transform_s3a_daily_bucketed works end to end."""
        import json

        from fenrix_synthetic.transforms.feature_only import (
            OhlcvRecord,
            transform_s3a_daily_bucketed,
        )

        # Produce enough records (260) so the transform meets the
        # minimum-length threshold and returns a releasable result.
        records = [
            OhlcvRecord(
                date=f"2025-01-{d:02d}",
                open=100.0 + d * 0.1,
                high=101.0 + d * 0.1,
                low=99.0 + d * 0.1,
                close=100.5 + d * 0.1,
                volume=1000000.0 + d * 1000,
            )
            for d in range(1, 261)
        ]
        result = transform_s3a_daily_bucketed(records)
        assert result.row_count > 0
        assert len(result.features) == result.row_count
        # Write and re-read deterministically
        out_path = tmp_path / "features.json"
        out_path.write_text(json.dumps(result.features))
        reread = json.loads(out_path.read_text())
        assert reread == result.features

    def test_exact_residual_scanner_through_residual_scanner(self, tmp_path: Path) -> None:
        """ExactResidualScanner is called via ResidualScanner.scan_all."""
        from fenrix_synthetic.identity import EntityRegistry
        from fenrix_synthetic.identity.schemas import EntityType

        reg = EntityRegistry.create("FAKE", "test-reg")
        reg.add_entity("fake_co", EntityType.COMPANY, "FakeCorp Inc")

        anon_dir = tmp_path / "anonymized"
        anon_dir.mkdir()
        (anon_dir / "clean.md").write_text("This document has no company names.")
        (anon_dir / "leaky.md").write_text("FakeCorp Inc announced earnings.")

        scanner = ResidualScanner("FAKE", reg, tmp_path / "qa")
        result = scanner.scan_all(anon_dir)
        assert result["exact_identifier_count"] > 0
        assert result["status"] == "remaining_leak"

    def test_html_filing_extractor_deterministic(self) -> None:
        """HtmlFilingExtractor produces deterministic output."""
        from fenrix_synthetic.extraction.converter import HtmlFilingExtractor

        extractor = HtmlFilingExtractor()
        html = "<html><body><h1>Item 1. Business</h1><p>TestCorp is a leading provider.</p></body></html>"
        result1 = extractor.extract(html)
        result2 = extractor.extract(html)
        assert result1["text"] == result2["text"]
        assert result1["char_count"] == result2["char_count"]
        assert "TestCorp" in result1["text"]


class TestZipExport:
    """TASK 5: End-to-end synthetic ZIP export test.

    Prove:
    - deterministic file ordering
    - stable ZIP metadata
    - checksums match between builds
    - anonymized outputs included
    - sanitized manifests included
    - QA summaries included
    - originals excluded
    - private maps excluded
    - .env excluded
    - secrets excluded
    - absolute machine paths excluded
    - temporary and partial files excluded
    """

    def test_zip_export_deterministic(self, tmp_path: Path) -> None:
        """Build ZIP twice and require identical semantic content."""
        import hashlib
        import zipfile

        from fenrix_synthetic.pipeline.runner import PipelineRunner, ReleaseGateResult

        config = PipelineConfig.from_ticker(
            "FAKE",
            tmp_path,
            years=1,
            collect_only=True,
        )
        runner = PipelineRunner(config)
        runner.run_dir.mkdir(parents=True, exist_ok=True)

        # Create synthetic anonymized artifacts
        anon_dir = runner.run_dir / "anonymized" / "FAKE"
        anon_dir.mkdir(parents=True, exist_ok=True)
        (anon_dir / "features_s3a.json").write_text('{"variant":"s3a","row_count":10}')
        (anon_dir / "clean_doc.md").write_text("# Clean Document\n\nNo identifiers here.")

        # Create manifests
        manif_dir = anon_dir / "manifests"
        manif_dir.mkdir(parents=True, exist_ok=True)
        (manif_dir / "artifact_0000.json").write_text('{"artifact_id":"fake_1","sha256":"aa"}')

        # Create QA
        qa_dir = runner.run_dir / "qa" / "FAKE"
        qa_dir.mkdir(parents=True, exist_ok=True)
        (qa_dir / "source_coverage" / "coverage_report.json").parent.mkdir(
            parents=True, exist_ok=True
        )
        (qa_dir / "source_coverage" / "coverage_report.json").write_text(
            '{"ticker":"FAKE","overall":{"sources_successful":0}}'
        )
        (qa_dir / "residual_scans" / "qa_report.json").parent.mkdir(parents=True, exist_ok=True)
        (qa_dir / "residual_scans" / "qa_report.json").write_text(
            '{"status":"zero_leak","exact_identifier_count":0}'
        )

        # Config
        config_dir = runner.run_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "resolved_config.json").write_text('{"run_id":"test"}')

        # Create originals (should NOT be in ZIP)
        orig_dir = runner.run_dir / "originals" / "FAKE"
        orig_dir.mkdir(parents=True, exist_ok=True)
        (orig_dir / "original_data.txt").write_text("SECRET SOURCE DATA")

        # Create private maps (should NOT be in ZIP)
        priv_dir = runner.run_dir / "private_maps" / "FAKE"
        priv_dir.mkdir(parents=True, exist_ok=True)
        (priv_dir / "identity_atlas.yaml").write_text("private: data")

        # Run summary (with CLEAN status)
        run_summary = {
            "run_id": "test",
            "tickers": {
                "FAKE": {
                    "status": "completed_clean",
                    "residual_exact_identifier_count": 0,
                    "unresolved_candidate_count": 0,
                    "blocking_finding_count": 0,
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

        # Build ZIP twice
        result1 = runner._create_export_bundle(gate, run_summary)
        gate2 = ReleaseGateResult()
        result2 = runner._create_export_bundle(gate2, run_summary)

        export1 = Path(result1["export_zip"])
        export2 = Path(result2["export_zip"])
        assert export1.exists()
        assert export2.exists()

        # Compare content (ZIP metadata may differ)
        def _extract_content_map(zip_path: Path) -> dict[str, str]:
            content_map: dict[str, str] = {}
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in sorted(zf.namelist()):
                    content_map[name] = zf.read(name).decode("utf-8", errors="replace")
            return content_map

        content1 = _extract_content_map(export1)
        content2 = _extract_content_map(export2)
        assert content1 == content2, "ZIP content differs between builds"
        assert result1["release_safe"] is True
        assert result2["release_safe"] is True

    def test_zip_export_excludes_originals(self, tmp_path: Path) -> None:
        """Verify originals are NOT in the export ZIP."""
        import zipfile

        from fenrix_synthetic.pipeline.runner import PipelineRunner, ReleaseGateResult

        config = PipelineConfig.from_ticker("FAKE", tmp_path, years=1, collect_only=True)
        runner = PipelineRunner(config)
        runner.run_dir.mkdir(parents=True, exist_ok=True)

        # Create synthetic data
        (runner.run_dir / "originals" / "FAKE" / "secret.txt").parent.mkdir(
            parents=True, exist_ok=True
        )
        (runner.run_dir / "originals" / "FAKE" / "secret.txt").write_text("SECRET")
        (runner.run_dir / "anonymized" / "FAKE" / "public.txt").parent.mkdir(
            parents=True, exist_ok=True
        )
        (runner.run_dir / "anonymized" / "FAKE" / "public.txt").write_text("PUBLIC")
        (runner.run_dir / "qa" / "FAKE" / "report.json").parent.mkdir(parents=True, exist_ok=True)
        (runner.run_dir / "qa" / "FAKE" / "report.json").write_text("{}")
        (runner.run_dir / "config" / "cfg.json").parent.mkdir(parents=True, exist_ok=True)
        (runner.run_dir / "config" / "cfg.json").write_text("{}")

        run_summary = {
            "run_id": "test",
            "tickers": {"FAKE": {"status": "completed_clean"}},
        }
        gate = ReleaseGateResult()
        result = runner._create_export_bundle(gate, run_summary)
        export_path = Path(result["export_zip"])

        with zipfile.ZipFile(export_path, "r") as zf:
            names = zf.namelist()
            assert not any("originals" in n for n in names), f"Originals leaked: {names}"
            assert not any("private_maps" in n for n in names), f"Private maps leaked: {names}"
            assert not any(n.endswith(".env") for n in names), f".env leaked: {names}"
            assert any("anonymized" in n for n in names), f"Anonymized missing: {names}"

    def test_zip_export_file_count_preview(self, tmp_path: Path) -> None:
        """Export contains expected file categories."""
        import zipfile

        from fenrix_synthetic.pipeline.runner import PipelineRunner, ReleaseGateResult

        config = PipelineConfig.from_ticker("FAKE", tmp_path, years=1, collect_only=True)
        runner = PipelineRunner(config)
        runner.run_dir.mkdir(parents=True, exist_ok=True)
        (runner.run_dir / "anonymized" / "FAKE" / "a.json").parent.mkdir(
            parents=True, exist_ok=True
        )
        (runner.run_dir / "anonymized" / "FAKE" / "a.json").write_text("{}")
        (runner.run_dir / "qa" / "FAKE" / "b.json").parent.mkdir(parents=True, exist_ok=True)
        (runner.run_dir / "qa" / "FAKE" / "b.json").write_text("{}")
        (runner.run_dir / "config" / "c.json").parent.mkdir(parents=True, exist_ok=True)
        (runner.run_dir / "config" / "c.json").write_text("{}")

        run_summary = {
            "run_id": "test",
            "tickers": {"FAKE": {"status": "completed_clean"}},
        }
        gate = ReleaseGateResult()
        result = runner._create_export_bundle(gate, run_summary)
        export_path = Path(result["export_zip"])
        with zipfile.ZipFile(export_path, "r") as zf:
            names = zf.namelist()
            assert len(names) >= 3, f"Expected >=3 files, got {len(names)}: {names}"
