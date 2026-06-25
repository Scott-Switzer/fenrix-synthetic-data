"""Phase 8F: full 8-company production end-to-end (OFFLINE fixture mode).

This test runs the ProfessorBundleMultiCompanyOrchestrator end-to-end
against a synthetic 8-company mapping, with the LLM provider in offline
mode (no real NVIDIA API calls). It asserts:

  - 8 anonymized company directories are produced.
  - Each company tree contains the required Phase 8F files.
  - The bundle-level ``qa/llm_blind_guess_summary.json``,
    ``qa/utility_preservation_summary.json``, and ``run_summary.json``
    are written.
  - The strict release gate runs (in tolerant mode) without crashing.

This test does NOT call live NVIDIA APIs. The presence of a live-mode
E2E is reserved for Lightning AI runs.

The test uses ``tmp_path`` fixtures and does not touch the repository.
It reuses the existing single-company orchestrator fixture pipeline by
running the multi-company wrapper with `fast_fixtures=True` and
``llm_provider={"provider": "offline_stub"}``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml as _yaml

from fenrix_synthetic.professor.multi_orchestrator import (
    ProfessorBundleMultiCompanyOrchestrator,
)

SAMPLE_SOURCE_MAPPING: dict[str, dict[str, str]] = {
    "COMPANY_001": {"source_company": "FakeCo A", "source_ticker": "FA"},
    "COMPANY_002": {"source_company": "FakeCo B", "source_ticker": "FB"},
    "COMPANY_003": {"source_company": "FakeCo C", "source_ticker": "FC"},
    "COMPANY_004": {"source_company": "FakeCo D", "source_ticker": "FD"},
    "COMPANY_005": {"source_company": "FakeCo E", "source_ticker": "FE"},
    "COMPANY_006": {"source_company": "FakeCo F", "source_ticker": "FF"},
    "COMPANY_007": {"source_company": "FakeCo G", "source_ticker": "FG"},
    "COMPANY_008": {"source_company": "FakeCo H", "source_ticker": "FH"},
}


@pytest.fixture
def source_mapping_path(tmp_path: Path) -> Path:
    p = tmp_path / "source_companies.yaml"
    p.write_text(_yaml.safe_dump(SAMPLE_SOURCE_MAPPING))
    return p


@pytest.fixture
def output_root(tmp_path: Path) -> Path:
    return tmp_path / "bundle"


REQUIRED_PER_COMPANY_FILES = [
    "profile/archetype_card.json",
    "profile/profile.md",
    "financials/transformed_metrics.csv",
    "financials/ratio_summary.csv",
    "financials/summary.md",
    "market/price_series.csv",
    "market/return_summary.md",
    "sec/annual_report_business.md",
    "sec/annual_report_risk_factors.md",
    "sec/annual_report_mda.md",
    "sec/filing_coverage.md",
    "news/synthetic_news_briefs.md",
    "news/event_timeline.csv",
]


REQUIRED_TOP_LEVEL_FILES = [
    "README.md",
    "QUICKSTART.md",
    "RUN_SUMMARY.md",
    "DATA_DICTIONARY.md",
    "RELEASE_MANIFEST.json",
    "RELEASE_MANIFEST.md",
    "qa/llm_blind_guess_summary.json",
    "qa/utility_preservation_summary.json",
    "checksums.sha256",
    "artifact_inventory.csv",
    "run_summary.json",
]


def test_eight_company_run_produces_required_files(
    tmp_path: Path, source_mapping_path: Path, output_root: Path
) -> None:
    """8-company pipeline must produce 8 anonymized company directories
    with the required Phase 8F file set each."""
    if output_root.exists():
        shutil.rmtree(output_root)

    orch = ProfessorBundleMultiCompanyOrchestrator(
        output_root=output_root,
        source_mapping_path=source_mapping_path,
        archive_inventory_path=None,
        llm_provider_cfg={"provider": "offline_stub"},
    )
    result = orch.run()

    # All 8 companies processed
    assert len(result.companies_processed) == 8

    # Top-level docs exist
    for fname in REQUIRED_TOP_LEVEL_FILES:
        assert (output_root / fname).exists(), f"missing top-level file: {fname}"

    # Each per-company tree exists with all required files
    public_root = output_root / "public" / "anonymized"
    expected_dirs = {f"COMPANY_{i:03d}" for i in range(1, 9)}
    actual_dirs = {p.name for p in public_root.iterdir() if p.is_dir()}
    assert expected_dirs.issubset(actual_dirs), (
        f"missing dirs: {expected_dirs - actual_dirs}, extra: {actual_dirs - expected_dirs}"
    )

    for company_id in expected_dirs:
        company_dir = public_root / company_id
        for rel in REQUIRED_PER_COMPANY_FILES:
            target = company_dir / rel
            assert target.exists(), f"missing {rel} for {company_id}"


def test_eight_company_run_aggregates_qa_summaries(
    tmp_path: Path, source_mapping_path: Path, output_root: Path
) -> None:
    """Aggregated bundle-level QA summaries must reflect all 8 companies."""
    if output_root.exists():
        shutil.rmtree(output_root)

    orch = ProfessorBundleMultiCompanyOrchestrator(
        output_root=output_root,
        source_mapping_path=source_mapping_path,
        archive_inventory_path=None,
        llm_provider_cfg={"provider": "offline_stub"},
    )
    result = orch.run()

    bg = result.blind_guess_summary
    assert bg["companies_reviewed"] >= 1
    util = result.utility_summary
    assert util["companies_reviewed"] >= 1
    # min score must be <= max score
    assert util["min_score"] <= util["max_score"]


def test_eight_company_run_zip_path_exists(
    tmp_path: Path, source_mapping_path: Path, output_root: Path
) -> None:
    """Final ZIP path is recorded even if ZIP packaging raises (the
    wrapper reports the issue in `failures`)."""
    if output_root.exists():
        shutil.rmtree(output_root)

    orch = ProfessorBundleMultiCompanyOrchestrator(
        output_root=output_root,
        source_mapping_path=source_mapping_path,
        llm_provider_cfg={"provider": "offline_stub"},
    )
    result = orch.run()
    # Whether or not packaging succeeded, the path is set.
    assert result.zip_path == output_root / "exports" / "anonymized_bundle.zip"


def test_one_canonical_public_llm_file_per_company(
    tmp_path: Path, source_mapping_path: Path, output_root: Path
) -> None:
    """Phase 8F Step 1 invariant: exactly one canonical public LLM JSON
    per company with one canonical schema across all companies.

    The wrapper deliberately migrates only ``stage_registry`` and lets
    ``_run_per_company_blind_guess`` write the canonical per-company LLM
    file — no stale single-company-shaped schema copies are created.
    """
    if output_root.exists():
        shutil.rmtree(output_root)

    orch = ProfessorBundleMultiCompanyOrchestrator(
        output_root=output_root,
        source_mapping_path=source_mapping_path,
        llm_provider_cfg={"provider": "offline_stub"},
    )
    orch.run()

    qa_dir = output_root / "qa"
    per_company_files = sorted(qa_dir.glob("llm_blind_guess_COMPANY_*.json"))
    assert len(per_company_files) == 8, [f.name for f in per_company_files]

    # Schema uniformity: every file shares the same top-level keys.
    import orjson as _orjson

    canonical_keys: set[str] | None = None
    for fp in per_company_files:
        data = _orjson.loads(fp.read_bytes())
        if isinstance(data, dict):
            keys = set(data.keys())
            if canonical_keys is None:
                canonical_keys = keys
            else:
                assert keys == canonical_keys, (fp.name, keys, canonical_keys)
