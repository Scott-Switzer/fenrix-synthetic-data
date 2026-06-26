"""Unit tests for V3.1 artifact quality gate.

Covers all failure scenarios and one clean-pass fixture.
Uses temporary directories with synthetic data only — no real company content.
"""

from __future__ import annotations

import json
from pathlib import Path

from fenrix_synthetic.qa.artifact_quality_gate import (
    NOT_PROFESSOR_READY,
    PROFESSOR_READY_V3_1,
    evaluate_artifact_quality_gate,
    write_quality_gate_report,
)

# ── Helpers ────────────────────────────────────────────────────────


def _make_bundle_root(tmp_path: Path, **overrides: object) -> Path:
    """Create a minimal clean bundle structure under tmp_path.

    Override keys:
        n_companies: int (default 8)
        archetype_keys: list[str] | None (default 8 distinct)
        fin_years: int (default 10)
        market_rows: int (default 1200)
        sec_identical: bool (default False)
        qa_contaminated: bool (default False)
        broken_doc_refs: bool (default False)
        include_stage_registry: bool (default False)
    """
    n = int(overrides.get("n_companies", 8))
    archetype_keys = overrides.get("archetype_keys")
    fin_years = int(overrides.get("fin_years", 10))
    market_rows = int(overrides.get("market_rows", 1200))
    sec_identical = bool(overrides.get("sec_identical", False))
    qa_contaminated = bool(overrides.get("qa_contaminated", False))
    broken_doc_refs = bool(overrides.get("broken_doc_refs", False))
    include_stage_registry = bool(overrides.get("include_stage_registry", False))

    public_dir = tmp_path / "public" / "anonymized"
    qa_dir = tmp_path / "qa"
    public_dir.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)

    # Top-level docs
    (tmp_path / "README.md").write_text(
        "RELEASE_MANIFEST.md\nRUN_SUMMARY.md\nDATA_DICTIONARY.md\nchecksums.sha256\n"
    )
    (tmp_path / "QUICKSTART.md").write_text(
        "See RUN_SUMMARY.md and DATA_DICTIONARY.md\n"
    )
    # Always create these since the gate checks for them
    (tmp_path / "RELEASE_MANIFEST.json").write_text('{"release_id": "test"}\n')
    (tmp_path / "RELEASE_MANIFEST.md").write_text("Release manifest placeholder\n")
    (tmp_path / "DATA_DICTIONARY.md").write_text("Data dictionary placeholder\n")
    # Conditionally omit RUN_SUMMARY.md to test broken refs
    if not broken_doc_refs:
        (tmp_path / "RUN_SUMMARY.md").write_text("Run summary placeholder\n")
        (tmp_path / "checksums.sha256").write_text("abc123  README.md\n")
    # If broken_doc_refs, RUN_SUMMARY.md and checksums.sha256 are missing
    # but README.md still references them — gate should detect this.

    # If no archetype keys at all, skip archetype card creation entirely
    if archetype_keys is None:
        archetype_keys = [
            "global_consumer_staples",
            "diversified_beverage_snack",
            "off_price_apparel_retail",
            "international_nicotine_products",
            "digital_commerce_cloud_platform",
            "regional_banking_institution",
            "global_asset_management",
            "digital_advertising_cloud_services",
        ]
    skip_archetypes = (len(archetype_keys) == 0)

    # If fewer archetypes than companies, cycle
    if len(archetype_keys) == 0:
        archetype_keys = []  # keep empty — archetype cards won't be created
    elif len(archetype_keys) < n:
        archetype_keys = (archetype_keys * ((n // len(archetype_keys)) + 1))[:n]

    for i in range(n):
        cid = f"COMPANY_{i + 1:03d}"
        cdir = public_dir / cid

        # Profile — skip if archetype_keys is empty (testing missing cards scenario)
        (cdir / "profile").mkdir(parents=True, exist_ok=True)
        if not skip_archetypes:
            ak = archetype_keys[i] if i < len(archetype_keys) else archetype_keys[-1]
            (cdir / "profile" / "archetype_card.json").write_text(
                json.dumps({
                    "archetype_key": ak,
                    "archetype_label": ak.replace("_", " ").title(),
                })
            )

        # Financials
        (cdir / "financials").mkdir(parents=True, exist_ok=True)
        csv_lines = ["year,metric_name,transformed_value,family"]
        for y in range(2020, 2020 + fin_years):
            csv_lines.append(f"{y},Revenue,{100 + i * 10},income_statement")
        (cdir / "financials" / "transformed_metrics.csv").write_text(
            "\n".join(csv_lines) + "\n"
        )

        # Market
        (cdir / "market").mkdir(parents=True, exist_ok=True)
        price_lines = ["relative_day,price,volume_indicator"]
        for d in range(market_rows):
            price_lines.append(f"DAY_{d:04d},{100.0 + (d % 50) * 0.1},{d % 5 + 1}")
        (cdir / "market" / "price_series.csv").write_text(
            "\n".join(price_lines) + "\n"
        )

        # SEC
        (cdir / "sec").mkdir(parents=True, exist_ok=True)
        if sec_identical:
            (cdir / "sec" / "annual_report_business.md").write_text(
                "Generic stub content — identical across all companies.\n"
            )
        else:
            (cdir / "sec" / "annual_report_business.md").write_text(
                f"Business description for {cid} with distinct content seed {i}.\n"
            )
        (cdir / "sec" / "filing_coverage.md").write_text(
            f"Filing coverage for {cid}\n"
        )

    # QA
    if qa_contaminated:
        (qa_dir / "some_report.json").write_text(
            '{"status": "LOCAL_DEV_NOT_READY", "professor_ready": false}\n'
        )
    if include_stage_registry:
        (qa_dir / "stage_registry_COMPANY_001.json").write_text('{"stage": "test"}\n')

    return tmp_path


# ── Clean fixture test ─────────────────────────────────────────────


class TestCleanBundlePasses:
    """A clean 8-company bundle with distinct archetypes, 10yr financials,
    1000+ market rows, clean QA, and valid docs should PASS."""

    def test_clean_bundle_passes(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is True, result.checks
        assert result.verdict == PROFESSOR_READY_V3_1
        assert result.company_count == 8
        assert result.distinct_archetypes == 8
        assert result.min_financial_years >= 7
        assert result.market_series_min_rows >= 1000
        assert result.public_qa_clean is True


# ── Failure: too few companies ─────────────────────────────────────


class TestFewerThanEightCompanies:
    def test_seven_companies_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, n_companies=7)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False
        assert result.verdict == NOT_PROFESSOR_READY
        assert result.company_count == 7

    def test_zero_companies_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, n_companies=0)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False


# ── Failure: non-distinct archetypes ───────────────────────────────


class TestNonDistinctArchetypes:
    def test_all_same_archetype_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, archetype_keys=["same_archetype"] * 8)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False
        assert result.distinct_archetypes == 1

    def test_only_four_distinct_of_eight_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(
            tmp_path,
            archetype_keys=["a", "b", "c", "d", "a", "b", "c", "d"],
        )
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False
        assert result.distinct_archetypes == 4

    def test_seven_distinct_of_eight_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(
            tmp_path,
            archetype_keys=["a", "b", "c", "d", "e", "f", "g", "a"],
        )
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False
        assert result.distinct_archetypes == 7


# ── Failure: too few financial years ───────────────────────────────


class TestInsufficientFinancialYears:
    def test_five_years_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, fin_years=5)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False
        assert result.min_financial_years == 5

    def test_six_years_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, fin_years=6)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False

    def test_seven_years_passes(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, fin_years=7)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is True  # other gates also pass


