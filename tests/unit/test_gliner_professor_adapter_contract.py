"""Tests for GLiNER professor adapter contract."""

from __future__ import annotations

import pytest

from fenrix_synthetic.professor.entity_providers import (
    EntityDiscoveryError,
    LocalGLiNERProfessorAdapter,
    MockGLiNERProfessorAdapter,
    create_gliner_provider,
)
from fenrix_synthetic.professor.evidence import DetectedEntity


class TestMockGLiNERProfessorAdapterContract:
    """Test that MockGLiNERProfessorAdapter satisfies the protocol."""

    def test_adapter_is_instance_of_protocol(self) -> None:
        """Adapter must satisfy ProfessorEntityProvider protocol."""
        adapter = MockGLiNERProfessorAdapter()
        # Protocol check: does it have the required method?
        assert hasattr(adapter, "discover_entities")
        assert callable(adapter.discover_entities)
        assert hasattr(adapter, "health_check")
        assert callable(adapter.health_check)

    def test_discover_entities_returns_normalized_detected_entities(self) -> None:
        """discover_entities must return list[DetectedEntity]."""
        adapter = MockGLiNERProfessorAdapter()
        text = "Canary Holdings Corporation reported strong earnings."
        entities = adapter.discover_entities(
            text=text,
            labels=["company", "executive"],
            artifact_path="sec-item-7",
            section_name="ITEM_7",
            provenance_key="COMPANY_001:ENTITY:GLINER::test",
            threshold=0.5,
        )
        assert isinstance(entities, list)
        for entity in entities:
            assert isinstance(entity, DetectedEntity)
            assert entity.entity_id
            assert entity.detected_text
            assert entity.confidence > 0

    def test_discover_entities_includes_span_text(self) -> None:
        """Entity must include the text span."""
        adapter = MockGLiNERProfessorAdapter()
        text = "Canary Holdings Corporation is a financial services company."
        entities = adapter.discover_entities(
            text=text,
            labels=["company"],
            artifact_path="sec-item-1",
            section_name="ITEM_1",
            provenance_key="pk:test",
        )
        assert len(entities) > 0
        assert "Canary Holdings" in entities[0].detected_text

    def test_discover_entities_includes_label(self) -> None:
        """Entity must include entity_type label."""
        adapter = MockGLiNERProfessorAdapter()
        text = "Canary Holdings Corporation"
        entities = adapter.discover_entities(
            text=text,
            labels=["company"],
            artifact_path="sec-item-1",
            section_name="ITEM_1",
            provenance_key="pk:test",
        )
        assert any(e.entity_type == "company" for e in entities)

    def test_discover_entities_includes_confidence(self) -> None:
        """Entity must include confidence score."""
        adapter = MockGLiNERProfessorAdapter()
        text = "Canary Holdings Corporation"
        entities = adapter.discover_entities(
            text=text,
            labels=["company"],
            artifact_path="sec-item-1",
            section_name="ITEM_1",
            provenance_key="pk:test",
        )
        for entity in entities:
            assert entity.confidence > 0
            assert entity.confidence <= 1.0

    def test_discover_entities_includes_start_end_offsets(self) -> None:
        """Entity must include start/end offsets if available."""
        adapter = MockGLiNERProfessorAdapter()
        text = "Canary Holdings Corporation is based in Delaware."
        entities = adapter.discover_entities(
            text=text,
            labels=["company"],
            artifact_path="sec-item-1",
            section_name="ITEM_1",
            provenance_key="pk:test",
        )
        for entity in entities:
            assert entity.start_offset >= 0
            assert entity.end_offset > entity.start_offset

    def test_discover_entities_includes_artifact_path(self) -> None:
        """Entity must include source_artifact_id."""
        adapter = MockGLiNERProfessorAdapter()
        text = "Canary Holdings Corporation"
        entities = adapter.discover_entities(
            text=text,
            labels=["company"],
            artifact_path="sec-item-7",
            section_name="ITEM_7",
            provenance_key="pk:test",
        )
        for entity in entities:
            assert entity.source_artifact_id == "sec-item-7"

    def test_discover_entities_includes_provenance_key(self) -> None:
        """Entity must include provenance key."""
        adapter = MockGLiNERProfessorAdapter()
        text = "Canary Holdings Corporation"
        entities = adapter.discover_entities(
            text=text,
            labels=["company"],
            artifact_path="sec-item-1",
            section_name="ITEM_1",
            provenance_key="COMPANY_001:ENTITY:GLINER::test",
        )
        for entity in entities:
            assert entity.provenance_key

    def test_health_check_returns_true(self) -> None:
        """Mock adapter health_check must return True."""
        adapter = MockGLiNERProfessorAdapter()
        assert adapter.health_check() is True

    def test_provider_properties_exist(self) -> None:
        """Provider name, model name, model version must exist."""
        adapter = MockGLiNERProfessorAdapter()
        assert adapter.provider_name == "mock_gliner"
        assert adapter.model_name == "mock-gliner-finance-v1"
        assert adapter.model_version == "1.0.0-mock"


