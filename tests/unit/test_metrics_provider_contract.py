"""Contract tests for MetricsProvider protocol implementations."""

from __future__ import annotations

import pytest

from fenrix_synthetic.professor.metrics_providers import (
    FixtureMetricsProvider,
    MetricsProvider,
    SDVMetricsProvider,
    create_metrics_provider,
)


class TestMetricsProviderProtocol:
    """Verify MetricsProvider protocol is structural-typed correctly."""

    def test_protocol_accepts_fixture(self) -> None:
        provider: MetricsProvider = FixtureMetricsProvider()
        assert provider.health_check() is True

    def test_protocol_accepts_sdv(self) -> None:
        provider: MetricsProvider = SDVMetricsProvider()
        # SDV health depends on sdv package being installed
        assert isinstance(provider.health_check(), bool)


class TestFixtureMetricsProvider:
    """FixtureMetricsProvider contract tests."""

    @pytest.fixture
    def provider(self) -> FixtureMetricsProvider:
        return FixtureMetricsProvider()

    def test_health_check(self, provider: FixtureMetricsProvider) -> None:
        assert provider.health_check() is True

    def test_provider_properties(self, provider: FixtureMetricsProvider) -> None:
        assert provider.provider_name == "fixture_metrics"
        assert provider.provider_kind == "fixture"

    def test_synthesize_metrics_returns_dict(self, provider: FixtureMetricsProvider) -> None:
        metrics = provider.synthesize_metrics("COMPANY_001")
        assert isinstance(metrics, dict)
        assert "daily_prices" in metrics
        assert "returns" in metrics
        assert "volume" in metrics
        assert "fundamentals" in metrics
        assert "ratios" in metrics

    def test_synthesize_metrics_varying_rows(self, provider: FixtureMetricsProvider) -> None:
        metrics_a = provider.synthesize_metrics("COMPANY_001")
        metrics_b = provider.synthesize_metrics("COMPANY_002")
        # Different company IDs produce different row counts
        rows_a = sum(len(rows) for rows in metrics_a.values())
        rows_b = sum(len(rows) for rows in metrics_b.values())
        assert rows_a != rows_b, "Different companies should produce different row counts"

    def test_synthesize_metrics_consistent(self, provider: FixtureMetricsProvider) -> None:
        metrics_1 = provider.synthesize_metrics("COMPANY_001")
        metrics_2 = provider.synthesize_metrics("COMPANY_001")
        assert metrics_1 == metrics_2, "Same company should produce identical metrics"

    def test_synthesize_selected_types(self, provider: FixtureMetricsProvider) -> None:
        metrics = provider.synthesize_metrics("COMPANY_001", metric_types=["returns"])
        assert list(metrics.keys()) == ["returns"]

    def test_synthesize_issuer_specific_columns(self, provider: FixtureMetricsProvider) -> None:
        prices = provider.synthesize_metrics("COMPANY_001", metric_types=["daily_prices"])
        rows = prices["daily_prices"]
        row = rows[0]
        assert "date" in row
        assert "close" in row
        assert "open" in row
        assert "high" in row
        assert "low" in row

    def test_evaluate_metrics_returns_reports(self, provider: FixtureMetricsProvider) -> None:
        metrics = provider.synthesize_metrics("COMPANY_001")
        evaluation = provider.evaluate_metrics(metrics)
        assert "quality_report" in evaluation
        assert "privacy_report" in evaluation
        assert "schema_report" in evaluation

    def test_quality_report_fields(self, provider: FixtureMetricsProvider) -> None:
        metrics = provider.synthesize_metrics("COMPANY_001")
        evaluation = provider.evaluate_metrics(metrics)
        quality = evaluation["quality_report"]
        assert quality["schema_valid"] is True
        assert quality["quality_score"] >= 0.0

    def test_privacy_report_fields(self, provider: FixtureMetricsProvider) -> None:
        metrics = provider.synthesize_metrics("COMPANY_001")
        evaluation = provider.evaluate_metrics(metrics)
        privacy = evaluation["privacy_report"]
        assert privacy["fixed_template"] is False
        assert privacy["exact_value_leakage"] is False
        assert privacy["privacy_score"] >= 0.0

    def test_evaluate_empty_data(self, provider: FixtureMetricsProvider) -> None:
        evaluation = provider.evaluate_metrics({})
        assert evaluation["quality_report"]["total_series"] == 0
        assert evaluation["privacy_report"]["privacy_score"] >= 0.0


class TestSDVMetricsProvider:
    """SDVMetricsProvider contract tests (SDV not installed by default)."""

    def test_sdv_not_available_by_default(self) -> None:
        """SDV is not installed, so health_check returns False."""
        provider = SDVMetricsProvider()
        assert provider.health_check() is False, (
            "SDV should not be available without explicit install"
        )

    def test_synthesize_raises_without_sdv(self) -> None:
        provider = SDVMetricsProvider()
        with pytest.raises((RuntimeError, NotImplementedError)):
            provider.synthesize_metrics("COMPANY_001")

    def test_evaluate_raises_without_sdv(self) -> None:
        provider = SDVMetricsProvider()
        with pytest.raises((RuntimeError, NotImplementedError)):
            provider.evaluate_metrics({})

    def test_provider_properties(self) -> None:
        provider = SDVMetricsProvider()
        assert provider.provider_name == "sdv_metrics"
        assert provider.provider_kind == "real"


class TestCreateMetricsProvider:
    """Factory function tests."""

    def test_create_fixture(self) -> None:
        provider = create_metrics_provider("fixture")
        assert isinstance(provider, FixtureMetricsProvider)

    def test_create_sdv(self) -> None:
        provider = create_metrics_provider("sdv")
        assert isinstance(provider, SDVMetricsProvider)

    def test_create_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown metrics provider"):
            create_metrics_provider("unknown")