# ── Failure: SEC stub content ──────────────────────────────────────


class TestSecStubContent:
    def test_identical_sec_stubs_warns_but_does_not_block(self, tmp_path: Path) -> None:
        """SEC stub detection is non-blocking — it warns but doesn't fail the gate."""
        root = _make_bundle_root(tmp_path, sec_identical=True)
        result = evaluate_artifact_quality_gate(root)
        # Non-blocking: gate should still pass on other criteria
        assert result.sec_content_archive_backed is False
        assert result.sec_content_honestly_labeled is True
        # Check that warnings contain the SEC stub warning
        assert any("sec_content" in w.lower() for w in result.warnings)

    def test_distinct_sec_content_shows_archive_backed(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, sec_identical=False)
        result = evaluate_artifact_quality_gate(root)
        assert result.sec_content_archive_backed is True


# ── Failure: QA contamination ─────────────────────────────────────


class TestQaContamination:
    def test_local_dev_not_ready_flags_fail(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, qa_contaminated=True)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False
        assert result.public_qa_clean is False

    def test_clean_qa_passes(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, qa_contaminated=False)
        result = evaluate_artifact_quality_gate(root)
        assert result.public_qa_clean is True


# ── Failure: broken doc references ─────────────────────────────────


class TestBrokenDocReferences:
    def test_broken_refs_fail(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, broken_doc_refs=True)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False
        check = [c for c in result.checks if c.check_id == "docs_have_no_broken_refs"][0]
        assert check.passed is False

    def test_valid_doc_refs_pass(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, broken_doc_refs=False)
        result = evaluate_artifact_quality_gate(root)
        check = [c for c in result.checks if c.check_id == "docs_have_no_broken_refs"][0]
        assert check.passed is True


