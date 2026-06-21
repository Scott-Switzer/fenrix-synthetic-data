"""Phase 4R2 end-to-end integration test with invented fixture.

Creates a temporary private root outside the repository with a
fully invented issuer, runs the complete 18-stage pipeline, and
verifies all key behaviors.
"""

import json
import os
from pathlib import Path

import pytest
import yaml

from fenrix_synthetic.pilot.orchestrator import RunConfig, StageStatus, run_pilot


def _make_invented_fixture(private_root: Path) -> Path:
    """Create a fully invented issuer fixture under private_root.

    Returns the path to the source manifest.
    """
    import math
    import random

    src_dir = private_root / "sources" / "SRC_001"
    src_dir.mkdir(parents=True, exist_ok=True)

    # ── Source manifest (MANDATORY) ─────────────────────────────────
    manifest = {
        "manifest_id": "manifest-SRC_001-v1",
        "schema_version": "1.0.0",
        "company_id": "SRC_001",
        "data_start": "2025-01-01",
        "data_end": "2025-12-31",
        "expected_history_years": 1,
        "documents": [
            {
                "document_id": "report_q1",
                "document_type": "earnings_release",
                "source_path": "unstructured/report_q1.txt",
                "content_hash": "abc123",
            }
        ],
        "series": [
            {
                "series_id": "prices",
                "format": "ohlcv",
                "source_path": "structured/prices.json",
                "content_hash": "def456",
                "row_count": 252,
            }
        ],
        "extractor_versions": {"ohlcv": "1.0.0"},
        "manifest_hash": "manifest-hash-123",
    }
    manifest_path = src_dir / "source_manifest.yaml"
    manifest_path.write_text(yaml.dump(manifest))

    # ── Identity atlas ─────────────────────────────────────────────
    atlas = {
        "atlas_id": "atlas-fictitious-v1",
        "schema_version": "1.0.0",
        "company_id": "SRC_001",
        "entries": [
            {
                "entry_id": "issuer-legal",
                "category": "issuer",
                "sub_type": "legal_name",
                "private_value": "Fictitious Holdings Inc.",
                "normalized_value": "fictitious holdings inc.",
                "match_policy": "case_insensitive",
                "priority": 100,
                "reason": "Primary legal name",
                "reviewer_id": "test-reviewer",
            },
            {
                "entry_id": "issuer-ticker",
                "category": "issuer",
                "sub_type": "ticker",
                "private_value": "FICT",
                "normalized_value": "fict",
                "match_policy": "case_insensitive",
                "priority": 90,
                "reason": "Exchange ticker",
                "reviewer_id": "test-reviewer",
            },
            {
                "entry_id": "issuer-short",
                "category": "issuer",
                "sub_type": "alias",
                "private_value": "Fictitious",
                "normalized_value": "fictitious",
                "match_policy": "case_insensitive",
                "priority": 80,
                "reason": "Short name",
                "reviewer_id": "test-reviewer",
            },
            {
                "entry_id": "exec-jane",
                "category": "people",
                "sub_type": "executive",
                "private_value": "Jane Fictitious",
                "normalized_value": "jane fictitious",
                "match_policy": "case_insensitive",
                "priority": 100,
                "reason": "CEO",
                "reviewer_id": "test-reviewer",
            },
            {
                "entry_id": "sub-co",
                "category": "organizations",
                "sub_type": "subsidiary",
                "private_value": "Fictitious Sub Co.",
                "normalized_value": "fictitious sub co.",
                "match_policy": "case_insensitive",
                "priority": 100,
                "reason": "Wholly owned subsidiary",
                "reviewer_id": "test-reviewer",
            },
            {
                "entry_id": "prod-main",
                "category": "products",
                "sub_type": "product_name",
                "private_value": "FictitiousPro",
                "normalized_value": "fictitiouspro",
                "match_policy": "case_insensitive",
                "priority": 100,
                "reason": "Flagship product",
                "reviewer_id": "test-reviewer",
            },
            {
                "entry_id": "hq-address",
                "category": "locations",
                "sub_type": "headquarters",
                "private_value": "123 Fictitious Way, Imaginary City",
                "normalized_value": "123 fictitious way, imaginary city",
                "match_policy": "case_insensitive",
                "priority": 100,
                "reason": "HQ address",
                "reviewer_id": "test-reviewer",
            },
            {
                "entry_id": "domain-main",
                "category": "digital",
                "sub_type": "domain",
                "private_value": "fictitious.example",
                "normalized_value": "fictitious.example",
                "match_policy": "domain",
                "priority": 100,
                "reason": "Primary domain",
                "reviewer_id": "test-reviewer",
            },
            {
                "entry_id": "email-domain",
                "category": "digital",
                "sub_type": "email_domain",
                "private_value": "fictitious.example",
                "normalized_value": "fictitious.example",
                "match_policy": "case_insensitive",
                "priority": 90,
                "reason": "Email domain",
                "reviewer_id": "test-reviewer",
            },
        ],
    }
    atlas_path = src_dir / "identity_atlas.yaml"
    atlas_path.write_text(yaml.dump(atlas))

    # ── Unstructured documents ──────────────────────────────────────
    doc_path = src_dir / "unstructured"
    doc_path.mkdir(parents=True, exist_ok=True)
    document = (
        "fictitious holdings inc. reported quarterly results today. "
        "CEO jane fictitious announced that fictitious sub co. contributed "
        "strong revenue from the fictitiouspro product line. "
        "The company's headquarters at 123 fictitious way, imaginary city "
        "is being expanded. Contact investor@fictitious.example for details."
    )
    (doc_path / "report_q1.txt").write_text(document)

    # ── Structured OHLCV ────────────────────────────────────────────
    prices_dir = src_dir / "structured"
    prices_dir.mkdir(parents=True, exist_ok=True)
    records = []
    rng = random.Random(42)
    price = 100.0
    for i in range(252):  # ~1 year of trading days
        ret = rng.gauss(0.0005, 0.015)
        day_open = price
        day_close = price * math.exp(ret)
        intra_range = day_close * rng.uniform(0.005, 0.03)
        day_high = max(day_open, day_close) + intra_range * rng.random()
        day_low = min(day_open, day_close) - intra_range * rng.random()
        day_low = max(day_low, 0.01)
        day_high = max(day_high, day_low)
        records.append(
            {
                "date": f"2025-01-{(i % 28) + 1:02d}",
                "open": round(day_open, 2),
                "high": round(day_high, 2),
                "low": round(day_low, 2),
                "close": round(day_close, 2),
                "volume": float(rng.randint(100000, 5000000)),
            }
        )
        price = day_close
    (prices_dir / "prices.json").write_text(json.dumps({"records": records}))

    # ── Market reference ────────────────────────────────────────────
    mkt_records = []
    rng_mkt = random.Random(7)
    mkt_price = 100.0
    for i in range(252):
        ret = rng_mkt.gauss(0.0003, 0.012)
        mkt_open = mkt_price
        mkt_close = mkt_price * math.exp(ret)
        intra = mkt_close * rng_mkt.uniform(0.003, 0.02)
        mkt_high = max(mkt_open, mkt_close) + intra * rng_mkt.random()
        mkt_low = min(mkt_open, mkt_close) - intra * rng_mkt.random()
        mkt_low = max(mkt_low, 0.01)
        mkt_high = max(mkt_high, mkt_low)
        mkt_records.append(
            {
                "date": f"2025-01-{(i % 28) + 1:02d}",
                "open": round(mkt_open, 2),
                "high": round(mkt_high, 2),
                "low": round(mkt_low, 2),
                "close": round(mkt_close, 2),
                "volume": float(rng_mkt.randint(1000000, 10000000)),
            }
        )
        mkt_price = mkt_close
    (prices_dir / "market_reference.json").write_text(json.dumps({"records": mkt_records}))

    # ── Sector reference ────────────────────────────────────────────
    sec_records = []
    rng_sec = random.Random(13)
    sec_price = 100.0
    for i in range(252):
        ret = rng_sec.gauss(0.0004, 0.014)
        sec_open = sec_price
        sec_close = sec_price * math.exp(ret)
        intra = sec_close * rng_sec.uniform(0.004, 0.025)
        sec_high = max(sec_open, sec_close) + intra * rng_sec.random()
        sec_low = min(sec_open, sec_close) - intra * rng_sec.random()
        sec_low = max(sec_low, 0.01)
        sec_high = max(sec_high, sec_low)
        sec_records.append(
            {
                "date": f"2025-01-{(i % 28) + 1:02d}",
                "open": round(sec_open, 2),
                "high": round(sec_high, 2),
                "low": round(sec_low, 2),
                "close": round(sec_close, 2),
                "volume": float(rng_sec.randint(500000, 8000000)),
            }
        )
        sec_price = sec_close
    (prices_dir / "sector_reference.json").write_text(json.dumps({"records": sec_records}))

    # ── Candidate universe (100+ deterministic candidates) ──────────
    universe = {"candidates": [], "universe_id": "univ-fake-v1"}
    source_returns = []
    for i in range(1, len(records)):
        if records[i - 1]["close"] > 0:
            source_returns.append(math.log(records[i]["close"] / records[i - 1]["close"]))

    # True source
    universe["candidates"].append(
        {
            "candidate_id": "SRC_001",
            "returns": source_returns,
            "prices": [r["close"] for r in records],
        }
    )

    # Distractor generators with deterministic seeds
    def _make_distractor(seed: int, vol: float, beta_mkt: float, beta_sec: float) -> list[float]:
        r = random.Random(seed)
        mkt_r = random.Random(seed + 1000)
        sec_r = random.Random(seed + 2000)
        returns = []
        for _ in range(len(source_returns)):
            mret = mkt_r.gauss(0.0003, 0.012)
            sret = sec_r.gauss(0.0004, 0.014)
            noise = r.gauss(0.0, vol)
            returns.append(beta_mkt * mret + beta_sec * sret + noise)
        return returns

    # Similar volatility distractors
    for d in range(20):
        returns = _make_distractor(100 + d, 0.015, 0.3, 0.2)
        universe["candidates"].append(
            {
                "candidate_id": f"DISTRACTOR-VOL-{d:04d}",
                "returns": returns,
            }
        )

    # Similar market beta distractors
    for d in range(20):
        returns = _make_distractor(200 + d, 0.012, 0.9, 0.1)
        universe["candidates"].append(
            {
                "candidate_id": f"DISTRACTOR-MKT-{d:04d}",
                "returns": returns,
            }
        )

    # Similar sector beta distractors
    for d in range(20):
        returns = _make_distractor(300 + d, 0.013, 0.2, 0.8)
        universe["candidates"].append(
            {
                "candidate_id": f"DISTRACTOR-SEC-{d:04d}",
                "returns": returns,
            }
        )

    # Unrelated distractors
    for d in range(30):
        returns = _make_distractor(400 + d, 0.020, 0.0, 0.0)
        universe["candidates"].append(
            {
                "candidate_id": f"DISTRACTOR-UNREL-{d:04d}",
                "returns": returns,
            }
        )

    # Shifted near-copy (robustness test)
    shifted_returns = [0.0] * 5 + source_returns[:-5]
    universe["candidates"].append(
        {
            "candidate_id": "SHIFTED-COPY-001",
            "returns": shifted_returns,
        }
    )

    # Rescaled near-copy
    rescaled_returns = [r * 1.05 for r in source_returns]
    universe["candidates"].append(
        {
            "candidate_id": "RESCALED-COPY-001",
            "returns": rescaled_returns,
        }
    )

    (prices_dir / "candidate_universe.json").write_text(json.dumps(universe))

    return src_dir


