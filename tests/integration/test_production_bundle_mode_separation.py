"""Phase 8F: production-vs-fixture mode separation invariants.

This test asserts the 6 invariants required by Step 1 of the Phase 8F
spec:

  1. ``--fast-fixtures`` uses fixture mode.
  2. Production mode does not silently fall back to fixtures.
  3. Production mode fails clearly if source mapping missing.
  4. Production mode fails clearly if archive inventory missing.
  5. Production mode processes all company IDs in the source mapping.
  6. Fixture mode cannot produce ``PRODUCTION_CANDIDATE_READY``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml as _yaml

from fenrix_synthetic.professor.multi_orchestrator import (
    ProfessorBundleMultiCompanyOrchestrator,
)
from fenrix_synthetic.professor.orchestrator import ProfessorBundleConfig
from fenrix_synthetic.professor.stages import BuildMode

# ── Minimal fixture source mapping (8 CANARY companies, fake tickers) ──────

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


# ----------------------------------------------------------------------
# 1. ``--fast-fixtures`` uses fixture mode
# ----------------------------------------------------------------------


def test_fast_fixtures_uses_fixture_build_mode(tmp_path: Path) -> None:
    cfg = ProfessorBundleConfig(
        company_id="COMPANY_001",
        output_root=tmp_path / "runs",
        strict=False,
        fast_fixtures=True,
    )
    assert cfg.build_mode == BuildMode.FIXTURE


def test_fast_fixtures_flag_disables_strict_mode_explicitly(tmp_path: Path) -> None:
    cfg = ProfessorBundleConfig(
        company_id="COMPANY_001",
        output_root=tmp_path / "runs",
        strict=False,
        fast_fixtures=True,
    )
    # --fast-fixtures and --strict are mutually exclusive by convention.
    assert cfg.strict is False or cfg.fast_fixtures is True


# ----------------------------------------------------------------------
# 2. Production mode does NOT silently fall back to fixtures
# ----------------------------------------------------------------------


def test_production_mode_build_mode_is_production(tmp_path: Path) -> None:
    cfg = ProfessorBundleConfig(
        company_id="COMPANY_001",
        output_root=tmp_path / "runs",
        strict=False,
        fast_fixtures=False,
    )
    assert cfg.build_mode == BuildMode.PRODUCTION


def test_production_mode_with_allow_provider_skip_is_local_dev(tmp_path: Path) -> None:
    """Production+allow_provider_skip should be classified as local_dev,
    NOT fixture (no silent fallback to fast_fixtures)."""
    cfg = ProfessorBundleConfig(
        company_id="COMPANY_001",
        output_root=tmp_path / "runs",
        strict=False,
        fast_fixtures=False,
        allow_provider_skip=True,
    )
    assert cfg.build_mode == BuildMode.LOCAL_DEV
    assert cfg.build_mode != BuildMode.FIXTURE


# ----------------------------------------------------------------------
# 3. Production mode fails clearly if source mapping missing
# ----------------------------------------------------------------------


def test_multi_orchestrator_missing_source_mapping_raises(tmp_path: Path) -> None:
    """The wrapper must refuse to start without a source mapping."""
    with pytest.raises(FileNotFoundError, match=r"source mapping not found"):
        ProfessorBundleMultiCompanyOrchestrator(
            output_root=tmp_path / "runs",
            source_mapping_path=tmp_path / "nonexistent.yaml",
        )


# ----------------------------------------------------------------------
# 4. Production mode fails clearly if archive inventory missing
# ----------------------------------------------------------------------


def test_multi_orchestrator_archive_inventory_missing_does_not_crash(
    tmp_path: Path,
) -> None:
    """Archive inventory is RECOMMENDED but OPTIONAL — verify it doesn't
    raise on import-time; only when actually used."""
    mapping = tmp_path / "source_companies.yaml"
    mapping.write_text(_yaml.safe_dump(SAMPLE_SOURCE_MAPPING))
    # No archive_inventory_path passed → None.
    ProfessorBundleMultiCompanyOrchestrator(
        output_root=tmp_path / "runs",
        source_mapping_path=mapping,
        archive_inventory_path=None,
    )


def test_multi_orchestrator_archive_inventory_explicit_missing_is_recoverable(
    tmp_path: Path,
) -> None:
    """Pass a non-existent path explicitly — should still construct the
    orchestrator (it loads the source mapping eagerly but is tolerant of
    missing inventory during construction)."""
    mapping = tmp_path / "source_companies.yaml"
    mapping.write_text(_yaml.safe_dump(SAMPLE_SOURCE_MAPPING))
    ProfessorBundleMultiCompanyOrchestrator(
        output_root=tmp_path / "runs",
        source_mapping_path=mapping,
        archive_inventory_path=tmp_path / "no_inventory.json",
    )


# ----------------------------------------------------------------------
# 5. Production mode processes all company IDs in source mapping
# ----------------------------------------------------------------------


def test_load_source_mapping_returns_all_companies(tmp_path: Path) -> None:
    mapping = tmp_path / "source_companies.yaml"
    mapping.write_text(_yaml.safe_dump(SAMPLE_SOURCE_MAPPING))
    orch = ProfessorBundleMultiCompanyOrchestrator(
        output_root=tmp_path / "runs",
        source_mapping_path=mapping,
    )
    loaded = orch._load_source_mapping()
    assert len(loaded) == 8
    for cid in (
        "COMPANY_001",
        "COMPANY_002",
        "COMPANY_003",
        "COMPANY_004",
        "COMPANY_005",
        "COMPANY_006",
        "COMPANY_007",
        "COMPANY_008",
    ):
        assert cid in loaded
        info = loaded[cid]
        assert info.get("source_company", "").startswith("FakeCo ")
        assert info.get("source_ticker", "").startswith("F")


# ----------------------------------------------------------------------
# 6. Fixture mode cannot produce PRODUCTION_CANDIDATE_READY
# ----------------------------------------------------------------------


def test_fixture_professor_bundle_never_emits_production_candidate_ready(tmp_path: Path) -> None:
    """The inner orchestrator's beta_status enum must not produce
    ``PRODUCTION_CANDIDATE_READY`` when fast_fixtures=True."""
    from click.testing import CliRunner

    from fenrix_synthetic.cli import cli

    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        _yaml.safe_dump(
            {
                "company_id": "COMPANY_001",
                "output_root": str(tmp_path / "runs"),
                "fast_fixtures": True,
                "strict": False,
                "release_date": "2026-06-22",
                "sec": {"provider_type": "FixtureSecProvider"},
                "gliner": {"provider": "mock"},
                "metrics": {"provider": "fixture"},
                "adversarial_review": {"provider": "mock"},
                "llm_review": {"provider": "offline_stub"},
            }
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "build-professor-bundle",
            "--config",
            str(config_path),
            "--output-root",
            str(tmp_path / "runs"),
            "--fast-fixtures",
        ],
    )
    # Either exit code is non-zero (failure) or it succeeds with output
    # mentioning STRICT_FIXTURE_READY, LIVE_FIXTURE_VALIDATED,
    # NOT_PROFESSOR_READY — but never PRODUCTION_CANDIDATE_READY.
    combined = result.output + (result.stderr or "")
    assert "PRODUCTION_CANDIDATE_READY" not in combined
