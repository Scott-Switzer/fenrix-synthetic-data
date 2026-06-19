"""Configuration loading utilities."""

from pathlib import Path
from typing import Any

import yaml

from ..schemas import CompanyConfig
from .settings import load_settings


def load_company_config(company_id: str, config_path: Path | None = None) -> CompanyConfig:
    """Load company configuration from YAML file.

    Args:
        company_id: Company ID to load (e.g., C001)
        config_path: Optional path to config file. Defaults to configs/company.yaml

    Returns:
        CompanyConfig instance

    Raises:
        FileNotFoundError: If config file not found
        ValueError: If company_id not found in config
    """
    settings = load_settings()

    if config_path is None:
        config_path = Path("configs/company.yaml")

    if not config_path.is_absolute():
        config_path = settings.data_root.parent / config_path

    if not config_path.exists():
        raise FileNotFoundError(f"Company config not found: {config_path}")

    with open(config_path) as f:
        config_data = yaml.safe_load(f)

    if company_id not in config_data.get("companies", {}):
        raise ValueError(f"Company {company_id} not found in config")

    company_data = config_data["companies"][company_id]
    company_data["company_id"] = company_id

    return CompanyConfig(**company_data)


def load_campaign_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load campaign configuration from YAML file."""
    if config_path is None:
        config_path = Path("configs/campaign.yaml")

    if not config_path.exists():
        return {}

    with open(config_path) as f:
        return yaml.safe_load(f) or {}
