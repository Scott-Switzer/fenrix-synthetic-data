"""Test that production config contains no real identity defaults."""

from __future__ import annotations

from pathlib import Path

import yaml


class TestProductionConfigNoRealIdentityDefaults:
    """Production config must only contain placeholder values."""

    def _load_config(self) -> dict:
        """Load the production example config."""
        config_path = (
            Path(__file__).parent.parent.parent
            / "configs"
            / "professor_bundle.production.example.yaml"
        )
        assert config_path.exists(), f"Config not found: {config_path}"
        return yaml.safe_load(config_path.read_text())

    def test_company_id_is_placeholder(self) -> None:
        """Company ID must be placeholder, not real."""
        config = self._load_config()
        company_id = config.get("company_id", "")
        known_real = {"HBAN", "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "JPM", "BAC"}
        assert company_id not in known_real, f"Config leaks real company_id: {company_id}"

    def test_no_ticker_at_top_level(self) -> None:
        """No ticker at top level."""
        config = self._load_config()
        assert "ticker" not in config, "Top-level ticker should not be in production config"

    def test_no_cik_at_top_level(self) -> None:
        """No CIK at top level."""
        config = self._load_config()
        assert "cik" not in config, "Top-level cik should not be in production config"

    def test_no_company_name_at_top_level(self) -> None:
        """No company name at top level."""
        config = self._load_config()
        assert "company_name" not in config, (
            "Top-level company_name should not be in production config"
        )

    def test_sec_user_agent_is_placeholder(self) -> None:
        """SEC User-Agent must be a placeholder, not a real personal email."""
        config = self._load_config()
        sec = config.get("sec", {})
        user_agent = sec.get("user_agent", "")
        assert "gmail.com" not in user_agent
        assert "outlook.com" not in user_agent
        assert "yahoo.com" not in user_agent
        if user_agent:
            assert len(user_agent) >= 10

    def test_sec_rate_limit_within_bounds(self) -> None:
        """SEC rate limit must be within safe bounds (<= 10 req/sec)."""
        config = self._load_config()
        sec = config.get("sec", {})
        max_rps = sec.get("max_requests_per_second", 0)
        if max_rps:
            assert 1 <= max_rps <= 10

    def test_cache_dir_not_user_path(self) -> None:
        """Cache path must not be a real system path."""
        config = self._load_config()
        sec = config.get("sec", {})
        cache_dir = str(sec.get("cache_dir", ""))
        if cache_dir:
            assert "/Users/" not in cache_dir
            assert "/home/" not in cache_dir

    def test_live_network_defaults_false(self) -> None:
        """live_network must default to false in production example."""
        config = self._load_config()
        sec = config.get("sec", {})
        live_network = sec.get("live_network", False)
        assert live_network is False

    def test_no_real_urls_in_config(self) -> None:
        """Config must not contain real company URLs."""
        config = self._load_config()
        config_text = yaml.dump(config)
        known_url_patterns = [
            "huntington.com",
            "nvidia.com",
            "apple.com",
        ]
        for pattern in known_url_patterns:
            assert pattern not in config_text.lower()

    def test_no_real_identifiers_in_gliner_section(self) -> None:
        """GLiNER section must not contain real company identifiers."""
        config = self._load_config()
        gliner = config.get("gliner", {})
        gliner_text = str(gliner)
        known_real = {"HBAN", "Huntington", "NVDA", "NVIDIA"}
        for item in known_real:
            assert item not in gliner_text

    def test_no_real_identifiers_in_sec_section(self) -> None:
        """SEC section must not contain real company identifiers."""
        config = self._load_config()
        sec = config.get("sec", {})
        sec_text = str(sec)
        known_real = {"HBAN", "Huntington", "NVDA", "NVIDIA"}
        for item in known_real:
            assert item not in sec_text

    def test_no_secrets_in_config(self) -> None:
        """Config must not contain api keys, tokens, or passwords."""
        config = self._load_config()
        config_text = yaml.dump(config).lower()
        secret_patterns = ["api_key", "apikey", "token", "secret", "password", "auth"]
        for pattern in secret_patterns:
            assert pattern not in config_text

    def test_gliner_model_id_is_generic(self) -> None:
        """GLiNER model ID must not encode real company names."""
        config = self._load_config()
        gliner = config.get("gliner", {})
        model_id = gliner.get("model_id", "")
        assert "huntington" not in model_id.lower()
        assert "nvidia" not in model_id.lower()
        assert "apple" not in model_id.lower()

    def test_sec_cache_dir_no_private_abs_path(self) -> None:
        """SEC cache_dir must be a relative path, not an absolute private path."""
        config = self._load_config()
        sec = config.get("sec", {})
        cache_dir = str(sec.get("cache_dir", ""))
        if cache_dir:
            assert not cache_dir.startswith("/")
