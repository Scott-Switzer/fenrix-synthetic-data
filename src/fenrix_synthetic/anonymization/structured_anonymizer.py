"""Structured data anonymizer using feature-only transforms."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..storage.hashing import hash_file
from ..transforms.feature_only import OhlcvRecord, transform_s3a_daily_bucketed

logger = logging.getLogger(__name__)


class StructuredAnonymizer:
    """Anonymize structured financial data using feature-only transforms."""

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

        # OHLCV -> feature-only
        ohlcv_path = self.originals_dir / "metrics" / "ohlcv.parquet"
        if ohlcv_path.exists():
            try:
                import pandas as pd

                df = pd.read_parquet(ohlcv_path)
                records = self._dataframe_to_records(df)
                if records:
                    result = transform_s3a_daily_bucketed(records)
                    out_path = self.anonymized_dir / "metrics" / "features_s3a.json"
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
                            "anonymized_path": str(
                                out_path.relative_to(self.anonymized_dir.parent)
                            ),
                            "sha256": hash_file(out_path),
                            "variant": result.variant.value,
                            "row_count": result.row_count,
                        }
                    )
            except Exception as exc:
                logger.warning("Structured anonymization failed for OHLCV: %s", exc)

        # Copy metadata (sanitized)
        meta_path = self.originals_dir / "metadata.json"
        if meta_path.exists():
            try:
                import orjson

                meta = orjson.loads(meta_path.read_bytes())
                # Sanitize: remove identifying fields
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