# ── Failure: short market series ───────────────────────────────────


class TestShortMarketSeries:
    def test_500_rows_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, market_rows=500)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False
        assert result.market_series_min_rows == 500

    def test_999_rows_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, market_rows=999)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is False

    def test_1000_rows_passes(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, market_rows=1000)
        result = evaluate_artifact_quality_gate(root)
        assert result.passed is True


# ── Failure: stage registry in QA ──────────────────────────────────


class TestStageRegistryExcluded:
    def test_stage_registry_in_qa_fails(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, include_stage_registry=True)
        result = evaluate_artifact_quality_gate(root)
        check = [c for c in result.checks if c.check_id == "stage_registry_excluded"][0]
        assert check.passed is False

    def test_no_stage_registry_passes(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path, include_stage_registry=False)
        result = evaluate_artifact_quality_gate(root)
        check = [c for c in result.checks if c.check_id == "stage_registry_excluded"][0]
        assert check.passed is True


# ── write_quality_gate_report ──────────────────────────────────────


class TestWriteQualityGateReport:
    def test_report_is_written(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path)
        result = evaluate_artifact_quality_gate(root)
        qa_dir = tmp_path / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        written = write_quality_gate_report(result, qa_dir)
        assert written.exists()
        assert written.name == "artifact_quality_gate.json"
        loaded = json.loads(written.read_text(encoding="utf-8"))
        assert loaded["verdict"] == PROFESSOR_READY_V3_1
        assert loaded["passed"] is True


# ── Edge cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_bundle_dir_fails(self, tmp_path: Path) -> None:
        """An empty directory with no companies should fail."""
        (tmp_path / "public" / "anonymized").mkdir(parents=True, exist_ok=True)
        (tmp_path / "qa").mkdir(parents=True, exist_ok=True)
        result = evaluate_artifact_quality_gate(tmp_path)
        assert result.passed is False
        assert result.company_count == 0
        assert result.verdict == NOT_PROFESSOR_READY

    def test_missing_archetype_cards_still_evaluates(self, tmp_path: Path) -> None:
        """Missing archetype cards should not crash — archetypes set is empty."""
        root = _make_bundle_root(tmp_path, archetype_keys=[])
        result = evaluate_artifact_quality_gate(root)
        assert result.distinct_archetypes == 0
        assert result.passed is False

    def test_all_eight_checks_present(self, tmp_path: Path) -> None:
        root = _make_bundle_root(tmp_path)
        result = evaluate_artifact_quality_gate(root)
        assert len(result.checks) == 8
        check_ids = {c.check_id for c in result.checks}
        assert check_ids == {
            "company_count",
            "distinct_archetypes",
            "min_financial_years",
            "sec_content_archive_backed",
            "public_qa_no_local_dev_flags",
            "docs_have_no_broken_refs",
            "market_series_min_rows",
            "stage_registry_excluded",
        }

    def test_professor_ready_v3_1_constant_is_string(self) -> None:
        assert isinstance(PROFESSOR_READY_V3_1, str)
        assert PROFESSOR_READY_V3_1 == "PROFESSOR_READY_V3_1"

    def test_not_professor_ready_constant_is_string(self) -> None:
        assert isinstance(NOT_PROFESSOR_READY, str)
        assert NOT_PROFESSOR_READY == "NOT_PROFESSOR_READY"
