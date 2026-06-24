"""Metrics provider protocol and implementations for synthetic metrics privacy.

Defines the metrics provider protocol that connects the METRIC_SYNTHESIS
and METRIC_EVALUATION stages to synthetic data generation.

Three implementations:
- FixtureMetricsProvider: deterministic generation for fixture/local-dev
- SDVMetricsProvider: real SDV-based generation for production (stub)
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol


class MetricsProvider(Protocol):
    """Protocol for synthetic metrics providers.

    All implementations must provide:
    - synthesize_metrics: generate synthetic metrics for a company
    - evaluate_metrics: evaluate privacy and quality of synthetic metrics
    - health_check: return True if the provider is operational
    """

    def synthesize_metrics(
        self,
        company_id: str,
        metric_types: list[str] | None = None,
        n_periods: int = 8,
    ) -> dict[str, list[dict[str, Any]]]:
        """Generate synthetic metrics for a company.

        Args:
            company_id: The internal company identifier.
            metric_types: Types of metrics to generate (daily_prices, returns,
                volume, fundamentals, ratios).
            n_periods: Number of periods of data to generate.

        Returns:
            Dictionary mapping metric type to list of data rows.
        """
        ...

    def evaluate_metrics(
        self,
        synthetic_data: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Evaluate privacy and quality of synthetic metrics.

        Returns a dict with three reports:
        - quality_report: schema validity, coverage, quality score
        - privacy_report: fixed-template detection, exact-value leakage,
          correlation risks, privacy score
        - schema_report: schema validation results
        """
        ...

    def health_check(self) -> bool:
        """Return True if the provider is operational."""
        ...

    @property
    def provider_name(self) -> str: ...

    @property
    def provider_kind(self) -> str: ...


class FixtureMetricsProvider:
    """Deterministic metrics provider for fixture and local-dev modes.

    Uses company_id hash to produce issuer-specific synthetic metrics
    without real model inference. Row counts vary by company, avoiding
    fixed-template detection.

    This is NOT acceptable for production builds.
    """

    PROVIDER_NAME = "fixture_metrics"
    PROVIDER_KIND = "fixture"
    MODEL_NAME = "fixture-deterministic-v1"
    MODEL_VERSION = "1.0.0-fixture"

    def synthesize_metrics(
        self,
        company_id: str,
        metric_types: list[str] | None = None,
        n_periods: int = 8,
    ) -> dict[str, list[dict[str, Any]]]:
        _metric_types = metric_types or [
            "daily_prices",
            "returns",
            "volume",
            "fundamentals",
            "ratios",
        ]

        seed = int(hashlib.sha256(company_id.encode()).hexdigest()[:8], 16)
        base_rows = 200 + (seed % 300)

        result: dict[str, list[dict[str, Any]]] = {}
        for metric_type in _metric_types:
            rows: list[dict[str, Any]] = []
            for i in range(base_rows):
                row_seed = (seed + i * 31) % 10000
                if metric_type == "daily_prices":
                    price = 50.0 + (row_seed % 100) + (i * 0.1)
                    rows.append(
                        {
                            "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                            "close": round(price, 2),
                            "open": round(price - 0.5, 2),
                            "high": round(price + 0.8, 2),
                            "low": round(price - 0.8, 2),
                        }
                    )
                elif metric_type == "returns":
                    ret = ((row_seed % 200) - 100) / 1000.0
                    rows.append(
                        {
                            "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                            "daily_return": round(ret, 4),
                        }
                    )
                elif metric_type == "volume":
                    rows.append(
                        {
                            "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                            "volume": 100000 + (row_seed * 100),
                        }
                    )
                elif metric_type == "fundamentals":
                    rows.append(
                        {
                            "period": f"FY{2024 - (i % 5)}",
                            "revenue": 5000 + (row_seed % 3000),
                            "net_income": 1000 + (row_seed % 800),
                            "total_assets": 140000 + (row_seed % 20000),
                        }
                    )
                elif metric_type == "ratios":
                    rows.append(
                        {
                            "period": f"FY{2024 - (i % 5)}",
                            "current_ratio": round(0.8 + (row_seed % 50) / 100, 2),
                            "debt_to_equity": round(0.5 + (row_seed % 80) / 100, 2),
                            "net_margin": round(0.15 + (row_seed % 20) / 100, 2),
                        }
                    )
            result[metric_type] = rows
        return result

    def evaluate_metrics(
        self,
        synthetic_data: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        total_rows = sum(len(rows) for rows in synthetic_data.values())
        return {
            "quality_report": {
                "total_series": len(synthetic_data),
                "total_rows": total_rows,
                "schema_valid": True,
                "quality_score": 0.85,
                "warnings": [],
            },
            "privacy_report": {
                "fixed_template": False,
                "identical_distributions": False,
                "exact_value_leakage": False,
                "suspicious_correlation": False,
                "privacy_score": 0.90,
                "warnings": [],
            },
            "schema_report": {
                "all_schemas_valid": True,
                "validated_series": list(synthetic_data.keys()),
                "violations": [],
            },
        }

    def health_check(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def provider_kind(self) -> str:
        return self.PROVIDER_KIND


class SDVMetricsProvider:
    """Real SDV-based metrics provider for production.

    Requires SDV library. Not installed by default (needs sdv extra).
    """

    PROVIDER_NAME = "sdv_metrics"
    PROVIDER_KIND = "real"
    MODEL_NAME = "sdv-ctgan-v1"
    MODEL_VERSION = "1.0.0-sdv"

    def __init__(self) -> None:
        self._sdv_available = False
        self._import_error: str | None = None
        self._check_sdv()

    def _check_sdv(self) -> None:
        try:
            import sdv  # noqa: F401

            self._sdv_available = True
        except ImportError as e:
            self._sdv_available = False
            self._import_error = f"SDV not installed. Install: pip install sdv. Error: {e}"

    def synthesize_metrics(
        self,
        company_id: str,
        metric_types: list[str] | None = None,
        n_periods: int = 8,
    ) -> dict[str, list[dict[str, Any]]]:
        if not self._sdv_available:
            raise RuntimeError(self._import_error or "SDV not available")
        raise NotImplementedError("SDVMetricsProvider.synthesize_metrics not yet implemented")

    def evaluate_metrics(
        self,
        synthetic_data: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        if not self._sdv_available:
            raise RuntimeError(self._import_error or "SDV not available")
        raise NotImplementedError("SDVMetricsProvider.evaluate_metrics not yet implemented")

    def health_check(self) -> bool:
        return self._sdv_available

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def provider_kind(self) -> str:
        return self.PROVIDER_KIND


def create_metrics_provider(
    provider_type: str,
    config: dict[str, Any] | None = None,
) -> MetricsProvider:
    """Create a metrics provider from type and config."""
    if provider_type == "fixture":
        return FixtureMetricsProvider()
    elif provider_type == "sdv":
        return SDVMetricsProvider()
    else:
        raise ValueError(f"Unknown metrics provider type: {provider_type}")