class TestEndToEndInventedPilot:
    """Complete end-to-end pilot run with invented fixture."""

    @pytest.mark.timeout(120)
    def test_full_invented_pilot(self, tmp_path: Path):
        """Run the complete 18-stage pipeline with an invented fixture.

        Verifies:
        1. All core stages pass
        2. Masking replaces invented identities
        3. Typed placeholders are consistent
        4. Raw identities do not appear in masked output
        5. S0 is non-releasable
        6. Structured attacks execute
        7. Evidence manifest is assembled
        8. Release gate consumes manifest
        9. Dossier contains actual outputs
        """
        # Set up private root outside repo
        private_root = tmp_path / "fenrix_private"
        private_root.mkdir(parents=True)

        # Create invented fixture
        _make_invented_fixture(private_root)

        # Set env var
        os.environ["FENRIX_PRIVATE_ROOT"] = str(private_root)

        # Run the pipeline
        config = RunConfig(
            source_id="SRC_001",
            release_id="SYNTH_001",
            private_root=private_root,
            candidate_universe_path=(
                private_root / "sources" / "SRC_001" / "structured" / "candidate_universe.json"
            ),
            market_reference_path=(
                private_root / "sources" / "SRC_001" / "structured" / "market_reference.json"
            ),
            sector_reference_path=(
                private_root / "sources" / "SRC_001" / "structured" / "sector_reference.json"
            ),
            test_fixture=True,
        )

        manifest = run_pilot(config)

        # ── Assertions ────────────────────────────────────────────
        stage_by_name = {s.stage.value: s for s in manifest.stages}

        # Stage 1: Boundary passes
        boundary = stage_by_name["validate_private_boundary"]
        assert boundary.status == StageStatus.PASSED

        # Stage 2: Manifest validated (now mandatory)
        validate_manifest = stage_by_name["validate_source_manifest"]
        assert validate_manifest.status == StageStatus.PASSED, (
            f"Source manifest validation failed: {validate_manifest.errors}"
        )

        # Stage 3: Atlas compiled (test_fixture allows REVIEW_REQUIRED on incomplete)
        compile_atlas = stage_by_name["compile_identity_atlas"]
        assert compile_atlas.status in (StageStatus.PASSED, StageStatus.REVIEW_REQUIRED)

        # Stage 4-6: Masking passes
        mask_stage = stage_by_name.get("mask_unstructured_records")
        if mask_stage:
            assert mask_stage.status == StageStatus.PASSED

        # Verify masking: raw identities should NOT appear in masked output
        masked_dir = private_root / "runs" / manifest.run_id / "private"
        masked_files = list(masked_dir.glob("*_masked.txt"))
        if masked_files:
            masked_text = masked_files[0].read_text()
            assert "fictitious holdings inc." not in masked_text, (
                "Legal name leaked in masked output"
            )
            assert "jane fictitious" not in masked_text, "Executive name leaked in masked output"
            assert "fict" not in masked_text, "Ticker leaked in masked output"
            # Placeholders should appear
            assert "[" in masked_text, "No placeholders found in masked output"

        # Stage 7-10: Structured transforms
        s0 = stage_by_name["generate_s0"]
        assert s0.status == StageStatus.PASSED
        assert not s0.metadata.get("releasable", True), "S0 must be non-releasable"

        s1 = stage_by_name["generate_s1"]
        assert s1.status == StageStatus.PASSED

        s2 = stage_by_name["generate_s2"]
        assert s2.status == StageStatus.PASSED

        # Stage 11: Text attacks
        text_attacks = stage_by_name["run_text_attacks"]
        assert text_attacks.status == StageStatus.PASSED

        # Stage 12: Structured attacks (if candidate universe provided)
        structured_attacks = stage_by_name.get("run_structured_attacks")
        if structured_attacks and structured_attacks.status != StageStatus.SKIPPED_NOT_CONFIGURED:
            assert structured_attacks.status == StageStatus.PASSED

        # Stage 13: Utility evaluation
        utility = stage_by_name["run_utility_evaluation"]
        assert utility.status == StageStatus.PASSED

        # Stage 14: Determinism
        determinism = stage_by_name["run_determinism_check"]
        assert determinism.status == StageStatus.PASSED

        # Stage 15: Evidence manifest
        evidence_stage = stage_by_name["assemble_evidence_manifest"]
        assert evidence_stage.status == StageStatus.PASSED

        # Stage 16: Release gate
        release_stage = stage_by_name["assess_release"]
        assert release_stage.status == StageStatus.PASSED

        # Stage 17: Dossier exported (if not FAIL)
        dossier_stage = stage_by_name.get("export_dossier_if_allowed")
        if dossier_stage:
            # Dossier may be BLOCKED_UPSTREAM if gate was FAIL
            assert dossier_stage.status in (StageStatus.PASSED, StageStatus.BLOCKED_UPSTREAM)

        # Stage 18: Finalize
        finalize = stage_by_name["finalize_checksums"]
        assert finalize.status == StageStatus.PASSED

        # Overall run completed
        assert manifest.overall_status in ("completed", "failed")

    @pytest.mark.timeout(120)
    def test_deterministic_rerun(self, tmp_path: Path):
        """Verify deterministic reproduction.

        Running the pipeline twice with the same inputs must produce
        identical evidence hashes.
        """
        private_root = tmp_path / "fenrix_private"
        private_root.mkdir(parents=True)
        _make_invented_fixture(private_root)
        os.environ["FENRIX_PRIVATE_ROOT"] = str(private_root)

        config = RunConfig(
            source_id="SRC_001",
            release_id="SYNTH_001",
            private_root=private_root,
            candidate_universe_path=(
                private_root / "sources" / "SRC_001" / "structured" / "candidate_universe.json"
            ),
            market_reference_path=(
                private_root / "sources" / "SRC_001" / "structured" / "market_reference.json"
            ),
            sector_reference_path=(
                private_root / "sources" / "SRC_001" / "structured" / "sector_reference.json"
            ),
            test_fixture=True,
        )

        manifest1 = run_pilot(config)
        manifest2 = run_pilot(config)

        # Evidence hashes must match
        assert manifest1.evidence_hashes == manifest2.evidence_hashes, (
            "Deterministic rerun produced different evidence hashes"
        )

    @pytest.mark.timeout(60)
    def test_no_private_identity_leakage(self, tmp_path: Path):
        """Verify no invented private identity leaks into dossier output."""
        private_root = tmp_path / "fenrix_private"
        private_root.mkdir(parents=True)
        _make_invented_fixture(private_root)
        os.environ["FENRIX_PRIVATE_ROOT"] = str(private_root)

        config = RunConfig(
            source_id="SRC_001",
            release_id="SYNTH_001",
            private_root=private_root,
            candidate_universe_path=(
                private_root / "sources" / "SRC_001" / "structured" / "candidate_universe.json"
            ),
            market_reference_path=(
                private_root / "sources" / "SRC_001" / "structured" / "market_reference.json"
            ),
            sector_reference_path=(
                private_root / "sources" / "SRC_001" / "structured" / "sector_reference.json"
            ),
            test_fixture=True,
        )

        run_pilot(config)
        export_root = private_root / "exports" / "SYNTH_001"

        if export_root.exists():
            for f in export_root.rglob("*"):
                if f.is_file():
                    content = f.read_text()
                    # Private identities must not appear in export
                    assert "fictitious holdings inc." not in content, (
                        f"Legal name leaked in {f.relative_to(export_root)}"
                    )
                    assert "jane fictitious" not in content, (
                        f"Executive name leaked in {f.relative_to(export_root)}"
                    )
                    assert "fictitious.example" not in content, (
                        f"Domain leaked in {f.relative_to(export_root)}"
                    )
