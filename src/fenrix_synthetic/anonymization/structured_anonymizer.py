"""Structured data anonymizer.

Exports both anonymized numeric datasets (complete Parquet tables)
and S3A bucketized features (supplemental only).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..storage.hashing import hash_file
from ..transforms.feature_only import OhlcvRecord, transform_s3a_daily_bucketed

logger = logging.getLogger(__name__)

# Required numeric dataset paths (relative to anonymized/ticker/)
_REQUIRED_NUMERIC_DATASETS: list[str] = [
    "market/ohlcv.parquet",
    "market/dividends.parquet",
    "market/splits.parquet",
    "market/actions.parquet",
    "statements/income_statement_annual.parquet",
    "statements/income_statement_quarterly.parquet",
    "statements/balance_sheet_annual.parquet",
    "statements/balance_sheet_quarterly.parquet",
    "statements/cash_flow_annual.parquet",
    "statements/cash_flow_quarterly.parquet",
    "estimates/earnings_dates.parquet",
    "estimates/recommendations.parquet",
    "estimates/price_targets.parquet",
    "estimates/earnings_estimates.parquet",
    "estimates/revenue_estimates.parquet",
    "estimates/growth_estimates.parquet",
    "sec/companyfacts.parquet",
    "sec/filing_inventory.parquet",
    "sec/filing_tables.parquet",
]


class StructuredAnonymizer:
    """Anonymize structured financial data.

    Exports complete anonymized Parquet datasets as canonical output,
    plus S3A bucketized features in supplemental_features/.
    """

    def __init__(
        self,
        ticker: str,
        originals_dir: Path,
        anonymized_dir: Path,
        private_maps_dir: Path,
    ) -> None:
        self.ticker = ticker.upper()
        self.originals_dir = originals_dir
        self.anonymized_dir = anonymized_dir
        self.private_maps_dir = private_maps_dir

    def anonymize_all(self) -> list[dict[str, Any]]:
        """Anonymize all structured data artifacts."""
        manifests: list[dict[str, Any]] = []

        # 1. Export complete anonymized numeric datasets
        manifests.extend(self._export_numeric_datasets())

        # 2. OHLCV -> S3A bucketized features (supplemental only)
        manifests.extend(self._export_s3a_features())

        # 3. Sanitize metadata
        manifests.extend(self._sanitize_metadata())

        return manifests

    def _export_numeric_datasets(self) -> list[dict[str, Any]]:
        """Export complete anonymized Parquet datasets.

        Copies original Parquet files and anonymizes identifying columns.
        Preserves full row counts and numeric columns.
        """
        manifests: list[dict[str, Any]] = []

        # Collect available original parquet files
        orig_parquets = list(self.originals_dir.rglob("*.parquet"))
        if not orig_parquets:
            return manifests

        import pandas as pd

        # Build identity set for column anonymization
        id_columns = self._build_id_columns_set()

        for orig_path in orig_parquets:
            try:
                df = pd.read_parquet(orig_path)
                rel = orig_path.relative_to(self.originals_dir)

                # Anonymize identifying columns
                df = self._anonymize_dataframe_columns(df, id_columns)

                # Write anonymized parquet
                out_path = self.anonymized_dir / rel
                out_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(out_path, index=False)

                manifests.append(
                    {
                        "artifact_id": f"{self.ticker}_anon_{rel.stem}",
                        "source": "numeric_dataset_export",
                        "original_path": str(rel),
                        "anonymized_path": str(out_path.relative_to(self.anonymized_dir.parent)),
                        "sha256": hash_file(out_path),
                        "row_count": len(df),
                        "column_count": len(df.columns),
                        "content_type": "parquet",
                    }
                )

                logger.debug(
                    "Exported anonymized dataset: %s (%d rows, %d cols)",
                    rel,
                    len(df),
                    len(df.columns),
                )
            except Exception as exc:
                logger.warning("Dataset export failed for %s: %s", orig_path, exc)

        return manifests

    def _export_s3a_features(self) -> list[dict[str, Any]]:
        """Export S3A bucketized features (supplemental only)."""
        manifests: list[dict[str, Any]] = []

        ohlcv_path = self.originals_dir / "metrics" / "ohlcv.parquet"
        if not ohlcv_path.exists():
            return manifests

        try:
            import pandas as pd

            df = pd.read_parquet(ohlcv_path)
            records = self._dataframe_to_records(df)
            if records:
                result = transform_s3a_daily_bucketed(records)
                out_path = self.anonymized_dir / "supplemental_features" / "features_s3a.json"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                import orjson

                out_path.write_bytes(
                    orjson.dumps(
                        {
                            "variant": result.variant.value,
                            "parameter_hash": result.parameter_hash,
                            "row_count": result.row_count,
                            "features": result.features,
                            "warnings": result.warnings,
                        },
                        option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
                    )
                )

                manifests.append(
                    {
                        "artifact_id": f"{self.ticker}_anon_features_s3a",
                        "source": "feature_only_transform",
                        "original_path": str(ohlcv_path.relative_to(self.originals_dir.parent)),
                        "anonymized_path": str(out_path.relative_to(self.anonymized_dir.parent)),
                        "sha256": hash_file(out_path),
                        "variant": result.variant.value,
                        "row_count": result.row_count,
                        "supplemental": True,
                    }
                )
        except Exception as exc:
            logger.warning("S3A features export failed: %s", exc)

        return manifests

    def _sanitize_metadata(self) -> list[dict[str, Any]]:
        """Sanitize metadata (remove identifying fields)."""
        manifests: list[dict[str, Any]] = []

        meta_path = self.originals_dir / "metadata.json"
        if not meta_path.exists():
            return manifests

        try:
            import orjson

            meta = orjson.loads(meta_path.read_bytes())
            safe_meta = {
                "ticker": "ANON",
                "collection_timestamp": meta.get("collection_timestamp"),
                "years_requested": meta.get("years_requested"),
                "collection_summary": [
                    {
                        "artifact_type": s.get("artifact_type"),
                        "status": s.get("status"),
                        "row_count": s.get("row_count"),
                    }
                    for s in meta.get("collection_summary", [])
                ],
            }
            out_meta = self.anonymized_dir / "metadata.json"
            out_meta.parent.mkdir(parents=True, exist_ok=True)
            out_meta.write_bytes(
                orjson.dumps(safe_meta, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
            )
            manifests.append(
                {
                    "artifact_id": f"{self.ticker}_anon_metadata",
                    "source": "sanitized_metadata",
                    "anonymized_path": str(out_meta.relative_to(self.anonymized_dir.parent)),
                    "sha256": hash_file(out_meta),
                }
            )
        except Exception as exc:
            logger.warning("Metadata sanitization failed: %s", exc)

        return manifests

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _dataframe_to_records(df: Any) -> list[OhlcvRecord]:
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            return []
        records: list[OhlcvRecord] = []
        for idx, row in df.iterrows():
            try:
                records.append(
                    OhlcvRecord(
                        date=str(idx) if hasattr(idx, "strftime") else str(idx),
                        open=float(row.get("Open", 0)),
                        high=float(row.get("High", 0)),
                        low=float(row.get("Low", 0)),
                        close=float(row.get("Close", 0)),
                        volume=float(row.get("Volume", 0)),
                        adj_close=float(row.get("Adj Close", row.get("Close", 0))),
                    )
                )
            except Exception:
                continue
        return records

    def _build_id_columns_set(self) -> set[str]:
        """Build set of identifying column names to anonymize."""
        return {
            "ticker",
            "symbol",
            "Symbol",
            "company_name",
            "CompanyName",
            "short_name",
            "shortName",
            "long_name",
            "longName",
            "issuer",
            "Issuer",
            "cik",
            "CIK",
            "accession",
            "Accession",
            "accessionNumber",
            "source",
            "Source",
            "source_name",
            "sourceName",
            "url",
            "URL",
            "source_url",
            "sourceUrl",
        }

    def _anonymize_dataframe_columns(self, df: Any, id_columns: set[str]) -> Any:
        """Replace identifying column values with pseudonyms."""
        import hashlib

        import pandas as pd

        for col in df.columns:
            col_str = str(col)
            if col_str.lower() in {c.lower() for c in id_columns}:
                try:
                    if df[col].dtype == object:
                        df[col] = df[col].apply(
                            lambda x: (
                                f"PSEUDO_{hashlib.sha256(str(x).encode()).hexdigest()[:8]}"
                                if pd.notna(x) and str(x) != ""
                                else x
                            )
                        )
                except Exception:
                    pass
        return df
