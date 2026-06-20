"""Pipeline configuration models."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class TickerConfig:
    """Configuration for a single ticker."""

    ticker: str
    enabled: bool = True
    years: int = 10


@dataclass
class PipelineConfig:
    """Configuration for a pipeline run."""

    tickers: list[TickerConfig]
    output_root: Path
    years: int = 10
    collect_only: bool = False
    anonymize_only: bool = False
    enable_nvidia: bool = False
    resume: bool = True
    force_refresh: set[str] = field(default_factory=set)
    dry_run: bool = False
    sec_user_agent: str | None = None
    run_id: str = ""

    def __post_init__(self) -> None:
        if not self.run_id:
            ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            self.run_id = f"run_{ts}"

    @classmethod
    def from_csv(
        cls,
        csv_path: Path,
        output_root: Path,
        years: int = 10,
        **kwargs: Any,
    ) -> PipelineConfig:
        """Load ticker list from CSV."""
        tickers: list[TickerConfig] = []
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row.get("ticker", "").strip().upper()
                enabled = row.get("enabled", "1").strip() in ("1", "true", "True", "TRUE")
                if ticker:
                    tickers.append(TickerConfig(ticker=ticker, enabled=enabled, years=years))
        return cls(
            tickers=[t for t in tickers if t.enabled],
            output_root=Path(output_root),
            years=years,
            **kwargs,
        )

    @classmethod
    def from_ticker(
        cls,
        ticker: str,
        output_root: Path,
        years: int = 10,
        **kwargs: Any,
    ) -> PipelineConfig:
        """Create config for a single ticker."""
        return cls(
            tickers=[TickerConfig(ticker=ticker.upper(), enabled=True, years=years)],
            output_root=Path(output_root),
            years=years,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tickers": [
                {"ticker": t.ticker, "enabled": t.enabled, "years": t.years} for t in self.tickers
            ],
            "output_root": str(self.output_root),
            "years": self.years,
            "collect_only": self.collect_only,
            "anonymize_only": self.anonymize_only,
            "enable_nvidia": self.enable_nvidia,
            "resume": self.resume,
            "force_refresh": sorted(self.force_refresh),
            "dry_run": self.dry_run,
            "sec_user_agent_configured": self.sec_user_agent is not None,
        }
