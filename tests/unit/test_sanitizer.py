from __future__ import annotations

from fenrix_synthetic.masking.sanitizer import (
    compute_text_hash,
    sanitize_metadata,
    sanitize_metadata_value,
    sanitize_path_name,
)


class TestMetadataSanitizer:
    def test_sanitize_metadata_value(self):
        registry_values = {"Canary Holdings", "CHC"}
        result = sanitize_metadata_value("Canary Holdings Corp", registry_values)
        assert "[REDACTED]" in result
        assert "Canary Holdings" not in result

    def test_sanitize_metadata_value_case_insensitive(self):
        registry_values = {"Canary Holdings"}
        result = sanitize_metadata_value("canary holdings corp", registry_values)
        assert "[REDACTED]" in result

    def test_sanitize_metadata_dict(self):
        metadata = {
            "title": "Canary Holdings 10-K",
            "year": 2024,
            "tags": ["CHC", "finance"],
        }
        registry_values = {"Canary Holdings", "CHC"}
        result = sanitize_metadata(metadata, registry_values)
        assert "[REDACTED]" in result["title"]
        assert "[REDACTED]" in result["tags"][0]
        assert result["year"] == 2024

    def test_skip_keys_preserved(self):
        metadata = {
            "artifact_id": "bronze-C001-1234",
            "title": "bronze-C001-1234 info",
        }
        registry_values = {"bronze-C001-1234"}
        result = sanitize_metadata(metadata, registry_values, skip_keys={"artifact_id"})
        assert result["artifact_id"] == "bronze-C001-1234"
        assert "info" in result["title"]

    def test_no_matches_unchanged(self):
        metadata = {"title": "Clean Document", "year": 2024}
        result = sanitize_metadata(metadata, {"NONEXISTENT"})
        assert result == metadata


class TestSanitizePathName:
    def test_basic(self):
        result = sanitize_path_name("Canary Holdings Corp")
        assert "/" not in result

    def test_special_characters_replaced(self):
        result = sanitize_path_name("hello world:test")
        assert "hello_world_test" == result


class TestComputeTextHash:
    def test_deterministic(self):
        h1 = compute_text_hash("hello")
        h2 = compute_text_hash("hello")
        assert h1 == h2

    def test_different_text_different_hash(self):
        h1 = compute_text_hash("hello")
        h2 = compute_text_hash("world")
        assert h1 != h2

    def test_hash_length(self):
        h = compute_text_hash("test")
        assert len(h) == 64
