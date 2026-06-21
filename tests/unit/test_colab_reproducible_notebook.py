"""Focused tests for the reproducible Colab notebook."""

from __future__ import annotations

import json
import re
from pathlib import Path

NOTEBOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / "notebooks"
    / "FENRIX_MultiCompany_Anonymization_Reproducible.ipynb"
)


def _code_text() -> str:
    nb = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    parts: list[str] = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            parts.append("".join(cell.get("source", [])))
    return "\n".join(parts)


def test_reproducible_notebook_exists() -> None:
    assert NOTEBOOK_PATH.is_file(), f"Notebook not found at {NOTEBOOK_PATH}"


def test_reproducible_notebook_has_no_local_paths_or_secrets() -> None:
    code = _code_text()
    # No local macOS/user paths in executable code.
    assert "/Users/scott" not in code
    # No hardcoded API key values (an actual nvapi- secret, not the scan regex).
    assert not re.search(r'= "nvapi-[A-Za-z0-9_-]+"', code)
    # No direct assignment of the env var to a literal secret.
    assert 'NVIDIA_API_KEY", "nvapi-' not in code


def test_reproducible_notebook_contains_required_markers() -> None:
    text = NOTEBOOK_PATH.read_text(encoding="utf-8")
    assert "SEC_USER_AGENT" in text
    assert "anonymized_bundle.zip" in text
    assert "build_submission_fast.py" in text
    assert "CL,PEP,TJX,PM,AMZN,HBAN,BLK,GOOGL" in text.replace(" ", "")


def test_reproducible_notebook_contains_validation_scans() -> None:
    text = NOTEBOOK_PATH.read_text(encoding="utf-8")
    assert "SEC_COVER_PATTERN" in text or "IRS Employer" in text
    assert "BROAD_PATTERN" in text or "us-gaap" in text
    assert "scan" in text.lower()


def test_reproducible_notebook_states_limitations() -> None:
    text = NOTEBOOK_PATH.read_text(encoding="utf-8").lower()
    assert "no mathematical anonymity" in text or "mathematical anonymity guarantee" in text
    assert "incomplete" in text
    assert "stated limitations" in text
