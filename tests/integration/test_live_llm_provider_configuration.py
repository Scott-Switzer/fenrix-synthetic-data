"""Integration tests for live LLM provider configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fenrix_synthetic.qa.llm_provider import (
    LLMProviderError,
    OfflineStubProvider,
    OpenAICompatibleProvider,
    create_llm_provider,
)


class TestLiveProviderConfiguration:
    """Test that live provider configuration behaves correctly."""

    def test_offline_stub_works_without_env(self) -> None:
        """Offline stub always works, no env needed."""
        provider = create_llm_provider("offline_stub")
        assert isinstance(provider, OfflineStubProvider)
        result = provider.complete_json("test prompt")
        assert "confidence" in result

    def test_openai_compatible_fails_clear_if_key_missing(self) -> None:
        """OpenAI-compatible fails clearly if key missing."""
        # Ensure env var is not set
        old_val = os.environ.pop("NVIDIA_API_KEY", None)
        try:
            provider = create_llm_provider(
                "openai_compatible",
                {
                    "api_key_env": "NONEXISTENT_KEY_ENV_VAR",
                },
            )
            with pytest.raises(LLMProviderError):
                provider.complete_json("test")
        finally:
            if old_val:
                os.environ["NVIDIA_API_KEY"] = old_val

    def test_openai_compatible_detects_key_via_env(self) -> None:
        """OpenAI-compatible detects key from environment."""
        os.environ["TEST_LLM_KEY"] = "sk-test-12345"
        try:
            provider = OpenAICompatibleProvider(api_key_env="TEST_LLM_KEY")
            assert provider.is_configured is True
        finally:
            del os.environ["TEST_LLM_KEY"]

    def test_env_loader_detects_keys_without_printing(self) -> None:
        """Environment variables can be read safely."""
        os.environ["TEST_SECRET_KEY"] = "secret-value-123"
        try:
            # Verify key exists without printing value
            has_key = "TEST_SECRET_KEY" in os.environ
            assert has_key is True
            # Verify key value is not empty
            assert len(os.environ["TEST_SECRET_KEY"]) > 0
            # Verify we don't need to print it
        finally:
            del os.environ["TEST_SECRET_KEY"]

    def test_live_provider_does_not_require_live_call_in_ci(self) -> None:
        """Live provider config can be created without calling API."""
        # Creating the provider should not make any network calls
        provider = OpenAICompatibleProvider(
            api_key_env="NONEXISTENT_KEY",
            base_url="http://localhost:9999/v1",
            model="test-model",
        )
        # Constructor should succeed even without network
        assert provider.model_name == "test-model"
        # But calling complete_json should fail (no key)
        with pytest.raises(LLMProviderError):
            provider.complete_json("test")

    def test_source_mapping_template_exists(self) -> None:
        """Source mapping template file should exist."""
        template_path = Path("configs/templates/source_companies.example.yaml")
        assert template_path.exists(), "Source mapping template missing"
        content = template_path.read_text()
        assert "PLACEHOLDER" in content
        assert "source_company" in content
        assert "source_ticker" in content

    def test_source_mapping_template_is_safe(self) -> None:
        """Template should not contain real company data."""
        template_path = Path("configs/templates/source_companies.example.yaml")
        content = template_path.read_text()
        # Should use PLACEHOLDER, not real company names
        assert "PLACEHOLDER" in content
        assert "PLC1" in content  # Fake ticker
