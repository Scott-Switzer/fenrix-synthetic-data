"""TASK 7: Notebook structure test.

Validates that FENRIX_MultiCompany_Anonymization.ipynb satisfies
cloud-first requirements without executing network calls.

Checks:
- Mount Google Drive
- Read secrets via google.colab.userdata
- Clone securely, remove credential-bearing URL
- Install package, not duplicate code
- SEC archive support
- NVIDIA disabled by default
- Never print or store secrets
- Resumable structure
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_NOTEBOOK_PATH = (
    Path(__file__).parent.parent.parent / "notebooks" / "FENRIX_MultiCompany_Anonymization.ipynb"
)


@pytest.fixture(scope="module")
def notebook_data() -> dict:
    """Load notebook JSON once for all tests."""
    if not _NOTEBOOK_PATH.exists():
        pytest.skip(f"Notebook not found: {_NOTEBOOK_PATH}")
    return json.loads(_NOTEBOOK_PATH.read_text())


def _all_cell_sources(nb: dict) -> list[str]:
    """Return all code/markdown cell source content as single strings."""
    sources: list[str] = []
    for cell in nb.get("cells", []):
        src = "".join(cell.get("source", []))
        sources.append(src)
    return sources


def _code_cells(nb: dict) -> list[str]:
    """Return only code cell sources."""
    sources: list[str] = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            src = "".join(cell.get("source", []))
            sources.append(src)
    return sources


class TestNotebookStructure:
    """Verify notebook structure meets cloud-first requirements."""

    def test_notebook_exists(self) -> None:
        assert _NOTEBOOK_PATH.exists(), f"Notebook missing: {_NOTEBOOK_PATH}"

    def test_has_markdown_and_code_cells(self, notebook_data: dict) -> None:
        cells = notebook_data.get("cells", [])
        assert len(cells) >= 5, f"Expected >=5 cells, got {len(cells)}"
        types = {c.get("cell_type") for c in cells}
        assert "markdown" in types, "No markdown cells"
        assert "code" in types, "No code cells"

    def test_mount_google_drive(self, notebook_data: dict) -> None:
        code = _code_cells(notebook_data)
        drive_mount = any("drive.mount" in c for c in code)
        assert drive_mount, "Missing google.colab.drive mount"

    def test_read_secrets_via_userdata(self, notebook_data: dict) -> None:
        code = _code_cells(notebook_data)
        uses_userdata = any("userdata.get" in c for c in code)
        assert uses_userdata, "Missing google.colab.userdata usage for secrets"

    def test_github_token_required(self, notebook_data: dict) -> None:
        sources = _all_cell_sources(notebook_data)
        all_text = " ".join(sources)
        assert "GITHUB_TOKEN" in all_text, "Missing GITHUB_TOKEN reference"

    def test_sec_user_agent_required(self, notebook_data: dict) -> None:
        sources = _all_cell_sources(notebook_data)
        all_text = " ".join(sources)
        assert "SEC_USER_AGENT" in all_text, "Missing SEC_USER_AGENT reference"

    def test_nvidia_disabled_by_default(self, notebook_data: dict) -> None:
        sources = _all_cell_sources(notebook_data)
        all_text = " ".join(sources)
        assert "ENABLE_NVIDIA" in all_text, "Missing ENABLE_NVIDIA toggle"
        # Default should be False
        assert "ENABLE_NVIDIA = False" in all_text, "NVIDIA not disabled by default"

    def test_remove_credential_url_after_clone(self, notebook_data: dict) -> None:
        sources = _all_cell_sources(notebook_data)
        all_text = " ".join(sources)
        assert "set-url" in all_text, "Missing git remote set-url (credential removal)"

    def test_install_package_not_duplicate_code(self, notebook_data: dict) -> None:
        code = _code_cells(notebook_data)
        install_cells = [c for c in code if "pip install" in c]
        assert len(install_cells) >= 1, "No pip install step"
        # Should use fenrix_synthetic package, not reimplement collectors
        all_code = " ".join(code)
        assert "from fenrix_synthetic" in all_code, "Not using fenrix_synthetic package"

    def test_sec_archive_support(self, notebook_data: dict) -> None:
        sources = _all_cell_sources(notebook_data)
        all_text = " ".join(sources)
        assert "SEC_ARCHIVE_PATH" in all_text or "DRIVE_SEC_ARCHIVE_PATH" in all_text, (
            "Missing SEC archive path configuration"
        )

    def test_never_print_secrets(self, notebook_data: dict) -> None:
        """Verify secret values are never printed."""
        code = _code_cells(notebook_data)
        for cell in code:
            # No cell should print a secret variable value
            assert "print(GITHUB_TOKEN)" not in cell, "GITHUB_TOKEN printed!"
            assert "print(SEC_USER_AGENT)" not in cell, "SEC_USER_AGENT printed!"
            assert "print(NVIDIA_API_KEY)" not in cell, "NVIDIA_API_KEY printed!"

    def test_resumable_structure(self, notebook_data: dict) -> None:
        """Verify resumability section exists."""
        sources = _all_cell_sources(notebook_data)
        all_text = " ".join(sources)
        assert "Resumability" in all_text, "Missing resumability section"
        assert "RESUME_RUN_ID" in all_text or "resume" in all_text.lower(), (
            "Missing resume mechanism"
        )

    def test_cpu_default(self, notebook_data: dict) -> None:
        """Notebook uses CPU by default (no GPU runtime requirement)."""
        sources = _all_cell_sources(notebook_data)
        all_text = " ".join(sources)
        # Should not require GPU
        assert "GPU" not in all_text.upper() or "CPU" in all_text, (
            "Notebook may require GPU runtime"
        )
