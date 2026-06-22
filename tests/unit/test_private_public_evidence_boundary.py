"""Tests for the private/public evidence boundary."""

from __future__ import annotations

import pytest

from fenrix_synthetic.professor.evidence import (
    DetectedEntity,
    PrivateSourceRef,
    PublicSerializationError,
    SanitizedSection,
    SourceFiling,
    SourceSection,
    build_provenance_key,
    compute_opaque_id,
    sanitize_for_public,
    validate_provenance_key,
    validate_public_artifact,
)


class TestPrivatePublicEvidenceBoundary:
    def test_provenance_key_format(self) -> None:
        key = build_provenance_key("COMPANY_001", "FILING", "10K", "2024", "ITEM7")
        assert key == "COMPANY_001:FILING:10K:2024:ITEM7"
        assert validate_provenance_key(key)

    def test_provenance_key_invalid(self) -> None:
        assert not validate_provenance_key("")
        assert not validate_provenance_key("invalid")
        assert not validate_provenance_key("FOO:FILING")

    def test_opaque_id_deterministic(self) -> None:
        id1 = compute_opaque_id("a", "b", "c")
        id2 = compute_opaque_id("a", "b", "c")
        assert id1 == id2
        assert len(id1) == 16

    def test_opaque_id_different_inputs(self) -> None:
        id1 = compute_opaque_id("a", "b")
        id2 = compute_opaque_id("a", "c")
        assert id1 != id2

    def test_private_source_ref_has_private_fields(self) -> None:
        ref = PrivateSourceRef(
            ref_id="ref-001",
            ref_type="filing",
            company_id="COMPANY_001",
            private_value="CHC",
            source_path="/private/path",
            source_url="https://sec.gov/...",
        )
        assert ref.private_value == "CHC"
        assert ref.source_path == "/private/path"

    def test_public_serialization_rejects_private_fields(self) -> None:
        """SanitizedSection must not contain source_ref or text_content."""
        section = SourceSection(
            section_id="sec-001",
            filing_id="filing-001",
            company_id="COMPANY_001",
            item_id="ITEM_7",
            item_title="MD&A",
            text_content="private text",
            provenance_key="COMPANY_001:SECTION:10K:2024:ITEM7",
        )
        with pytest.raises(PublicSerializationError):
            sanitize_for_public(section)

    def test_public_serialization_passes_for_sanitized_section(self) -> None:
        section = SanitizedSection(
            section_id="san-001",
            company_id="COMPANY_001",
            item_id="ITEM_7",
            item_title="MD&A",
            sanitized_text="public safe text",
            provenance_key="COMPANY_001:SECTION:10K:2024:ITEM7",
        )
        result = sanitize_for_public(section)
        assert "sanitized_text" in result
        assert "text_content" not in result

    def test_validate_public_artifact_catches_private_fields(self) -> None:
        artifact = {
            "section_id": "san-001",
            "company_id": "COMPANY_001",
            "source_ref": {"private_value": "CHC"},
        }
        violations = validate_public_artifact(artifact)
        assert len(violations) > 0
        assert any("source_ref" in v for v in violations)

    def test_validate_public_artifact_passes_clean(self) -> None:
        artifact = {
            "section_id": "san-001",
            "company_id": "COMPANY_001",
            "sanitized_text": "public text",
            "provenance_key": "COMPANY_001:SECTION:10K:2024:ITEM7",
        }
        violations = validate_public_artifact(artifact)
        assert violations == []

    def test_source_filing_provenance_key_present(self) -> None:
        filing = SourceFiling(
            filing_id="filing-001",
            company_id="COMPANY_001",
            form_type="10-K",
            filing_date="2025-02-15",
            period_end="2024-12-31",
            accession_ref="[PRIVATE]",
            provenance_key="COMPANY_001:FILING:10K:2024",
        )
        assert filing.provenance_key == "COMPANY_001:FILING:10K:2024"

    def test_detected_entity_has_private_detected_text(self) -> None:
        entity = DetectedEntity(
            entity_id="ent-001",
            company_id="COMPANY_001",
            entity_type="company",
            detected_text="Canary Holdings Corporation",
            detection_method="gliner",
            provenance_key="COMPANY_001:ENTITY:GLINER:ent-001",
        )
        # detected_text is private — must not appear in public serialization
        with pytest.raises(PublicSerializationError):
            sanitize_for_public(entity)
