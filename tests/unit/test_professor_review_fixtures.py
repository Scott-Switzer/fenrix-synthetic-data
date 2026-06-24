"""Professor review fixture regression tests (Phase 1).

Validate the V2 identification cases fixture and encode the failure
modes that V3 must not regress on.

These tests encode:
- Fixture loads and validates
- All known leak classes are represented
- Professor guesses are attack evidence, not ground truth
- COMPANY_002 is the only exact hit in V2
- V3 release gates must treat exact hits as blocking and peer-category
  guesses as risk warnings

IMPORTANT: This test file reads ALL real company identifiers from the
YAML fixture. No real company names, tickers, or CIKs are hardcoded here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "professor_review"
    / "identification_cases.yaml"
)

# Leak classes confirmed in V2 batches, grouped by whether they are
# addressable by deterministic pattern matching (Tier 0) or require
# transformation/generalization (Tier 1+).
V2_LEAK_CLASSES = frozenset(
    {
        # Tier 0 - addressable by blocking patterns and metadata stripping
        "direct_identifier",
        "sec_commission_file_number",
        "cik",
        "ein",
        "accession_number",
        "xbrl_namespace",
        "original_filename",
        "executive_name",
        "hq_zip",
        "area_code",
        # Tier 1+ - addressable by generalization and perturbation
        "exact_revenue",
        "exact_segment_figure",
        "distinctive_segment_structure",
        "subsidiary_jv",
        "acquisition_history",
        "product_segment_naming",
        "sector_label",
        "fundamentals_bin_fingerprint",
        "synthetic_trajectory_fingerprint",
        "suggestive_fake_name",
        "exact_margin_structure",
        "exact_leverage_profile",
        "litigation",
        "fiscal_year_end",
    }
)


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, Any]:
    """Load the identification cases fixture."""
    assert FIXTURE_PATH.exists(), f"Fixture not found: {FIXTURE_PATH}"
    with open(FIXTURE_PATH) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "Fixture must be a dict"
    return data


@pytest.fixture(scope="module")
def v2_dataset(fixture_data: dict[str, Any]) -> dict[str, Any]:
    """Return the fenrix_alpha_v2 dataset section."""
    return fixture_data["datasets"]["fenrix_alpha_v2"]


@pytest.fixture(scope="module")
def actual_sources(v2_dataset: dict[str, Any]) -> dict[str, Any]:
    """Return the actual_sources mapping from the fixture."""
    return v2_dataset["actual_sources"]


@pytest.fixture(scope="module")
def professor_guesses(v2_dataset: dict[str, Any]) -> dict[str, Any]:
    """Return the professor_guesses mapping from the fixture."""
    return v2_dataset["professor_guesses"]


class TestV2FixtureLoads:
    """The identification cases fixture must load and validate."""

    def test_fixture_has_required_top_level_keys(self, fixture_data: dict[str, Any]) -> None:
        """Required top-level keys must be present."""
        required = [
            "fixture_version",
            "datasets",
            "leak_classes",
            "v3_privacy_targets",
            "remediation",
        ]
        for key in required:
            assert key in fixture_data, f"Missing required key: {key}"

    def test_fixture_has_fenrix_alpha_v2_dataset(self, fixture_data: dict[str, Any]) -> None:
        """The fenrix_alpha_v2 dataset must exist."""
        assert "fenrix_alpha_v2" in fixture_data["datasets"], "fenrix_alpha_v2 dataset missing"

    def test_actual_sources_have_eight_companies(self, actual_sources: dict[str, Any]) -> None:
        """There must be exactly 8 companies in the V2 batch."""
        assert len(actual_sources) == 8, f"Expected 8 companies, got {len(actual_sources)}"

    def test_all_companies_have_ticker_and_name(self, actual_sources: dict[str, Any]) -> None:
        """Every company must have a ticker and company name."""
        for cid, info in actual_sources.items():
            assert info.get("ticker"), f"Missing ticker for {cid}"
            assert info.get("company"), f"Missing company name for {cid}"
            assert info.get("broad_archetype"), f"Missing broad_archetype for {cid}"

    def test_professor_guesses_match_company_count(
        self, actual_sources: dict[str, Any], professor_guesses: dict[str, Any]
    ) -> None:
        """There must be a professor guess for every actual source company."""
        assert len(professor_guesses) == len(actual_sources), (
            f"Expected {len(actual_sources)} guesses, got {len(professor_guesses)}"
        )
        for cid in actual_sources:
            assert cid in professor_guesses, f"Missing guess for: {cid}"

    def test_high_confidence_identifications_zero(self, v2_dataset: dict[str, Any]) -> None:
        """V2 had zero high-confidence identifications."""
        assert v2_dataset["high_confidence_identifications"] == 0

    def test_medium_confidence_identifications_eight(self, v2_dataset: dict[str, Any]) -> None:
        """V2 had eight medium-confidence identifications."""
        assert v2_dataset["medium_confidence_identifications"] == 8

    def test_correct_identifications_one(self, v2_dataset: dict[str, Any]) -> None:
        """V2 had exactly one correct identification."""
        assert v2_dataset["correct_identifications"] == 1

    def test_total_companies_eight(self, v2_dataset: dict[str, Any]) -> None:
        """V2 batch contained 8 companies."""
        assert v2_dataset["total_companies"] == 8


class TestLeakClassesRepresented:
    """Every known V2 leak class must be represented in the fixture."""

    def test_all_leak_classes_defined(self, fixture_data: dict[str, Any]) -> None:
        """The fixture must define all 22 known leak classes."""
        lc = fixture_data["leak_classes"]
        defined = set(lc.keys())
        missing = V2_LEAK_CLASSES - defined
        assert not missing, f"Missing leak classes: {sorted(missing)}"
        extra = defined - V2_LEAK_CLASSES
        assert not extra, f"Unexpected extra leak classes: {sorted(extra)}"

    def test_direct_identifier_is_blocking(self, fixture_data: dict[str, Any]) -> None:
        """Direct identifier leak class must be blocking severity."""
        di = fixture_data["leak_classes"]["direct_identifier"]
        assert di["severity"] == "blocking"

    def test_blocking_leak_classes_have_blocking_severity(
        self, fixture_data: dict[str, Any]
    ) -> None:
        """Known blocking leak classes must have blocking severity."""
        blocking = {
            "direct_identifier",
            "sec_commission_file_number",
            "cik",
            "ein",
            "accession_number",
            "xbrl_namespace",
            "original_filename",
            "executive_name",
            "hq_zip",
            "area_code",
        }
        lc = fixture_data["leak_classes"]
        for name in blocking:
            entry = lc.get(name, {})
            assert entry.get("severity") == "blocking", (
                f"Leak class '{name}' should be blocking, got {entry.get('severity')}"
            )

    def test_warning_leak_classes_have_warning_severity(self, fixture_data: dict[str, Any]) -> None:
        """Quasi-identifier leak classes must have warning severity."""
        warning = {
            "exact_revenue",
            "distinctive_segment_structure",
            "sector_label",
            "fundamentals_bin_fingerprint",
            "synthetic_trajectory_fingerprint",
            "suggestive_fake_name",
        }
        lc = fixture_data["leak_classes"]
        for name in warning:
            entry = lc.get(name, {})
            assert entry.get("severity") == "warning", (
                f"Leak class '{name}' should be warning, got {entry.get('severity')}"
            )

    def test_every_leak_class_has_description(self, fixture_data: dict[str, Any]) -> None:
        """Every leak class must have a description."""
        for name, entry in fixture_data["leak_classes"].items():
            assert entry.get("description"), f"Leak class '{name}' missing description"
            assert entry.get("severity") in ("blocking", "warning"), (
                f"Leak class '{name}' has invalid severity: {entry.get('severity')}"
            )


class TestProfessorGuessesAsEvidence:
    """Professor guesses are attack evidence, not ground truth.

    The actual source mapping shows only one company was correctly
    identified in V2. All other guesses were wrong, but they represent
    useful attack signals that should be scored as risk warnings.
    """

    def test_only_one_correct_guess(self, professor_guesses: dict[str, Any]) -> None:
        """Exactly one professor guess should be marked correct."""
        correct = [cid for cid, g in professor_guesses.items() if g.get("correct")]
        assert len(correct) == 1, f"Expected 1 correct guess, got {len(correct)}: {correct}"

    def test_correct_guess_has_exact_category(self, professor_guesses: dict[str, Any]) -> None:
        """The correct guess must have guess_category 'exact'."""
        correct = [g for g in professor_guesses.values() if g.get("correct")]
        assert len(correct) == 1
        assert correct[0].get("guess_category") == "exact", (
            f"Correct guess should be 'exact', got '{correct[0].get('guess_category')}'"
        )

    def test_wrong_guesses_are_peer_or_sector(self, professor_guesses: dict[str, Any]) -> None:
        """All wrong guesses must be categorized as 'peer' or 'sector'."""
        for cid, g in professor_guesses.items():
            if g.get("correct"):
                continue  # Skip the one correct guess
            cat = g.get("guess_category", "")
            assert cat in ("peer", "sector"), f"{cid}: expected 'peer' or 'sector', got '{cat}'"

    def test_wrong_guesses_have_distance_info(self, professor_guesses: dict[str, Any]) -> None:
        """Every wrong guess must document the guess_distance."""
        for cid, g in professor_guesses.items():
            if g.get("correct"):
                continue
            assert g.get("guess_distance"), f"{cid}: missing guess_distance"

    def test_all_guesses_have_ticker_and_company(self, professor_guesses: dict[str, Any]) -> None:
        """Every professor guess must have ticker and company fields."""
        for cid, g in professor_guesses.items():
            assert g.get("ticker"), f"{cid}: missing ticker"
            assert g.get("company"), f"{cid}: missing company"
            assert "correct" in g, f"{cid}: missing correct field"


class TestV3ReleaseGateBehavior:
    """V3 release gates must treat exact hits as blocking and peer-category
    guesses as risk warnings, NOT as blocking failures.

    The V2 confusion matrix shows that medium-confidence wrong guesses
    are NOT release-safe - they indicate quasi-identifier leakage that
    must be addressed. But they should not block release if the gate
    only requires: no exact hits, no direct identifiers, no metadata leaks.
    """

    def test_v2_only_one_exact_hit_is_blocking_level(
        self, professor_guesses: dict[str, Any]
    ) -> None:
        """The one exact hit in V2 represents a blocking-level failure for V3.

        V3 should produce ZERO exact hits. V2's single exact hit
        is the proof that the V2 pipeline was not release-safe.
        """
        exact_hits = [
            cid for cid, g in professor_guesses.items() if g.get("guess_category") == "exact"
        ]
        assert len(exact_hits) == 1, (
            f"V2 had {len(exact_hits)} exact hits. V3 target: 0 exact hits (blocking failure)."
        )

    def test_v2_wrong_guesses_indicate_quasi_identifier_risk(
        self, professor_guesses: dict[str, Any]
    ) -> None:
        """V2's 7 wrong peer/sector guesses indicate quasi-identifier leakage.

        These are NOT blocking but MUST be scored as warnings. V3 must
        reduce the number and confidence of wrong guesses.
        """
        wrong = [g for g in professor_guesses.values() if not g.get("correct")]
        assert len(wrong) == 7, (
            f"V2 had {len(wrong)} wrong guesses. "
            "V3 target: reduce wrong-guess confidence; these are risk warnings."
        )

    def test_v3_privacy_targets_are_recorded_in_fixture(self, fixture_data: dict[str, Any]) -> None:
        """All V3 privacy targets must be defined in the fixture."""
        targets = fixture_data["v3_privacy_targets"]
        required = [
            "k_peer",
            "actual_source_top_1",
            "actual_source_top_3",
            "llm_confidence",
            "exact_number_match_count",
            "direct_identifier_count",
            "metadata_identifier_count",
        ]
        for key in required:
            assert key in targets, f"Missing V3 privacy target: {key}"

    def test_k_peer_min_five(self, fixture_data: dict[str, Any]) -> None:
        """Minimum plausible peer basket size must be >= 5."""
        k_peer = fixture_data["v3_privacy_targets"]["k_peer"]
        assert k_peer["min"] == 5, f"k_peer min should be 5, got {k_peer['min']}"

    def test_direct_identifier_count_target_zero(self, fixture_data: dict[str, Any]) -> None:
        """Direct identifier count target must be 0."""
        target = fixture_data["v3_privacy_targets"]["direct_identifier_count"]
        assert target["target"] == 0, f"DI count target should be 0, got {target['target']}"


class TestRemediationEncoded:
    """Company-specific remediation targets must be encoded."""

    def test_remediation_has_all_eight_companies(
        self, fixture_data: dict[str, Any], actual_sources: dict[str, Any]
    ) -> None:
        """Remediation entries must exist for all 8 companies."""
        rem = fixture_data["remediation"]
        for cid in actual_sources:
            assert cid in rem, f"Missing remediation for: {cid}"

    def test_remediation_entries_have_required_changes(self, fixture_data: dict[str, Any]) -> None:
        """Every remediation entry must have non-empty required_changes list."""
        for cid, entry in fixture_data["remediation"].items():
            changes = entry.get("required_changes", [])
            assert changes, f"{cid}: empty required_changes"
            assert len(changes) >= 1, f"{cid}: empty required_changes"

    def test_remediation_for_exact_hit_has_broad_archetype(
        self, professor_guesses: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        """The exact-hit company must have broad archetype remediation."""
        correct_cid = None
        for cid, g in professor_guesses.items():
            if g.get("correct"):
                correct_cid = cid
                break
        assert correct_cid is not None, "No correct guess found"
        rem = fixture_data["remediation"][correct_cid]
        required = rem["required_changes"]
        has_archetype = any("archetype" in c.lower() or "broad" in c.lower() for c in required)
        assert has_archetype, (
            f"No broad archetype remediation for exact-hit {correct_cid}: {required}"
        )
