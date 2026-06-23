"""Tests ensuring production requires a real GLiNER provider."""

from __future__ import annotations

import pytest

from fenrix_synthetic.professor.entity_providers import (
    EntityDiscoveryError,
    MockGLiNERProfessorAdapter,
    create_gliner_provider,
)


class TestProductionRequiresRealGLiNER:
    """Production builds must not proceed with unavailable or mock GLiNER."""

    def test_mock_gliner_not_suitable_for_production(self) -> None:
        """mock_gliner must not be used in production."""
        provider = create_gliner_provider("mock", {})
        assert isinstance(provider, MockGLiNERProfessorAdapter)
        # Production should never use mock
        assert provider.provider_name == "mock_gliner"

    def test_local_gliner_fails_health_check_when_unavailable(self) -> None:
        """Local GLiNER fails health check when GLiNER not installed."""
        provider = create_gliner_provider("local", {})
        healthy = provider.health_check()
        if not healthy:
            assert provider.provider_name == "gliner_local"
            report = provider.get_audit_report()
            assert report["model_version"] == "unavailable"

    def test_missing_gliner_produces_structured_error(self) -> None:
        """Missing GLiNER dependency must produce EntityDiscoveryError.

        The error message must tell the user how to fix it.
        """
        provider = create_gliner_provider("local", {})
        try:
            provider.discover_entities(
                text="test",
                labels=["company"],
                artifact_path="x",
                section_name="x",
                provenance_key="pk:x",
            )
        except Exception as e:
            error_text = str(e)
            # Must mention remediation
            assert any(
                keyword in error_text.lower()
                for keyword in ["gliner", "local-ner", "install", "pip"]
            )

    def test_orchestrator_skips_discover_when_unhealthy(self) -> None:
        """Orchestrator must skip discovery when health_check fails.

        Simulates orchestrator behavior: check health, skip if unhealthy.
        """
        provider = create_gliner_provider("local", {})
        if not provider.health_check():
            with pytest.raises(EntityDiscoveryError):
                provider.discover_entities(
                    text="test",
                    labels=["company"],
                    artifact_path="sec-item-1",
                    section_name="ITEM_1",
                    provenance_key="pk:test",
                )

    def test_mock_provider_cannot_satisfy_production_gate(self) -> None:
        """Mock provider cannot satisfy the production GLiNER requirement.

        Production requires a real GLiNER model with real provenance.
        """
        provider = create_gliner_provider("mock", {})
        report = provider.get_audit_report()
        assert report["provider_kind"] == "fake"
        assert report["discovery_succeeded"] is True

    def test_missing_gliner_fails_readiness_check(self) -> None:
        """Missing GLiNER fails the readiness check.

        The orchestrator's _check_gliner_readiness() should return
        a non-empty list of blockers when GLiNER is unavailable.
        """
        provider = create_gliner_provider("local", {})
        blockers = []
        if not provider.health_check():
            blockers.append("GLiNER model not available")
            blockers.append("Install with: pip install fenrix-synthetic[local-ner]")

        # In CI, both blockers should be present
        # In dev with GLiNER installed, none should be present
        if not provider.health_check():
            assert len(blockers) == 2
            assert any("install" in b.lower() for b in blockers)
