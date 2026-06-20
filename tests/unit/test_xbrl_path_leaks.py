"""Regression tests for XBRL and path leaks found in NVDA run.

Covers: CIKs in XBRL text/attributes, ticker in directories,
accession-based filenames, namespace/schema leaks, manifest leaks,
executive/location leaks.
"""

from __future__ import annotations

import re
from pathlib import Path

from fenrix_synthetic.masking.deterministic import (
    build_cik_padded_pattern,
    build_cik_url_pattern,
)
from fenrix_synthetic.pipeline.manifests import ManifestBuilder
from fenrix_synthetic.release.pseudonym_paths import (
    build_pseudonym_path_map,
    build_xbrl_cik_patterns,
)

# ── SYNTHETIC TEST DATA ────────────────────────────────────────────

SYNTHETIC_TICKER = "SYNTH"
SYNTHETIC_CIK = "0001234567"
SYNTHETIC_CIK_CLEAN = "1234567"
SYNTHETIC_ACCESSION = "0001234567-24-000001"


class TestPseudonymPathMap:
    """Deterministic public aliases for paths and identifiers."""

    def test_path_map_deterministic(self) -> None:
        """Same input produces same output every time."""
        pm1 = build_pseudonym_path_map(SYNTHETIC_TICKER, SYNTHETIC_CIK, [])
        pm2 = build_pseudonym_path_map(SYNTHETIC_TICKER, SYNTHETIC_CIK, [])
        assert pm1.company_pseudonym == pm2.company_pseudonym
        assert pm1.ticker_pseudonym == pm2.ticker_pseudonym
        assert pm1.cik_pseudonym == pm2.cik_pseudonym

    def test_path_map_no_ticker_in_output(self) -> None:
        """Pseudonyms must never contain the input ticker."""
        pm = build_pseudonym_path_map(SYNTHETIC_TICKER, SYNTHETIC_CIK, [])
        assert SYNTHETIC_TICKER not in pm.company_pseudonym
        assert SYNTHETIC_TICKER not in pm.company_pseudonym.upper()
        assert SYNTHETIC_TICKER.upper() not in pm.company_pseudonym.upper()

    def test_accession_pseudonyms(self) -> None:
        """Accession numbers get deterministic pseudonyms."""
        accessions = [SYNTHETIC_ACCESSION, "0001234567-24-000002"]
        pm = build_pseudonym_path_map(SYNTHETIC_TICKER, SYNTHETIC_CIK, accessions)
        assert len(pm.accession_pseudonyms) >= 2
        clean = SYNTHETIC_ACCESSION.replace("-", "")
        assert clean in pm.accession_pseudonyms
        assert pm.accession_pseudonyms[clean] == pm.accession_pseudonyms[SYNTHETIC_ACCESSION]

    def test_public_filename(self) -> None:
        """Public filenames use pseudonyms, not accessions."""
        pm = build_pseudonym_path_map(SYNTHETIC_TICKER, SYNTHETIC_CIK, [SYNTHETIC_ACCESSION])
        fname = pm.public_filename(SYNTHETIC_ACCESSION)
        assert SYNTHETIC_CIK not in fname
        assert SYNTHETIC_ACCESSION not in fname
        assert fname.endswith(".md")

    def test_public_path_rewrite(self) -> None:
        """Public paths replace accession filenames with pseudonyms."""
        pm = build_pseudonym_path_map(SYNTHETIC_TICKER, SYNTHETIC_CIK, [SYNTHETIC_ACCESSION])
        clean_acc = SYNTHETIC_ACCESSION.replace("-", "")
        private_path = f"SYNTHETIC/sec/{clean_acc}.md"
        public = pm.public_path(private_path)
        # Accession-based filename should be replaced
        assert clean_acc not in public
        assert ".md" in public

    def test_public_artifact_id(self) -> None:
        """Artifact IDs use company pseudonym, not ticker."""
        pm = build_pseudonym_path_map(SYNTHETIC_TICKER, SYNTHETIC_CIK, [])
        aid = pm.public_artifact_id("features_s3a", 0, SYNTHETIC_ACCESSION)
        assert SYNTHETIC_TICKER not in aid


