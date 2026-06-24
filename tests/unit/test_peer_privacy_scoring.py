"""Unit tests for peer privacy scoring edge cases and contract validation.

Covers:
- Threshold configuration
- Taxonomy completeness
- Forbidden feature enforcement
- Output contract validation
- Edge cases (empty pool, single candidate, unclassified)
"""

from __future__ import annotations

import json
from pathlib import Path

from fenrix_synthetic.anonymization.peer_archetype import (
    ALLOWED_PUBLIC_FEATURES,
    ARCHETYPE_TAXONOMY,
    DEFAULT_MIN_K_PEER,
    DEFAULT_WARN_K_PEER,
    FORBIDDEN_PUBLIC_FEATURES,
    PeerArchetypeProfile,
    PeerCandidate,
    build_peer_archetype_profile,
    compute_k_peer,
    score_peer_candidates,
    write_private_peer_archetype_audit,
    write_public_archetype_card,
)

# ── Threshold configuration tests ─────────────────────────────────────


class TestThresholdConfiguration:
    def test_min_k_peer_default(self) -> None:
        assert DEFAULT_MIN_K_PEER == 5

    def test_warn_k_peer_default(self) -> None:
        assert DEFAULT_WARN_K_PEER == 8

    def test_defaults_are_consistent(self) -> None:
        assert DEFAULT_MIN_K_PEER < DEFAULT_WARN_K_PEER

    def test_custom_thresholds_respected(self) -> None:
        candidates = [
            PeerCandidate(
                candidate_id="p1",
                similarity_score=0.9,
                broad_sector="Tech",
                archetype="digital_platform_services",
            ),
            PeerCandidate(
                candidate_id="p2",
                similarity_score=0.8,
                broad_sector="Tech",
                archetype="digital_platform_services",
            ),
            PeerCandidate(
                candidate_id="p3",
                similarity_score=0.7,
                broad_sector="Tech",
                archetype="digital_platform_services",
            ),
            PeerCandidate(
                candidate_id="p4",
                similarity_score=0.6,
                broad_sector="Tech",
                archetype="digital_platform_services",
            ),
        ]
        k_peer, _, _, _, _ = compute_k_peer(candidates, "SRC_X", similarity_threshold=0.5)
        assert k_peer == 4


# ── Taxonomy completeness ─────────────────────────────────────────────


class TestTaxonomy:
    def test_all_archetypes_have_required_keys(self) -> None:
        required = {
            "public_label",
            "description",
            "allowed_sectors",
            "feature_expectations",
            "forbidden_naming_cues",
            "default_peer_minimum",
        }
        for key, archetype in ARCHETYPE_TAXONOMY.items():
            missing = required - set(archetype.keys())
            assert not missing, f"Archetype {key!r} missing keys: {missing}"

    def test_all_archetypes_peer_minimum_at_least_3(self) -> None:
        for key, archetype in ARCHETYPE_TAXONOMY.items():
            assert archetype["default_peer_minimum"] >= 3, (
                f"Archetype {key!r} peer minimum too low: {archetype['default_peer_minimum']}"
            )

    def test_eight_archetypes_defined(self) -> None:
        assert len(ARCHETYPE_TAXONOMY) >= 8


# ── Forbidden feature enforcement ─────────────────────────────────────


class TestForbiddenFeatures:
    def test_forbidden_set_is_non_empty(self) -> None:
        assert len(FORBIDDEN_PUBLIC_FEATURES) > 10

    def test_no_overlap_allowed_forbidden(self) -> None:
        overlap = ALLOWED_PUBLIC_FEATURES & FORBIDDEN_PUBLIC_FEATURES
        assert not overlap, f"Features both allowed and forbidden: {overlap}"

    def test_forbidden_features_absent_from_public_output(self, tmp_path: Path) -> None:
        pool = [
            {
                "candidate_id": "PEER_A",
                "broad_sector": "Technology",
                "archetype": "digital_platform_services",
                "revenue_bucket": "LARGE",
                "asset_intensity_bucket": "LOW",
                "profitability_bucket": "HIGH",
                "leverage_bucket": "LOW",
                "growth_bucket": "HIGH",
                "similarity_score": 0.9,
            },
        ]
        profile = build_peer_archetype_profile(
            "SRC_FAKE",
            pool,
            anonymized_company_id="COMPANY_FAKE",
            broad_sector="Technology",
            archetype="digital_platform_services",
        )
        out = tmp_path / "profile"
        card_path, _ = write_public_archetype_card(profile, out)
        card = json.loads(card_path.read_text(encoding="utf-8"))
        card_str = json.dumps(card)
        for forbidden in [
            "CIK",
            "exact_ticker",
            "exact_company_name",
            "exact_revenue",
            "exact_market_cap",
        ]:
            assert forbidden not in card_str, f"forbidden key {forbidden!r} in public card"


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_pool_produces_empty_candidates(self) -> None:
        candidates = score_peer_candidates({"broad_sector": "Technology"}, [])
        assert len(candidates) == 0

    def test_single_candidate_pool(self) -> None:
        pool = [
            {
                "candidate_id": "ONLY_PEER",
                "broad_sector": "Technology",
                "archetype": "digital_platform_services",
                "similarity_score": 0.9,
            }
        ]
        candidates = score_peer_candidates({"broad_sector": "Technology"}, pool)
        assert len(candidates) == 1
        assert candidates[0].candidate_id == "ONLY_PEER"

    def test_unclassified_archetype_profile(self) -> None:
        """Unclassified companies still produce a valid profile."""
        pool = [
            {
                "candidate_id": "PEER_U",
                "broad_sector": "Aerospace & Defense",
                "archetype": "unclassified",
                "revenue_bucket": "LARGE",
            }
        ]
        profile = build_peer_archetype_profile(
            "SRC_U",
            pool,
            anonymized_company_id="COMPANY_U",
            broad_sector="Aerospace & Defense",
            archetype="unclassified",
        )
        assert profile.archetype == "unclassified"
        assert profile.broad_sector == "Aerospace & Defense"

    def test_zero_k_peer_empty_pool(self) -> None:
        candidates = score_peer_candidates({"broad_sector": "Technology"}, [])
        k_peer, source_rank, in_top_1, in_top_3, in_top_5 = compute_k_peer(candidates, "SRC_X")
        assert k_peer == 0
        assert source_rank is None
        assert not in_top_1
        assert not in_top_3
        assert not in_top_5

    def test_source_not_in_pool_no_rank(self) -> None:
        candidates = [
            PeerCandidate(
                candidate_id="P1",
                similarity_score=0.9,
                broad_sector="Tech",
                archetype="digital_platform_services",
            ),
            PeerCandidate(
                candidate_id="P2",
                similarity_score=0.8,
                broad_sector="Tech",
                archetype="digital_platform_services",
            ),
        ]
        k_peer, source_rank, in_top_1, in_top_3, in_top_5 = compute_k_peer(
            candidates, "SRC_NOT_PRESENT"
        )
        assert k_peer == 2
        assert source_rank is None


