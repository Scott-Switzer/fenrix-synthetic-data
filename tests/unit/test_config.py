"""Unit tests for configuration loading."""

from pathlib import Path

import pytest

from fenrix_synthetic.config import load_campaign_config, load_company_config
from fenrix_synthetic.config.settings import CampaignConfig, Settings
from fenrix_synthetic.schemas import CompanyConfig, StageName


class TestSettings:
    """Test Settings model."""

    def test_default_settings(self):
        settings = Settings()
        assert settings.data_root == Path("data")
        assert settings.pipeline_version == "0.1.0"
        assert settings.resume_enabled is True
        assert settings.fail_fast is False
        assert settings.log_level == "INFO"
        assert settings.log_format == "json"

    def test_settings_from_env(self, monkeypatch):
        monkeypatch.setenv("DATA_ROOT", "/custom/data")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("RESUME_ENABLED", "false")

        settings = Settings()
        assert settings.data_root == Path("/custom/data")
        assert settings.log_level == "DEBUG"
        assert settings.resume_enabled is False


class TestCampaignConfig:
    """Test CampaignConfig model."""

    def test_default_campaign_config(self):
        config = CampaignConfig(company_id="C001")
        assert config.company_id == "C001"
        assert config.stages == [StageName.INGEST, StageName.EXTRACT, StageName.MANIFEST]
        assert config.resume is True
        assert config.stop_on_failure is True

    def test_custom_stages(self):
        config = CampaignConfig(
            company_id="C001",
            stages=[StageName.INGEST],
        )
        assert config.stages == [StageName.INGEST]

    def test_stage_names_from_strings(self):
        config = CampaignConfig(
            company_id="C001",
            stages=["ingest", "extract"],
        )
        assert config.stages == [StageName.INGEST, StageName.EXTRACT]


class TestLoadCompanyConfig:
    """Test load_company_config function."""

    def test_load_valid_config(self, temp_dir: Path):
        # Create config file
        config_file = temp_dir / "company.yaml"
        config_file.write_text("""
companies:
  C001:
    source_identity: "CHC"
    data_root: "data"
    raw_dir: "data/raw"
    bronze_dir: "data/bronze"
""")

        config = load_company_config("C001", config_file)
        assert isinstance(config, CompanyConfig)
        assert config.company_id == "C001"
        assert config.source_identity == "CHC"

    def test_load_config_missing_file(self, temp_dir: Path):
        with pytest.raises(FileNotFoundError):
            load_company_config("C001", temp_dir / "nonexistent.yaml")

    def test_load_config_missing_company(self, temp_dir: Path):
        config_file = temp_dir / "company.yaml"
        config_file.write_text("""
companies:
  C002:
    source_identity: "TEST"
""")

        with pytest.raises(ValueError) as exc_info:
            load_company_config("C001", config_file)
        assert "Company C001 not found" in str(exc_info.value)

    def test_load_config_resolves_paths(self, temp_dir: Path):
        config_file = temp_dir / "company.yaml"
        config_file.write_text("""
companies:
  C001:
    source_identity: "CHC"
    data_root: "custom_data"
    raw_dir: "custom_data/raw"
    bronze_dir: "custom_data/bronze"
""")

        config = load_company_config("C001", config_file)
        # Paths are resolved relative to CWD (not config file location)
        assert "custom_data" in str(config.data_root)
        assert config.raw_dir.name == "raw"
        assert config.bronze_dir.name == "bronze"


class TestLoadCampaignConfig:
    """Test load_campaign_config function."""

    def test_load_valid_campaign(self, temp_dir: Path):
        config_file = temp_dir / "campaign.yaml"
        config_file.write_text("""
company_id: "C001"
stages:
  - "ingest"
  - "extract"
resume: true
stop_on_failure: true
config_overrides: {}
""")

        config = load_campaign_config(config_file)
        assert config["company_id"] == "C001"
        assert config["stages"] == ["ingest", "extract"]
        assert config["resume"] is True

    def test_load_missing_campaign(self, temp_dir: Path):
        config = load_campaign_config(temp_dir / "nonexistent.yaml")
        assert config == {}

    def test_load_empty_campaign(self, temp_dir: Path):
        config_file = temp_dir / "campaign.yaml"
        config_file.write_text("")
        config = load_campaign_config(config_file)
        assert config == {}
