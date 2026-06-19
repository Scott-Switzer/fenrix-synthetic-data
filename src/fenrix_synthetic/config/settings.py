"""Global settings and campaign configuration."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..schemas import StageName


class Settings(BaseSettings):
    """Global application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Data paths
    data_root: Path = Field(default=Path("data"), description="Base data directory")

    # Pipeline settings
    pipeline_version: str = Field(default="0.1.0", description="Pipeline version")
    resume_enabled: bool = Field(default=True, description="Enable resume from checkpoints")
    fail_fast: bool = Field(default=False, description="Stop on first stage failure")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")
    log_format: str = Field(default="json", description="Log format: json or text")

    # Security
    secret_keys_pattern: str = Field(
        default=r"(?i)(key|token|secret|password|auth|credential)",
        description="Regex pattern for secret key names to redact",
    )


class CampaignConfig(BaseModel):
    """Campaign configuration for a specific run."""

    company_id: str = Field(..., description="Company ID to process")
    stages: list[StageName] = Field(
        default_factory=lambda: [StageName.INGEST, StageName.EXTRACT, StageName.MANIFEST],
        description="Stages to execute",
    )
    resume: bool = Field(default=True, description="Resume from checkpoints")
    stop_on_failure: bool = Field(default=True, description="Stop campaign on stage failure")
    config_overrides: dict[str, Any] = Field(default_factory=dict, description="Config overrides")

    @field_validator("stages", mode="before")
    @classmethod
    def parse_stages(cls, v: list[str | StageName]) -> list[StageName]:
        if isinstance(v, list):
            return [StageName(s) if isinstance(s, str) else s for s in v]
        return v


_settings: Settings | None = None


def load_settings() -> Settings:
    """Load global settings (singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_settings() -> Settings:
    """Get loaded settings."""
    if _settings is None:
        return load_settings()
    return _settings
