"""Tests for GLiNER audit report schema."""

from __future__ import annotations

import json

from fenrix_synthetic.professor.entity_providers import (
    LocalGLiNERProfessorAdapter,
    MockGLiNERProfessorAdapter,
)


class TestMockGLiNERAuditReportSchema:
    """Validate MockGLiNERProfessorAdapter audit report schema."""

    REQUIRED_FIELDS = (
        "provider_name",
        "provider_kind",
        "model_id",
        "model_loaded",
        "threshold",
        "labels_configured",
        "labels_requested",
        "artifacts_scanned",
        "entity_count_total",
        "entity_discovery_count",
        "failure_count",
        "total_inference_time_seconds",
        "discovery_succeeded",
    )

    def test_audit_report_has_required_fields(self) -> None:
        """Audit report must have all required fields."""
        provider = MockGLiNERProfessorAdapter()
        report = provider.get_audit_report()
        for field in self.REQUIRED_FIELDS:
            assert field in report, f"Missing required field: {field}"

    def test_provider_name_identifies_provider(self) -> None:
        """Provider name must identify the provider."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert report["provider_name"] == "mock_gliner"

    def test_provider_kind_is_fake(self) -> None:
        """Provider kind must be 'fake' for mock."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert report["provider_kind"] == "fake"

    def test_model_id_present(self) -> None:
        """Model ID must be present."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert isinstance(report["model_id"], str)
        assert report["model_id"] == "mock-gliner-finance-v1"

    def test_model_loaded_flag(self) -> None:
        """Model loaded flag must be boolean."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert isinstance(report["model_loaded"], bool)

    def test_threshold_is_float(self) -> None:
        """Threshold must be float."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert isinstance(report["threshold"], float)
        assert 0.0 <= report["threshold"] <= 1.0

    def test_labels_configured_is_list(self) -> None:
        """Labels configured must be list."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert isinstance(report["labels_configured"], list)

    def test_artifacts_scanned_is_int(self) -> None:
        """Artifacts scanned must be int."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert isinstance(report["artifacts_scanned"], int)

    def test_entity_count_total(self) -> None:
        """Entity count total must be int."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert isinstance(report["entity_count_total"], int)

    def test_discovery_succeeded(self) -> None:
        """Discovery succeeded must be boolean."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert isinstance(report["discovery_succeeded"], bool)

    def test_total_inference_time(self) -> None:
        """Total inference time must be float."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        assert isinstance(report["total_inference_time_seconds"], float)

    def test_report_serializes_to_json(self) -> None:
        """Report must be JSON-serializable."""
        report = MockGLiNERProfessorAdapter().get_audit_report()
        serialized = json.dumps(report)
        assert isinstance(serialized, str)
        assert "mock_gliner" in serialized


class TestLocalGLiNERAuditReportSchema:
    """Validate LocalGLiNERProfessorAdapter audit report schema."""

    LOCAL_REQUIRED_FIELDS = (
        "provider_name",
        "provider_kind",
        "model_id",
        "model_version",
        "threshold",
        "labels_requested",
        "artifacts_scanned",
        "empty_artifact_count",
        "failed_artifact_count",
        "spans_detected_by_label",
        "coverage_summary",
        "provenance_keys",
        "warnings",
    )

    def test_audit_report_has_required_fields(self) -> None:
        """Audit report must have all required fields."""
        provider = LocalGLiNERProfessorAdapter()
        report = provider.get_audit_report()
        for field in self.LOCAL_REQUIRED_FIELDS:
            assert field in report, f"Missing required field: {field}"

    def test_provider_name_identifies_provider(self) -> None:
        """Provider name must identify provider."""
        report = LocalGLiNERProfessorAdapter().get_audit_report()
        assert report["provider_name"] == "gliner_local"

    def test_provider_kind_is_real(self) -> None:
        """Provider kind must be 'real' for LocalGLiNER (production mode)."""
        report = LocalGLiNERProfessorAdapter().get_audit_report()
        assert report["provider_kind"] == "real"

    def test_model_version_present(self) -> None:
        """Model version must be present."""
        report = LocalGLiNERProfessorAdapter().get_audit_report()
        assert "model_version" in report
        assert isinstance(report["model_version"], str)