# ── Output contract ───────────────────────────────────────────────────


class TestOutputContract:
    def test_audit_has_schema_version(self, tmp_path: Path) -> None:
        profile = PeerArchetypeProfile(
            anonymized_company_id="C001",
            broad_sector="Tech",
            archetype="digital_platform_services",
            k_peer=6,
            peer_candidates=[],
            selected_peer_mix=[],
            passes_peer_privacy=True,
        )
        path = write_private_peer_archetype_audit(profile, tmp_path / "private/qa")
        audit = json.loads(path.read_text(encoding="utf-8"))
        assert audit["schema_version"] == "1.0"

    def test_card_has_schema_version(self, tmp_path: Path) -> None:
        profile = PeerArchetypeProfile(
            anonymized_company_id="C001",
            broad_sector="Tech",
            archetype="digital_platform_services",
            k_peer=6,
            peer_candidates=[],
            selected_peer_mix=[],
            passes_peer_privacy=True,
        )
        card_path, _ = write_public_archetype_card(profile, tmp_path / "profile")
        card = json.loads(card_path.read_text(encoding="utf-8"))
        assert card["schema_version"] == "1.0"

    def test_both_outputs_are_written(self, tmp_path: Path) -> None:
        profile = PeerArchetypeProfile(
            anonymized_company_id="C001",
            broad_sector="Tech",
            archetype="digital_platform_services",
            k_peer=6,
            peer_candidates=[],
            selected_peer_mix=[],
            passes_peer_privacy=True,
        )
        audit_path = write_private_peer_archetype_audit(profile, tmp_path / "private/qa")
        card_path, md_path = write_public_archetype_card(
            profile, tmp_path / "public/anonymized/C001/profile"
        )
        assert audit_path.is_file()
        assert card_path.is_file()
        assert md_path.is_file()

    def test_private_audit_not_written_to_public_dir(self, tmp_path: Path) -> None:
        profile = PeerArchetypeProfile(
            anonymized_company_id="C001",
            broad_sector="Tech",
            archetype="digital_platform_services",
            k_peer=6,
            peer_candidates=[],
            selected_peer_mix=[],
            passes_peer_privacy=True,
        )
        audit_path = write_private_peer_archetype_audit(profile, tmp_path / "private/qa")
        assert "private" in str(audit_path)
        assert "public" not in str(audit_path)


# ── Profile model validation ─────────────────────────────────────────


class TestProfileModel:
    def test_profile_serializable(self) -> None:
        profile = PeerArchetypeProfile(
            anonymized_company_id="C001",
            broad_sector="Tech",
            archetype="digital_platform_services",
            k_peer=6,
            peer_candidates=[],
            selected_peer_mix=["P1", "P2"],
            passes_peer_privacy=True,
            warnings=["test warning"],
        )
        d = profile.model_dump()
        assert d["anonymized_company_id"] == "C001"
        assert d["passes_peer_privacy"] is True

    def test_profile_with_source_rank(self) -> None:
        profile = PeerArchetypeProfile(
            anonymized_company_id="C001",
            broad_sector="Tech",
            archetype="digital_platform_services",
            k_peer=6,
            peer_candidates=[],
            selected_peer_mix=[],
            source_rank=4,
            source_in_top_1=False,
            source_in_top_3=False,
            source_in_top_5=True,
            passes_peer_privacy=True,
            warnings=["source ranks #4 — within top-5 warning threshold"],
        )
        assert profile.source_rank == 4
        assert not profile.source_in_top_1
        assert not profile.source_in_top_3
        assert profile.source_in_top_5
