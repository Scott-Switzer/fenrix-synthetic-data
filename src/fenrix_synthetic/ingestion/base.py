"""Base ingestor interface for FENRIX Synthetic Data."""

from abc import ABC, abstractmethod
from pathlib import Path

from ..schemas.manifests import SourceManifest


class BaseIngestor(ABC):
    """Abstract base class for data ingestors."""

    @abstractmethod
    def discover(self, company_id: str) -> list[SourceManifest]:
        """Return available filings for a company."""
        pass

    @abstractmethod
    def fetch(
        self,
        manifest: SourceManifest,
        target_path: Path,
        expected_hash: str | None = None,
    ) -> Path:
        """Download filing to target_path. Verify hash if provided."""
        pass
