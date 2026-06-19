"""Unit tests for hashing utilities."""

from pathlib import Path

import pytest

from fenrix_synthetic.storage.hashing import (
    hash_bytes,
    hash_dict,
    hash_file,
    hash_object,
    hash_string,
)


class TestHashBytes:
    """Test hash_bytes function."""

    def test_empty_bytes(self):
        result = hash_bytes(b"")
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_simple_bytes(self):
        result = hash_bytes(b"hello")
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_unicode_bytes(self):
        result = hash_bytes(b"hello")
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


class TestHashString:
    """Test hash_string function."""

    def test_empty_string(self):
        result = hash_string("")
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_simple_string(self):
        result = hash_string("hello")
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_unicode_string(self):
        result = hash_string("Hello, 世界!")
        # Just verify it's a valid hash
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestHashFile:
    """Test hash_file function."""

    def test_hash_file(self, temp_file: Path, sample_text: str):
        result = hash_file(temp_file)
        expected = hash_string(sample_text)
        assert result == expected

    def test_hash_file_deterministic(self, temp_file: Path):
        result1 = hash_file(temp_file)
        result2 = hash_file(temp_file)
        assert result1 == result2

    def test_hash_nonexistent_file(self, temp_dir: Path):
        with pytest.raises(FileNotFoundError):
            hash_file(temp_dir / "nonexistent.txt")


class TestHashObject:
    """Test hash_object function."""

    def test_hash_dict(self):
        obj = {"key": "value", "number": 42}
        result = hash_object(obj)
        assert len(result) == 64

    def test_hash_list(self):
        obj = [1, 2, 3, "test"]
        result = hash_object(obj)
        assert len(result) == 64

    def test_hash_nested(self):
        obj = {"outer": {"inner": [1, 2, 3]}}
        result = hash_object(obj)
        assert len(result) == 64

    def test_deterministic(self, sample_dict: dict):
        result1 = hash_object(sample_dict)
        result2 = hash_object(sample_dict)
        assert result1 == result2

    def test_key_order_independent(self):
        obj1 = {"a": 1, "b": 2, "c": 3}
        obj2 = {"c": 3, "a": 1, "b": 2}
        assert hash_object(obj1) == hash_object(obj2)

    def test_nested_key_order_independent(self):
        obj1 = {"outer": {"b": 2, "a": 1}}
        obj2 = {"outer": {"a": 1, "b": 2}}
        assert hash_object(obj1) == hash_object(obj2)


class TestHashDict:
    """Test hash_dict function."""

    def test_hash_dict(self):
        result = hash_dict({"a": 1, "b": 2})
        assert len(result) == 64

    def test_same_as_hash_object(self, sample_dict: dict):
        assert hash_dict(sample_dict) == hash_object(sample_dict)
