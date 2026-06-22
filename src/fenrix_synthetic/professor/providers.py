"""Mock providers for deterministic offline professor-bundle builds.

These providers produce deterministic output for fixture-based testing.
They are NOT real model calls — they simulate the provider interface
so the pipeline can run end-to-end in CI without network or model weights.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .evidence import DetectedEntity, build_provenance_key, compute_opaque_id


class MockGLiNERProvider:
    """Mock GLiNER provider that produces deterministic entity spans.

    Simulates the GLiNERLocalProvider interface without requiring
    model weights or network access.
    """

    PROVIDER_NAME = "mock_gliner"
    MODEL_NAME = "mock-gliner-finance-v1"
    MODEL_VERSION = "1.0.0-mock"

    def discover_entities(
        self,
        text: str,
        company_id: str,
        source_artifact_id: str,
        labels: list[str] | None = None,
    ) -> list[DetectedEntity]:
        """Discover entities in text using deterministic mock logic.

        Detects: company names, tickers, executives, products, domains.
        """
        entities: list[DetectedEntity] = []
        _labels = labels or ["company", "executive", "product", "ticker"]

        # Simple deterministic detection based on known fixture patterns
        patterns: list[tuple[str, str, str]] = [
            (r"Canary Holdings Corporation", "company", "company"),
            (r"Canary Holdings", "company", "company"),
            (r"CHC", "ticker", "ticker"),
            (r"Eleanor Testperson", "executive", "executive"),
            (r"canary-test\.invalid", "domain", "domain"),
        ]

        import re

        for pattern_text, entity_type, label in patterns:
            if label not in _labels and "all" not in _labels:
                continue
            for match in re.finditer(pattern_text, text):
                entity_id = (
                    f"ent-{compute_opaque_id(source_artifact_id, str(match.start()), entity_type)}"
                )
                entity = DetectedEntity(
                    entity_id=entity_id,
                    company_id=company_id,
                    entity_type=entity_type,
                    detected_text=match.group(),
                    detection_method="gliner",
                    confidence=0.92,
                    start_offset=match.start(),
                    end_offset=match.end(),
                    source_artifact_id=source_artifact_id,
                    provenance_key=build_provenance_key(
                        company_id, "ENTITY", "GLINER", "", entity_id[:8]
                    ),
                )
                entities.append(entity)

        return entities

    def health_check(self) -> bool:
        return True


class MockNVIDIAReviewer:
    """Mock NVIDIA reviewer for deterministic adversarial QA.

    Produces a PASS verdict with non-zero confidence and cited evidence.
    This simulates the NVIDIA review adapter without requiring API keys.

    In strict mode, this provider is NOT acceptable — real NVIDIA keys
    are required. The stage will be marked PROVIDER_NOT_RUN.
    """

    PROVIDER_NAME = "mock_nvidia"
    MODEL_NAME = "mock-llama-3.1-70b"
    MODEL_VERSION = "1.0.0-mock"

    def review_artifact(
        self,
        text: str,
        artifact_id: str,
        company_id: str,
    ) -> dict[str, Any]:
        """Review an artifact for identity leakage.

        Returns a deterministic PASS verdict with evidence.
        """
        return {
            "artifact_id": artifact_id,
            "company_id": company_id,
            "verdict": "PASS",
            "confidence": 0.15,  # Low confidence = low identifiability risk
            "evidence_cited": [
                build_provenance_key(company_id, "QA", "ENTITY_AUDIT"),
            ],
            "samples_reviewed": 1,
            "model": self.MODEL_NAME,
            "provider": self.PROVIDER_NAME,
            "review_timestamp": "2026-06-22T00:00:00Z",
        }


class MockMetricsSynthesizer:
    """Mock metrics synthesizer for deterministic offline metric generation.

    Produces issuer-specific synthetic metrics without requiring SDV/CTGAN.
    """

    PROVIDER_NAME = "mock_sdv"
    MODEL_NAME = "mock-ctgan-v1"
    MODEL_VERSION = "1.0.0-mock"

    def synthesize_metrics(
        self,
        company_id: str,
        metric_types: list[str] | None = None,
        n_periods: int = 8,
    ) -> dict[str, list[dict[str, Any]]]:
        """Produce deterministic synthetic metrics for a company.

        Generates: daily_prices, returns, volume, fundamentals, ratios.
        Row count is issuer-specific (derived from company_id hash), not
        a fixed template.
        """
        _metric_types = metric_types or [
            "daily_prices",
            "returns",
            "volume",
            "fundamentals",
            "ratios",
        ]

        seed = int(hashlib.sha256(company_id.encode()).hexdigest()[:8], 16)
        # Issuer-specific row count — NOT a fixed template
        base_rows = 200 + (seed % 300)  # 200-500 rows, varies by company

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
                    ret = ((row_seed % 200) - 100) / 1000.0  # -10% to +10%
                    rows.append(
                        {
                            "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                            "daily_return": round(ret, 4),
                        }
                    )
                elif metric_type == "volume":
                    vol = 100000 + (row_seed * 100)
                    rows.append(
                        {
                            "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                            "volume": vol,
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

    def evaluate_metrics(self, synthetic_data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        """Produce a deterministic metrics evaluation report."""
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
                "fixed_template": False,  # Row count varies by company
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
