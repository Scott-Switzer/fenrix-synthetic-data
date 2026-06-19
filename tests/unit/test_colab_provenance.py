"""Tests for colab wrapper provenance verification.

BLOCKER 1 — DIRTY-WORKTREE PROVENANCE

These tests verify:
- Clean working tree detection
- Modified tracked file detection
- Allowed external output files
- Commit matching and mismatch
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the colab wrapper script using importlib.util
_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "colab_phase3c_smoke.py"
_spec = importlib.util.spec_from_file_location("colab_phase3c_smoke", _SCRIPT_PATH)
_colab = importlib.util.module_from_spec(_spec)
sys.modules["colab_phase3c_smoke"] = _colab
_spec.loader.exec_module(_colab)


class TestIsWorkingTreeClean:
    """Test _is_working_tree_clean for various git status outputs."""

    def test_clean_empty_tree(self, tmp_path: Path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            clean, details = _colab._is_working_tree_clean(tmp_path)
            assert clean is True
            assert details == ""

    def test_modified_tracked_file(self, tmp_path: Path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=" M src/fenrix_synthetic/cli.py\n", returncode=0
            )
            clean, details = _colab._is_working_tree_clean(tmp_path)
            assert clean is False
            assert "src/fenrix_synthetic/cli.py" in details

    def test_added_tracked_file(self, tmp_path: Path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="A  src/fenrix_synthetic/new_file.py\n", returncode=0
            )
            clean, details = _colab._is_working_tree_clean(tmp_path)
            assert clean is False
            assert "src/fenrix_synthetic/new_file.py" in details

    def test_deleted_tracked_file(self, tmp_path: Path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=" D src/fenrix_synthetic/deleted.py\n", returncode=0
            )
            clean, details = _colab._is_working_tree_clean(tmp_path)
            assert clean is False
            assert "src/fenrix_synthetic/deleted.py" in details

    def test_untracked_file_allowed(self, tmp_path: Path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="?? other/file.txt\n", returncode=0)
            clean, details = _colab._is_working_tree_clean(tmp_path)
            assert clean is True
            assert details == ""

    def test_untracked_file_in_permitted_dir(self, tmp_path: Path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="?? evidence/phase3c_evidence.json\n", returncode=0
            )
            clean, details = _colab._is_working_tree_clean(
                tmp_path, permitted_output_dir=tmp_path / "evidence"
            )
            assert clean is True
            assert details == ""

    def test_multiple_dirty_lines(self, tmp_path: Path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=" M src/fenrix_synthetic/cli.py\n M src/fenrix_synthetic/cli.py\n",
                returncode=0,
            )
            clean, details = _colab._is_working_tree_clean(tmp_path)
            assert clean is False
            assert "src/fenrix_synthetic/cli.py" in details


class TestVerifyCommit:
    """Test _verify_commit for matching and mismatching commits."""

    def _setup_git_mocks(self, commit: str, status: str) -> tuple[MagicMock, callable]:
        """Return a configured mock_run for git commands."""

        def side_effect(cmd, **kwargs):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(stdout=f"{commit}\n", returncode=0)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return MagicMock(stdout=status, returncode=0)
            return MagicMock(stdout="", returncode=0)

        mock_run = MagicMock()
        mock_run.side_effect = side_effect
        return mock_run, side_effect

    def test_matching_clean_commit(self, tmp_path: Path):
        mock_run, _ = self._setup_git_mocks("abc123def456", "")
        with patch("subprocess.run", mock_run):
            result = _colab._verify_commit(tmp_path, "abc123def456")
            assert result["checked_out_commit"] == "abc123def456"
            assert result["expected_commit"] == "abc123def456"
            assert result["commit_verified"] is True
            assert result["working_tree_clean"] is True

    def test_matching_clean_commit_without_expected(self, tmp_path: Path):
        mock_run, _ = self._setup_git_mocks("abc123def456", "")
        with patch("subprocess.run", mock_run):
            result = _colab._verify_commit(tmp_path, None)
            assert result["checked_out_commit"] == "abc123def456"
            assert result["expected_commit"] is None
            assert result["commit_verified"] is True
            assert result["working_tree_clean"] is True

    def test_mismatched_commit(self, tmp_path: Path):
        mock_run, _ = self._setup_git_mocks("abc123def456", "")
        with patch("subprocess.run", mock_run):
            with pytest.raises(_colab.Phase3CFailure) as exc_info:
                _colab._verify_commit(tmp_path, "wrong_commit")
            assert exc_info.value.step == "verify_commit"
            assert exc_info.value.returncode == 1

    def test_dirty_tree_fails(self, tmp_path: Path):
        mock_run, _ = self._setup_git_mocks("abc123def456", " M src/fenrix_synthetic/cli.py\n")
        with patch("subprocess.run", mock_run):
            with pytest.raises(_colab.Phase3CFailure) as exc_info:
                _colab._verify_commit(tmp_path, "abc123def456")
            assert exc_info.value.step == "verify_commit"
            assert exc_info.value.returncode == 1

    def test_permitted_untracked_files_not_dirty(self, tmp_path: Path):
        mock_run, _ = self._setup_git_mocks("abc123def456", "?? evidence/phase3c_evidence.json\n")
        with patch("subprocess.run", mock_run):
            result = _colab._verify_commit(
                tmp_path, "abc123def456", permitted_output_dir=tmp_path / "evidence"
            )
            assert result["checked_out_commit"] == "abc123def456"
            assert result["commit_verified"] is True
            assert result["working_tree_clean"] is True

    def test_modified_tracked_file_with_permitted_dir(self, tmp_path: Path):
        mock_run, _ = self._setup_git_mocks(
            "abc123def456", " M src/fenrix_synthetic/cli.py\n?? evidence/phase3c_evidence.json\n"
        )
        with patch("subprocess.run", mock_run):
            with pytest.raises(_colab.Phase3CFailure) as exc_info:
                _colab._verify_commit(
                    tmp_path, "abc123def456", permitted_output_dir=tmp_path / "evidence"
                )
            assert exc_info.value.step == "verify_commit"
            assert "tracked modifications" in exc_info.value.stderr_tail
