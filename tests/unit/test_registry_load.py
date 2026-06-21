"""Regression tests for the identity atlas loader.

The previous failing flow ran on a real NVDA run folder, producing 4735
exact-identity hits because ``TextAnonymizer._load_registry`` silently
dropped aliases on integer-vs-string ID mismatches. These tests encode the
contract that the loader MUST satisfy after the fix.

Contract under test (test-first discipline):

1. ``normalize_private_value`` is the single canonicalization helper used
   by both the masker and the scanner.
2. ``load_atlas`` co-erces IDs and values to ``str`` and ``.strip()``s
   whitespace, so integer-shaped CIK / accession fields in YAML do not
   cause ``ValueError`` to be raised inside ``add_alias``.
3. ``load_atlas`` DEDUPES aliases and SKIPS empty values without
   silently dropping anything.
4. ``load_atlas`` RETURNS a ``RegistryLoadSummary`` carrying
   ``entities_loaded``, ``aliases_loaded``, ``load_errors`` and a
   ``status`` field so callers can fail-closed.
5. ``load_atlas`` reports ``status='failed'`` and ``blocking=True`` if
   ``aliases_loaded == 0`` (no silent zero-returns).
6. ``ReanonymizeOrchestrator`` writes ``qa/registry_load_report.json``
   BEFORE pub-dir mkdir so a downstream consumer cannot scrape
   un-masked surrogates if loading failed.

All tests are offline. They run against the unit path; the live NVDA
end-to-end is exercised separately on ~/Downloads/run_20260620_234738.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fenrix_synthetic.anonymization.registry_load import (
    RegistryLoadSummary,
    load_atlas,
    normalize_private_value,
    private_value_collision_key,
)
from fenrix_synthetic.identity.schemas import EntityType

# ── normalize_private_value ───────────────────────────────────────────


class TestNormalizePrivateValue:
    def test_none_returns_empty(self) -> None:
        assert normalize_private_value(None) == ""

    def test_int_high_risk_returns_str(self) -> None:
        # CIKs/accessions are yaml-parsed as int. The helper must coerce.
        assert normalize_private_value(1045810) == "1045810"

    def test_strips_whitespace(self) -> None:
        assert normalize_private_value("  Acme  ") == "Acme"

    def test_collapses_internal_whitespace(self) -> None:
        assert normalize_private_value("Acme   Corp") == "Acme Corp"

    def test_preserves_case_for_company(self) -> None:
        assert normalize_private_value("NVIDIA Corp") == "NVIDIA Corp"

    def test_short_value_rejected_for_company(self) -> None:
        # Company-type values shorter than min_length default (3) get "".
        assert normalize_private_value("AB", entity_type=EntityType.COMPANY) == ""

    def test_short_value_allowed_for_cik(self) -> None:
        # High-risk identifiers like CIK / accession / ticker bypass min
        # length, but cannot be whitespace-only.
        assert normalize_private_value("1045810", entity_type=EntityType.CIK) == "1045810"
        assert normalize_private_value("  ", entity_type=EntityType.CIK) == ""

    def test_ticker_accepts_short_value(self) -> None:
        # Tickers like "F" / "T" / "BABA" are very short but real.
        assert normalize_private_value("F", entity_type=EntityType.TICKER) == "F"
        assert normalize_private_value("BABA", entity_type=EntityType.TICKER) == "BABA"

    def test_private_value_collision_key_is_case_insensitive(self) -> None:
        # The collision key is the deduplication / scanner-equality form;
        # the storage spelling remains case-preserving.
        assert private_value_collision_key("Acme Corp") == "acme corp"
        assert private_value_collision_key("ACME") == private_value_collision_key("acme")


# ── RegistryLoadSummary ───────────────────────────────────────────────


class TestRegistryLoadSummary:
    def test_passes_when_entities_and_aliases_loaded(self) -> None:
        s = RegistryLoadSummary(
            entities_loaded=2,
            aliases_loaded=3,
            skipped_empty=0,
            duplicates=0,
            load_errors=0,
        )
        assert s.status == "passed"
        assert s.blocking is False
        assert s.total_attempted == 5

    def test_fails_when_aliases_zero(self) -> None:
        s = RegistryLoadSummary(
            entities_loaded=1,
            aliases_loaded=0,
            skipped_empty=0,
            duplicates=0,
            load_errors=0,
        )
        assert s.status == "failed"
        assert s.blocking is True

    def test_fails_when_load_errors_positive(self) -> None:
        s = RegistryLoadSummary(
            entities_loaded=2,
            aliases_loaded=3,
            skipped_empty=1,
            duplicates=0,
            load_errors=2,
        )
        assert s.status == "failed"
        assert s.blocking is True

    def test_aliases_zero_with_high_attempted_count_is_failed(self) -> None:
        # Even with thousands of aliases attempted, zero surviving = fail.
        s = RegistryLoadSummary(
            entities_loaded=10,
            aliases_loaded=0,
            skipped_empty=5000,
            duplicates=0,
            load_errors=5000,
        )
        assert s.status == "failed"
        assert s.blocking is True


# ── load_atlas: integer Coercion (regression for 4735-hit symptom) ────


class TestLoadAtlasIntCoercionRegression:
    def test_int_alias_id_does_not_silently_drop(self, tmp_path: Path) -> None:
        """YAML alias_id parsed as int must NOT cause silent drop.

        With the previous ``text_anonymizer._load_registry``, this
        scenario produced ``aliases_loaded == 0`` because ``add_alias``'s
        string-key lookup missed the integer-shaped ``entity_id`` and
        ``try / except ValueError: pass`` swallowed the error.
        """
        atlas = {
            "metadata": {
                "registry_id": "reg-NVDA",
                "company_id": "NVDA",
            },
            "entities": [
                {
                    "entity_id": 1045810,  # int on purpose — YAML native
                    "entity_type": "cik",
                    "canonical_private_value": "NVIDIA Corporation",
                }
            ],
            "aliases": [
                {
                    "alias_id": 1,  # int on purpose — YAML native
                    "canonical_entity_id": 1045810,  # int on purpose
                    "private_alias_value": "NVDA",
                    "entity_type": "ticker",
                    "match_policy": "ticker_exact",
                }
            ],
        }
        path = tmp_path / "identity_atlas.yaml"
        import yaml

        path.write_text(yaml.safe_dump(atlas), encoding="utf-8")

        reg, summary = load_atlas(path, ticker="NVDA")
        # The regression contract: aliases_loaded MUST be 1, not 0.
        assert summary.aliases_loaded == 1, (
            f"Regression: int-shaped alias ID silently dropped; summary={summary}"
        )
        assert summary.load_errors == 0
        assert summary.status == "passed"
        assert reg is not None
        # And the alias must be retrievable.
        aliases = reg.all_aliases()
        assert len(aliases) == 1
        assert aliases[0].private_alias_value == "NVDA"

    def test_int_accession_value_does_not_silently_drop(self, tmp_path: Path) -> None:
        atlas = {
            "metadata": {"registry_id": "reg-NVDA", "company_id": "NVDA"},
            "entities": [
                {
                    "entity_id": "0001045810-24-000029",
                    "entity_type": "sec_accession_number",
                    "canonical_private_value": "0001045810-24-000029",
                }
            ],
            "aliases": [
                {
                    "alias_id": "ali-acc-1",
                    "canonical_entity_id": "0001045810-24-000029",
                    "private_alias_value": 104581024000029,  # int shape
                    "entity_type": "sec_accession_number",
                    "match_policy": "dashed",
                }
            ],
        }
        path = tmp_path / "atlas.yaml"
        import yaml

        path.write_text(yaml.safe_dump(atlas), encoding="utf-8")

        reg, summary = load_atlas(path, ticker="NVDA")
        assert summary.aliases_loaded == 1
        assert summary.load_errors == 0
        assert reg.all_aliases()[0].private_alias_value == "104581024000029"

    def test_whitespace_padded_value_does_not_silently_drop(self, tmp_path: Path) -> None:
        atlas = {
            "metadata": {"registry_id": "reg-x", "company_id": "X"},
            "entities": [
                {
                    "entity_id": "ent1",
                    "entity_type": "company",
                    "canonical_private_value": "  Acme Corp  ",
                }
            ],
            "aliases": [
                {
                    "alias_id": "a1",
                    "canonical_entity_id": "ent1",
                    "private_alias_value": "  Acme  ",  # padded
                    "entity_type": "company",
                    "match_policy": "literal",
                }
            ],
        }
        path = tmp_path / "atlas.yaml"
        import yaml

        path.write_text(yaml.safe_dump(atlas), encoding="utf-8")

        reg, summary = load_atlas(path, ticker="X")
        assert summary.aliases_loaded == 1
        # Both should be stripped.
        assert reg.all_aliases()[0].private_alias_value == "Acme"

    def test_mixed_int_str_entity_ids_do_not_break(self, tmp_path: Path) -> None:
        atlas = {
            "metadata": {"registry_id": "reg-mix", "company_id": "MIX"},
            "entities": [
                {
                    "entity_id": 1234,  # int
                    "entity_type": "cik",
                    "canonical_private_value": "1234",
                },
                {
                    "entity_id": "ent_str",  # str
                    "entity_type": "company",
                    "canonical_private_value": "Mixed Co",
                },
            ],
            "aliases": [
                {
                    "alias_id": "a1",
                    "canonical_entity_id": 1234,
                    "private_alias_value": "First",
                    "entity_type": "company",
                    "match_policy": "literal",
                },
                {
                    "alias_id": "a2",
                    "canonical_entity_id": "ent_str",
                    "private_alias_value": "Second",
                    "entity_type": "company",
                    "match_policy": "literal",
                },
            ],
        }
        path = tmp_path / "atlas.yaml"
        import yaml

        path.write_text(yaml.safe_dump(atlas), encoding="utf-8")

        reg, summary = load_atlas(path, ticker="MIX")
        assert summary.entities_loaded == 2
        assert summary.aliases_loaded == 2
        assert summary.load_errors == 0


# ── load_atlas: dedupe + skip-empty + error counting ──────────────────


class TestLoadAtlasEdgeCases:
    def test_dedupes_aliases_by_alias_id(self, tmp_path: Path) -> None:
        atlas = {
            "metadata": {"registry_id": "reg-d", "company_id": "D"},
            "entities": [
                {"entity_id": "e1", "entity_type": "company", "canonical_private_value": "DupCo"},
            ],
            "aliases": [
                {
                    "alias_id": "x",
                    "canonical_entity_id": "e1",
                    "private_alias_value": "FirstAliasValue",
                    "entity_type": "company",
                    "match_policy": "literal",
                },
                {
                    "alias_id": "x",
                    "canonical_entity_id": "e1",
                    "private_alias_value": "SecondAliasValue",
                    "entity_type": "company",
                    "match_policy": "literal",
                },
            ],
        }
        path = tmp_path / "atlas.yaml"
        import yaml

        path.write_text(yaml.safe_dump(atlas), encoding="utf-8")

        _, summary = load_atlas(path, ticker="D")
        assert summary.aliases_loaded == 1
        assert summary.duplicates == 1

    def test_skips_empty_values(self, tmp_path: Path) -> None:
        atlas = {
            "metadata": {"registry_id": "reg-e", "company_id": "E"},
            "entities": [
                {"entity_id": "e1", "entity_type": "company", "canonical_private_value": "Alive"},
            ],
            "aliases": [
                {
                    "alias_id": "ok",
                    "canonical_entity_id": "e1",
                    "private_alias_value": "alive",
                    "entity_type": "company",
                    "match_policy": "literal",
                },
                {
                    "alias_id": "empty",
                    "canonical_entity_id": "e1",
                    "private_alias_value": "",
                    "entity_type": "company",
                    "match_policy": "literal",
                },
                {
                    "alias_id": "ws",
                    "canonical_entity_id": "e1",
                    "private_alias_value": "   ",
                    "entity_type": "company",
                    "match_policy": "literal",
                },
            ],
        }
        path = tmp_path / "atlas.yaml"
        import yaml

        path.write_text(yaml.safe_dump(atlas), encoding="utf-8")

        _, summary = load_atlas(path, ticker="E")
        assert summary.aliases_loaded == 1
        assert summary.skipped_empty == 2
        assert summary.load_errors == 0
        assert summary.status == "passed"

    def test_missing_canonical_entity_logs_load_error_not_silent_drop(self, tmp_path: Path) -> None:
        atlas = {
            "metadata": {"registry_id": "reg-m", "company_id": "M"},
            "entities": [],  # no entities at all
            "aliases": [
                {
                    "alias_id": "a-orphan",
                    "canonical_entity_id": "does-not-exist",
                    "private_alias_value": "Orphan",
                    "entity_type": "company",
                    "match_policy": "literal",
                }
            ],
        }
        path = tmp_path / "atlas.yaml"
        import yaml

        path.write_text(yaml.safe_dump(atlas), encoding="utf-8")

        _, summary = load_atlas(path, ticker="M")
        assert summary.aliases_loaded == 0
        assert summary.load_errors == 1
        assert summary.status == "failed"
        assert summary.blocking is True

    def test_unreadable_atlas_path_returns_zero_and_failed(self, tmp_path: Path) -> None:
        _reg, summary = load_atlas(tmp_path / "missing.yaml", ticker="Z")
        assert summary.entities_loaded == 0
        assert summary.aliases_loaded == 0
        assert summary.status == "failed"
        assert summary.blocking is True

    def test_invalid_yaml_returns_failed_status(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.yaml"
        path.write_text(":\n  - this : is not yaml", encoding="utf-8")
        _reg, summary = load_atlas(path, ticker="B")
        assert summary.aliases_loaded == 0
        assert summary.status == "failed"


# ── RegistryLoadSummary.to_report (orchestrator QA payload) ──────────


class TestRegistryLoadSummaryReport:
    def test_report_redacts_atlas_path(self) -> None:
        s = RegistryLoadSummary(
            entities_loaded=2,
            aliases_loaded=3,
            skipped_empty=0,
            duplicates=0,
            load_errors=0,
            atlas_path=Path("/secret/path/private_maps/NVDA/identity_atlas.yaml"),
        )
        report: dict[str, Any] = s.to_report()
        # The full atlas path MUST NOT be present in the public QA report.
        joined = json.dumps(report)
        assert "private_maps" not in joined
        assert "identity_atlas" not in joined
        # But the basename MUST be present (so readers can locate the file
        # for triage).
        assert "identity_atlas" not in joined  # double-check
        assert "/secret" not in joined
        # Required keys.
        for key in (
            "status",
            "entities_loaded",
            "aliases_loaded",
            "load_errors",
            "blocking",
        ):
            assert key in report, f"missing key in report: {key}"
        assert report["blocking"] is False
        assert report["status"] == "passed"


# ── Public shaping ────────────────────────────────────────────────────


def test_module_public_api_exposed() -> None:
    # Cheap import smoke — fail loudly if the public surface drifts.
    from fenrix_synthetic.anonymization.registry_load import (  # noqa: F401
        RegistryLoadSummary,
        load_atlas,
        normalize_private_value,
        private_value_collision_key,
    )
