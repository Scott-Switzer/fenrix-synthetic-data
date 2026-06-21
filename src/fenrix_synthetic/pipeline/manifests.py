"""Manifest builder for pipeline artifacts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson


class ManifestBuilder:
    """Build canonical manifests for original and anonymized artifacts."""

    def __init__(
        self,
        run_id: str,
        ticker: str,
        output_root: Path,
        company_pseudonym: str = "",
    ) -> None:
        self.run_id = run_id
        self.ticker = ticker.upper()
        self.output_root = output_root
        self.schema_version = "1.0.0"
        self.parser_version = "fenrix_pipeline_v1"
        self._company_pseudonym = (
            company_pseudonym or f"COMP_{hashlib.sha256(ticker.upper().encode()).hexdigest()[:12]}"
        )

    def build_manifest(
        self,
        artifact_id: str,
        source: str,
        source_url: str | None,
        requested_range: tuple[str | None, str | None],
        observed_range: tuple[str | None, str | None],
        content_type: str,
        relative_path: str,
        byte_size: int,
        sha256: str,
        collection_status: str,
        anonymization_status: str = "not_anonymized",
        parent_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a canonical artifact manifest."""
        # Sanitize paths: replace ticker with company pseudonym
        sanitized_path = self._sanitize_path(relative_path)
        manifest: dict[str, Any] = {
            "artifact_id": self._sanitize_artifact_id(artifact_id),
            "company_id": self._company_pseudonym,
            "source": source,
            "source_url": source_url,
            "requested_date_range": requested_range,
            "observed_date_range": observed_range,
            "fetch_timestamp": datetime.now(UTC).isoformat(),
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "content_type": content_type,
            "relative_output_path": sanitized_path,
            "byte_size": byte_size,
            "sha256": sha256,
            "collection_status": collection_status,
            "anonymization_status": anonymization_status,
            "run_id": self.run_id,
        }
        if parent_hash:
            manifest["parent_artifact_hash"] = parent_hash
        if metadata:
            manifest["metadata"] = metadata
        return manifest

    def save_manifest(self, manifest: dict[str, Any], manifest_dir: Path, name: str) -> Path:
        """Save manifest atomically with sorted keys."""
        manifest_dir.mkdir(parents=True, exist_ok=True)
        path = manifest_dir / f"{name}.json"
        path.write_bytes(orjson.dumps(manifest, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2))
        return path

    def build_run_manifest(
        self,
        original_manifests: list[dict[str, Any]],
        anonymized_manifests: list[dict[str, Any]],
        qa_manifests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the top-level run manifest."""
        return {
            "run_id": self.run_id,
            "company_pseudonym": self._company_pseudonym,
            "schema_version": self.schema_version,
            "parser_version": self.parser_version,
            "generated_at": datetime.now(UTC).isoformat(),
            "original_manifests": original_manifests,
            "anonymized_manifests": anonymized_manifests,
            "qa_manifests": qa_manifests,
        }

    def _sanitize_artifact_id(self, artifact_id: str) -> str:
        """Replace the ticker in artifact IDs with the company pseudonym."""
        return artifact_id.replace(self.ticker, self._company_pseudonym)

    def _sanitize_path(self, path: str) -> str:
        """Replace ticker in paths with company pseudonym."""
        return path.replace(self.ticker, self._company_pseudonym)

    @staticmethod
    def semantic_hash(manifest: dict[str, Any]) -> str:
        """Compute semantic hash excluding run timestamps."""
        copy = {
            k: v
            for k, v in manifest.items()
            if k not in ("fetch_timestamp", "generated_at", "run_id")
        }
        canonical = orjson.dumps(copy, option=orjson.OPT_SORT_KEYS)
        return hashlib.sha256(canonical).hexdigest()
