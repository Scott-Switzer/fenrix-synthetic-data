"""Tests for strict V3 release gate.

Validates that the gate fails closed on privacy violations
and passes on clean bundles.
"""

from __future__ import annotations

from pathlib import Path

from fenrix_synthetic.qa.release_gate import evaluate_strict_release_gate


def _make_dir_structure(base: Path, files: dict[str, str]) -> None:
    """Create a directory structure from a dict of {relpath: content}."""
    for rel, content in files.items():
        fp = base / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")


class TestStrictReleaseGateBlocks:
    """Test that the gate blocks known leak scenarios."""

    def test_fails_on_private_identity_map_in_public(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/identity_map.json": '{"private_to_public": {}}',
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"], (
            f"Should block identity map in public: {result['fail_reasons']}"
        )
        assert any("private_data_in_allowlisted_area" in f for f in result["fail_reasons"])

    def test_fails_on_raw_sec_html(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/anonymized/C001/sec/filing.html": "<html><body>SEC Filing</body></html>",
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"]
        assert any("forbidden_extension_in_public" in f for f in result["fail_reasons"])

    def test_fails_on_raw_xbrl(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/anonymized/C001/sec/filing.xbrl": "<xbrl>data</xbrl>",
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"]
        assert any("forbidden_extension_in_public" in f for f in result["fail_reasons"])

    def test_fails_on_forbidden_zip_entry(self, tmp_path: Path) -> None:
        import zipfile

        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
            },
        )
        exports = tmp_path / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        zip_path = exports / "anonymized_bundle.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("private/secret.txt", "secret")
            zf.writestr("public/README.md", "# Bundle")
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"]
        assert any("zip_contains_forbidden_path" in f for f in result["fail_reasons"])

    def test_fails_on_forbidden_extension_in_zip(self, tmp_path: Path) -> None:
        import zipfile

        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
            },
        )
        exports = tmp_path / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        zip_path = exports / "anonymized_bundle.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("public/data.xml", "<xml/>")
            zf.writestr("public/README.md", "# Bundle")
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"]
        assert any("zip_contains_forbidden_extension" in f for f in result["fail_reasons"])

    def test_fails_on_direct_identifier_in_public(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle\n\nCIK: 0000999999\nCommission File Number: 001-09999\n",
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"]
        assert any("direct_identifier_scan" in f for f in result["fail_reasons"])

    def test_fails_on_metadata_in_public(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle\n\n<ix:hidden>secret</ix:hidden>\n",
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"]
        assert any("metadata_scan" in f for f in result["fail_reasons"])

    def test_fails_on_private_evidence_in_public(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/evidence/evidence_graph.json": "{}",
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"]
        assert any("private_data_in_allowlisted_area" in f for f in result["fail_reasons"])

    def test_fails_on_raw_dir_in_public(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/raw/filing.html": "<html>",
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"]
        assert any("forbidden_path_in_allowlisted_area" in f for f in result["fail_reasons"])

    def test_fails_on_env_file_in_public(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
                "public/.env": "SECRET=value",
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        assert not result["passed"]
        assert any("forbidden_file_in_allowlisted_area" in f for f in result["fail_reasons"])


class TestStrictReleaseGatePasses:
    """Test that the gate passes on clean bundles."""

    def test_passes_clean_minimal_public_bundle(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "RELEASE_MANIFEST.json": '{"release_id": "test"}',
                "public/README.md": "# Professor Bundle",
                "public/CLASSROOM_GUIDE.md": "# Classroom Guide",
                "public/anonymized/C001/sec/item_7.md": "# Item 7\n\nClean text.\n",
                "public/anonymized/C001/metrics/returns.json": '{"returns": [0.01, -0.02]}',
                "qa/stage_registry.json": '{"build_mode": "fixture"}',
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        assert result["passed"], f"Should pass: {result['fail_reasons']}"

    def test_passes_with_company_specific_scans(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "RELEASE_MANIFEST.json": '{"release_id": "test"}',
                "public/README.md": "# Bundle",
                "public/anonymized/C001/sec/item_7.md": "# Item 7\n\nClean analysis.\n",
            },
        )
        result = evaluate_strict_release_gate(
            tmp_path,
            company_names=["Test Corp"],
            tickers=["TST"],
        )
        assert result["passed"], f"Should pass: {result['fail_reasons']}"


class TestReleaseGateOutput:
    """Test gate output structure."""

    def test_gate_output_has_expected_keys(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
            },
        )
        result = evaluate_strict_release_gate(tmp_path)
        required = [
            "passed",
            "mode",
            "checked_at",
            "scanned_files",
            "scanned_bytes",
            "direct_identifier_hits",
            "metadata_hits",
            "forbidden_paths",
            "missing_required_files",
            "manifest_status",
            "fail_reasons",
            "gate_hash",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_gate_hash_is_deterministic(self, tmp_path: Path) -> None:
        _make_dir_structure(
            tmp_path,
            {
                "public/README.md": "# Bundle",
            },
        )
        r1 = evaluate_strict_release_gate(tmp_path)
        r2 = evaluate_strict_release_gate(tmp_path)
        assert r1["gate_hash"] == r2["gate_hash"]
