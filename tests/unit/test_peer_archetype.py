"""Unit tests for peer archetype assignment and privacy scoring.

Covers:
- Fixture loading
- Deterministic archetype assignment
- k_peer computation
- Source rank evaluation
- Public card privacy
- Private audit detail
- Deterministic output with seed
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fenrix_synthetic.anonymization.peer_archetype import (
    DEFAULT_MIN_K_PEER,
    DEFAULT_WARN_K_PEER,
    FORBIDDEN_PUBLIC_FEATURES,
    PeerArchetypeProfile,
    assign_company_archetype,
    build_peer_archetype_profile,
    compute_k_peer,
    evaluate_peer_privacy,
    load_peer_universe,
    score_peer_candidates,
    write_private_peer_archetype_audit,
    write_public_archetype_card,
)

# ── Fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def peer_universe() -> dict[str, list[dict]]:
    path = Path(__file__).parent.parent / "fixtures" / "peer_archetype" / "peer_universe.yaml"
    companies_by_source, _archetypes = load_peer_universe(path)
    return companies_by_source


# ── Fixture loading ────────────────────────────────────────────────────


class TestLoadPeerUniverse:
    def test_loads_all_five_cases(self, peer_universe: dict) -> None:
        assert len(peer_universe) == 5
        for src in ("SRC_A", "SRC_B", "SRC_C", "SRC_D", "SRC_E"):
            assert src in peer_universe

    def test_src_a_has_seven_peers(self, peer_universe: dict) -> None:
        assert len(peer_universe["SRC_A"]) >= 8  # source + peers

    def test_src_b_has_only_two_peers(self, peer_universe: dict) -> None:
        non_source = [c for c in peer_universe["SRC_B"] if c["candidate_id"] != "SRC_B"]
        assert len(non_source) == 2


# ── Archetype assignment ──────────────────────────────────────────────


class TestArchetypeAssignment:
    def test_assigns_consumer_defensive(self) -> None:
        result = assign_company_archetype(
            "Consumer Defensive",
            {
                "revenue_bucket": "LARGE",
                "profitability_bucket": "HIGH",
                "growth_bucket": "MODERATE",
            },
        )
        assert result == "consumer_defensive_multiline"

    def test_assigns_digital_platform(self) -> None:
        result = assign_company_archetype(
            "Technology",
            {
                "asset_intensity_bucket": "LOW",
                "profitability_bucket": "HIGH",
                "growth_bucket": "HIGH",
                "leverage_bucket": "LOW",
            },
        )
        assert result == "digital_platform_services"

    def test_assigns_regional_financial(self) -> None:
        result = assign_company_archetype(
            "Financial Services",
            {
                "asset_intensity_bucket": "HIGH",
                "profitability_bucket": "MEDIUM",
                "growth_bucket": "LOW",
                "leverage_bucket": "MODERATE",
            },
        )
        assert result == "regional_financial_institution"

    def test_unmatched_sector_returns_unclassified(self) -> None:
        result = assign_company_archetype("Aerospace & Defense")
        assert result == "unclassified"

    def test_deterministic_assignment(self) -> None:
        r1 = assign_company_archetype("Consumer Defensive", {"revenue_bucket": "LARGE"})
        r2 = assign_company_archetype("Consumer Defensive", {"revenue_bucket": "LARGE"})
        assert r1 == r2


# ── Candidate scoring ─────────────────────────────────────────────────


class TestCandidateScoring:
    def test_scores_and_ranks_peers(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_A"]
        source_feats = {
            "broad_sector": "Consumer Defensive",
            "archetype": "consumer_defensive_multiline",
            "revenue_bucket": "LARGE",
            "asset_intensity_bucket": "MEDIUM",
            "profitability_bucket": "HIGH",
            "leverage_bucket": "LOW",
            "growth_bucket": "MODERATE",
        }
        candidates = score_peer_candidates(source_feats, pool)
        assert len(candidates) >= 7
        ids = [c.candidate_id for c in candidates]
        assert "SRC_A" in ids
        assert "PEER_001" in ids
        # PEER_001 (similarity_score=0.92) should be the top peer after source
        non_source = [c for c in candidates if c.candidate_id != "SRC_A"]
        assert non_source[0].candidate_id == "PEER_001"

    def test_deterministic_with_same_seed(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_A"]
        source_feats = {"broad_sector": "Consumer Defensive"}
        r1 = score_peer_candidates(source_feats, pool, seed=42)
        r2 = score_peer_candidates(source_feats, pool, seed=42)
        for a, b in zip(r1, r2, strict=False):
            assert a.candidate_id == b.candidate_id
            assert a.similarity_score == b.similarity_score

    def test_different_seed_produces_different_jitter(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_A"]
        source_feats = {"broad_sector": "Consumer Defensive"}
        r1 = score_peer_candidates(source_feats, pool, seed=42)
        r2 = score_peer_candidates(source_feats, pool, seed=99)
        scores1 = [c.similarity_score for c in r1]
        scores2 = [c.similarity_score for c in r2]
        assert scores1 != scores2


# ── k_peer computation ────────────────────────────────────────────────


class TestKPeer:
    def test_src_a_has_k_peer_at_least_5(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_A"]
        source_feats = {"broad_sector": "Consumer Defensive"}
        candidates = score_peer_candidates(source_feats, pool)
        k_peer, _, _, _, _ = compute_k_peer(candidates, "SRC_A")
        assert k_peer >= DEFAULT_MIN_K_PEER

    def test_src_b_has_k_peer_below_3(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_B"]
        source_feats = {"broad_sector": "Digital Platform Services"}
        candidates = score_peer_candidates(source_feats, pool)
        k_peer, _, _, _, _ = compute_k_peer(candidates, "SRC_B")
        assert k_peer < DEFAULT_MIN_K_PEER

    def test_source_excluded_from_k_peer(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_A"]
        source_feats = {"broad_sector": "Consumer Defensive"}
        candidates = score_peer_candidates(source_feats, pool)
        k_peer, _source_rank, _, _, _ = compute_k_peer(candidates, "SRC_A")
        non_source = [c for c in candidates if c.candidate_id != "SRC_A"]
        assert k_peer <= len(non_source)

    def test_source_rank_detected(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_C"]
        source_feats = {"broad_sector": "Regional Financial Institution"}
        candidates = score_peer_candidates(source_feats, pool)
        _, source_rank, in_top_1, in_top_3, in_top_5 = compute_k_peer(candidates, "SRC_C")
        assert source_rank is not None
        assert in_top_1
        assert in_top_3
        assert in_top_5


# ── Privacy evaluation ────────────────────────────────────────────────


class TestPrivacyEvaluation:
    def test_src_a_passes_privacy(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_A"]
        source_feats = {"broad_sector": "Consumer Defensive"}
        candidates = score_peer_candidates(source_feats, pool)
        passes, failures, warnings = evaluate_peer_privacy(candidates, "SRC_A")
        assert passes
        assert len(failures) == 0
        assert any("passed" in w.lower() for w in warnings)

    def test_src_b_fails_low_k(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_B"]
        source_feats = {"broad_sector": "Digital Platform Services"}
        candidates = score_peer_candidates(source_feats, pool)
        passes, failures, _warnings = evaluate_peer_privacy(candidates, "SRC_B")
        assert not passes
        assert any("k_peer" in f for f in failures)

    def test_src_c_fails_top_1(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_C"]
        source_feats = {"broad_sector": "Regional Financial Institution"}
        candidates = score_peer_candidates(source_feats, pool)
        passes, failures, _warnings = evaluate_peer_privacy(candidates, "SRC_C")
        assert not passes
        assert any("top 1" in f.lower() for f in failures)

    def test_src_d_fails_top_3(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_D"]
        source_feats = {"broad_sector": "Industrial Distribution Services"}
        candidates = score_peer_candidates(source_feats, pool)
        passes, failures, _warnings = evaluate_peer_privacy(candidates, "SRC_D")
        assert not passes
        assert any("top 3" in f.lower() for f in failures)

    def test_src_e_detects_high_source_rank(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_E"]
        source_feats = {"broad_sector": "Large Scale Technology Services"}
        candidates = score_peer_candidates(source_feats, pool)
        passes, failures, _warnings = evaluate_peer_privacy(candidates, "SRC_E")
        # SRC_E has similarity_score=4.0 (highest in pool) so it ranks #1 → FAIL
        assert not passes
        assert any("top 1" in f.lower() for f in failures)


# ── Build complete profile ────────────────────────────────────────────


class TestBuildProfile:
    def test_build_profile_for_src_a(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_A"]
        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
        )
        assert isinstance(profile, PeerArchetypeProfile)
        assert profile.anonymized_company_id == "COMPANY_001"
        assert profile.passes_peer_privacy is True
        assert profile.k_peer >= DEFAULT_MIN_K_PEER
        assert len(profile.selected_peer_mix) == 5

    def test_deterministic_with_same_seed(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_A"]
        p1 = build_peer_archetype_profile("SRC_A", pool, seed=42)
        p2 = build_peer_archetype_profile("SRC_A", pool, seed=42)
        assert p1.selected_peer_mix == p2.selected_peer_mix
        assert p1.k_peer == p2.k_peer
        assert p1.source_rank == p2.source_rank

    def test_different_seed_different_mix(self, peer_universe: dict) -> None:
        pool = peer_universe["SRC_A"]
        p1 = build_peer_archetype_profile("SRC_A", pool, seed=42)
        p2 = build_peer_archetype_profile("SRC_A", pool, seed=99)
        assert p1.k_peer == p2.k_peer  # k_peer is independent of seed


# ── Public card privacy ───────────────────────────────────────────────


class TestPublicCardPrivacy:
    def test_card_excludes_forbidden_features(self, tmp_path: Path) -> None:
        path = Path(__file__).parent.parent / "fixtures" / "peer_archetype" / "peer_universe.yaml"
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]
        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
        )
        out = tmp_path / "profile"
        card_path, _ = write_public_archetype_card(profile, out)
        raw = card_path.read_text(encoding="utf-8")

        for forbidden in FORBIDDEN_PUBLIC_FEATURES:
            if forbidden in ("exact_ticker", "exact_company_name"):
                assert forbidden not in raw, f"forbidden key {forbidden!r} in public card"
        assert "CIK" not in raw
        assert "exact_revenue" not in raw
        assert "exact_market_cap" not in raw

    def test_card_has_public_label_not_key(self, tmp_path: Path) -> None:
        path = Path(__file__).parent.parent / "fixtures" / "peer_archetype" / "peer_universe.yaml"
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]
        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
        )
        out = tmp_path / "profile"
        card_path, _ = write_public_archetype_card(profile, out)
        card = json.loads(card_path.read_text(encoding="utf-8"))
        assert card["archetype_label"] == "Consumer Defensive — Multi-line"
        assert "anonymized_company_id" in card
        assert "k_peer" in card

    def test_markdown_has_archetype_no_source_id(self, tmp_path: Path) -> None:
        path = Path(__file__).parent.parent / "fixtures" / "peer_archetype" / "peer_universe.yaml"
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]
        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
        )
        out = tmp_path / "profile"
        _, md_path = write_public_archetype_card(profile, out)
        md_text = md_path.read_text(encoding="utf-8")
        assert "COMPANY_001" in md_text
        assert "Consumer Defensive" in md_text
        assert "SRC_A" not in md_text

    def test_card_no_source_rank_public(self, tmp_path: Path) -> None:
        path = Path(__file__).parent.parent / "fixtures" / "peer_archetype" / "peer_universe.yaml"
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]
        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
        )
        out = tmp_path / "profile"
        card_path, _ = write_public_archetype_card(profile, out)
        card = json.loads(card_path.read_text(encoding="utf-8"))
        assert "source_rank" not in card
        assert "source_in_top" not in str(card)


# ── Private audit detail ──────────────────────────────────────────────


class TestPrivateAudit:
    def test_audit_contains_debugging_detail(self, tmp_path: Path) -> None:
        path = Path(__file__).parent.parent / "fixtures" / "peer_archetype" / "peer_universe.yaml"
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]
        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
        )
        out = tmp_path / "private" / "qa"
        audit_path = write_private_peer_archetype_audit(profile, out)
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        assert "source_rank" in audit
        assert "source_in_top_1" in audit
        assert "source_in_top_3" in audit
        assert "peer_candidates" in audit
        assert len(audit["peer_candidates"]) > 0
        assert "similarity_score" in str(audit["peer_candidates"])

    def test_audit_includes_thresholds(self, tmp_path: Path) -> None:
        path = Path(__file__).parent.parent / "fixtures" / "peer_archetype" / "peer_universe.yaml"
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]
        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
        )
        out = tmp_path / "private" / "qa"
        audit_path = write_private_peer_archetype_audit(profile, out)
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        thresholds = audit["thresholds_applied"]
        assert thresholds["min_k_peer"] == DEFAULT_MIN_K_PEER
        assert thresholds["warn_k_peer"] == DEFAULT_WARN_K_PEER
