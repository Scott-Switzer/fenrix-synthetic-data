"""Tests for pipeline modules."""

from __future__ import annotations

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
        assert mf["company_id"] == "NVDA"
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