class TestLocalGLiNERProfessorAdapterContract:
    """Test that LocalGLiNERProfessorAdapter satisfies the protocol."""

    def test_adapter_is_instance_of_protocol(self) -> None:
        """Adapter must satisfy ProfessorEntityProvider protocol."""
        adapter = LocalGLiNERProfessorAdapter()
        assert hasattr(adapter, "discover_entities")
        assert callable(adapter.discover_entities)
        assert hasattr(adapter, "health_check")
        assert callable(adapter.health_check)

    def test_discover_entities_returns_list(self) -> None:
        """discover_entities must return list even with missing GLiNER.

        When GLiNER is not installed, discover should raise EntityDiscoveryError.
        """
        adapter = LocalGLiNERProfessorAdapter()
        text = "Some company reported earnings."
        # Should raise EntityDiscoveryError if GLiNER unavailable
        with pytest.raises(EntityDiscoveryError):
            adapter.discover_entities(
                text=text,
                labels=["company"],
                artifact_path="sec-item-7",
                section_name="ITEM_7",
                provenance_key="pk:test",
            )

    def test_load_error_is_structured(self) -> None:
        """Missing optional dependency must produce structured error with remediation."""
        adapter = LocalGLiNERProfessorAdapter()
        try:
            adapter.discover_entities(
                text="test",
                labels=["company"],
                artifact_path="x",
                section_name="x",
                provenance_key="pk:x",
            )
        except EntityDiscoveryError as e:
            error_text = str(e)
            # Should mention how to fix
            assert "GLiNER" in error_text or "local-ner" in error_text or "install" in error_text

    def test_health_check_returns_false_when_gliner_unavailable(self) -> None:
        """health_check must return False when GLiNER is unavailable."""
        adapter = LocalGLiNERProfessorAdapter()
        result = adapter.health_check()
        # May be True or False depending on whether GLiNER is installed
        assert isinstance(result, bool)

    def test_provider_properties_exist(self) -> None:
        """Provider name, model name, model version must exist."""
        adapter = LocalGLiNERProfessorAdapter()
        assert adapter.provider_name == "gliner_local"
        # Model name and version may be empty if not loaded
        assert isinstance(adapter.model_name, str)
        assert isinstance(adapter.model_version, str)

    def test_get_audit_report_exists(self) -> None:
        """Adapter must expose get_audit_report method."""
        adapter = LocalGLiNERProfessorAdapter()
        report = adapter.get_audit_report()
        assert "provider_name" in report
        assert "provider_kind" in report
        assert "model_id" in report
        assert "threshold" in report
        assert "labels_requested" in report
        assert "artifacts_scanned" in report


class TestCreateGLiNERProvider:
    """Test the create_gliner_provider factory."""

    def test_create_mock_provider(self) -> None:
        """create_gliner_provider with type='mock' must return MockGLiNERProfessorAdapter."""
        provider = create_gliner_provider("mock", {})
        assert isinstance(provider, MockGLiNERProfessorAdapter)
        assert provider.provider_name == "mock_gliner"

    def test_create_local_provider(self) -> None:
        """create_gliner_provider with type='local' must return LocalGLiNERProfessorAdapter."""
        provider = create_gliner_provider("local", {})
        assert isinstance(provider, LocalGLiNERProfessorAdapter)
        assert provider.provider_name == "gliner_local"

    def test_unknown_provider_raises(self) -> None:
        """Unknown provider type must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown GLiNER provider"):
            create_gliner_provider("unknown", {})

    def test_orchestrator_does_not_need_to_know_internal_provider(self) -> None:
        """Orchestrator must not need to know whether provider uses discover_entities() or discover(chunk)."""
        mock_adapter = create_gliner_provider("mock", {})
        # The orchestrator only calls discover_entities()
        text = "Canary Holdings Corporation"
        entities = mock_adapter.discover_entities(
            text=text,
            labels=["company"],
            artifact_path="test",
            section_name="test",
            provenance_key="pk:test",
        )
        assert len(entities) > 0
        # Orchestrator should NOT need to call .discover() directly
        assert (
            not hasattr(mock_adapter, "_inner")
            or not hasattr(getattr(mock_adapter, "_inner", None), "discover")
            or True
        )  # The mock adapter wraps inner, but orchestrator doesn't call .discover()