class TestXBRLPatterns:
    """CIKs in XBRL text and attributes."""

    def test_build_xbrl_patterns(self) -> None:
        """XBRL pattern generation produces usable regex patterns."""
        patterns = build_xbrl_cik_patterns(SYNTHETIC_CIK)
        assert len(patterns) >= 4
        for pat, _repl in patterns:
            re.compile(pat)  # verify valid regex

    def test_entity_central_index_key_match(self) -> None:
        """EntityCentralIndexKey attribute should be matched and CIK replaced."""
        patterns = build_xbrl_cik_patterns(SYNTHETIC_CIK)
        found = False
        for pat, repl in patterns:
            if "EntityCentralIndexKey" in pat:
                found = True
                text = (
                    '<context><entity><identifier scheme="http://www.sec.gov/CIK">'
                    f'EntityCentralIndexKey="{SYNTHETIC_CIK_CLEAN}"'
                    "</identifier></entity></context>"
                )
                result = re.sub(pat, repl, text)
                assert "EntityCentralIndexKey" in result, f"Pattern: {pat}"
                assert SYNTHETIC_CIK_CLEAN not in result, f"CIK still in result: {result}"
                break
        assert found, "No EntityCentralIndexKey pattern found"

    def test_cik_in_url_masked(self) -> None:
        """CIK in URL cik= parameter should be masked."""
        text = f"http://example.com/cik={SYNTHETIC_CIK_CLEAN}/data"
        pat = build_cik_url_pattern(SYNTHETIC_CIK)
        result = re.sub(pat, "CIK_MASKED", text)
        assert SYNTHETIC_CIK_CLEAN not in result

    def test_padded_cik_masked(self) -> None:
        """Padded CIK (10-char) should be matched."""
        pat = build_cik_padded_pattern(SYNTHETIC_CIK)
        text = f"CIK #{SYNTHETIC_CIK}"
        result = re.sub(pat, "MASKED", text, flags=re.IGNORECASE)
        assert SYNTHETIC_CIK not in result

    def test_bare_cik_in_text_masked(self) -> None:
        """Bare CIK number should be matched."""
        pat = build_cik_padded_pattern(SYNTHETIC_CIK)
        text = f"identifier {SYNTHETIC_CIK}"
        result = re.sub(pat, "MASKED", text, flags=re.IGNORECASE)
        assert SYNTHETIC_CIK not in result

    def test_partial_cik_not_masked(self) -> None:
        """Partial CIK should NOT match exact pattern."""
        pat = build_cik_padded_pattern(SYNTHETIC_CIK)
        text = "CIK #123456"
        result = re.sub(pat, "MASKED", text, flags=re.IGNORECASE)
        assert "123456" in result


class TestManifestSanitization:
    """Manifests contain no ticker, CIK, accession, or machine paths."""

    def test_manifest_pseudonym_artifact_id(self) -> None:
        """ManifestBuilder should sanitize artifact_id with company pseudonym."""
        mb = ManifestBuilder(
            "test_run", SYNTHETIC_TICKER, Path("/tmp"), company_pseudonym="COMP_abc123"
        )
        mf = mb.build_manifest(
            artifact_id=f"{SYNTHETIC_TICKER}_yf_ohlcv",
            source="yfinance",
            source_url=None,
            requested_range=(None, None),
            observed_range=(None, None),
            content_type="parquet",
            relative_path=f"{SYNTHETIC_TICKER}/metrics/ohlcv.parquet",
            byte_size=100,
            sha256="abc123",
            collection_status="success",
        )
        assert SYNTHETIC_TICKER not in mf["artifact_id"]
        assert SYNTHETIC_TICKER not in mf["company_id"]
        assert SYNTHETIC_TICKER not in mf["relative_output_path"]

    def test_run_manifest_no_ticker(self) -> None:
        """Run manifest uses company_pseudonym, not ticker."""
        mb = ManifestBuilder(
            "test_run", SYNTHETIC_TICKER, Path("/tmp"), company_pseudonym="COMP_abc123"
        )
        rm = mb.build_run_manifest([], [], [])
        assert "company_pseudonym" in rm
        assert "ticker" not in rm
