"""Pytest configuration and shared fixtures."""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir() -> Path:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def sample_text() -> str:
    """Sample text for hashing tests."""
    return "Hello, FENRIX Synthetic Data!"


@pytest.fixture
def sample_dict() -> dict:
    """Sample dictionary for hashing tests."""
    return {"key": "value", "number": 42, "nested": {"a": 1}}


@pytest.fixture
def sample_list() -> list:
    """Sample list for hashing tests."""
    return [1, 2, 3, "test"]


@pytest.fixture
def temp_file(temp_dir: Path, sample_text: str) -> Path:
    """Create a temporary file with sample content."""
    file_path = temp_dir / "test.txt"
    file_path.write_text(sample_text)
    return file_path
