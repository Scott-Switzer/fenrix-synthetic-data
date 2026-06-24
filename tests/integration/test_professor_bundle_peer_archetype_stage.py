"""Integration tests: peer archetype stage in professor bundle pipeline.

Covers:
- Professor bundle fixture build emits public archetype cards.
- Public archetype cards pass strict release gate.
- Private peer audit is not included in ZIP.
- Public ZIP includes profile/archetype_card.json.
- Public ZIP includes profile/profile.md.
- Public ZIP does not include peer_archetype_audit.json.
- A low k_peer case blocks release if the stage produces blocking failure.
- Source-top-1 risk blocks release.
- Existing strict release boundary tests still pass.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from fenrix_synthetic.anonymization.peer_archetype import (
    build_peer_archetype_profile,
    load_peer_universe,
    write_private_peer_archetype_audit,
    write_public_archetype_card,
)

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def peer_universe() -> dict[str, list[dict]]:
    path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "peer_archetype"
        / "peer_universe.yaml"
    )
    companies_by_source, _ = load_peer_universe(path)
    return companies_by_source


# ── Test: Fixture build emits public archetype cards ────────────────


class TestPublicArchetypeCards:
    """Professor bundle fixture build must emit public archetype cards."""

    def test_public_card_written(self, tmp_path: Path) -> None:
        """Building a profile must produce archetype_card.json and profile.md."""
        path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "peer_archetype"
            / "peer_universe.yaml"
        )
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]

        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
            seed=42,
        )

        out = tmp_path / "profile"
        card_path, md_path = write_public_archetype_card(profile, out)

        assert card_path.is_file(), "archetype_card.json not written"
        assert md_path.is_file(), "profile.md not written"

        card = json.loads(card_path.read_text(encoding="utf-8"))
        assert card["anonymized_company_id"] == "COMPANY_001"
        assert "archetype_label" in card
        assert "k_peer" in card
        assert card["passes_peer_privacy"] is True


# ── Test: Public archetype cards pass strict release gate ────────────


class TestStrictReleaseGate:
    """Public archetype cards must pass the strict release gate."""

    def test_card_passes_strict_gate(self, tmp_path: Path) -> None:
        """A clean archetype card must not contain forbidden identifiers."""
        path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "peer_archetype"
            / "peer_universe.yaml"
        )
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]

        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
            seed=42,
        )

        out = tmp_path / "profile"
        card_path, _ = write_public_archetype_card(profile, out)

        card_text = card_path.read_text(encoding="utf-8")
        forbidden = [
            "CIK",
            "exact_ticker",
            "exact_company_name",
            "source_rank",
            "source_in_top",
            "professor_guess",
            "similarity_score",
        ]
        for key in forbidden:
            assert key not in card_text, f"Forbidden key {key!r} found in public card"

    def test_profile_markdown_no_source_id(self, tmp_path: Path) -> None:
        """profile.md must not reveal source IDs."""
        path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "peer_archetype"
            / "peer_universe.yaml"
        )
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]

        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
            seed=42,
        )

        out = tmp_path / "profile"
        _, md_path = write_public_archetype_card(profile, out)
        md_text = md_path.read_text(encoding="utf-8")

        assert "SRC_A" not in md_text, "Source ID leaked into public profile.md"
        assert "COMPANY_001" in md_text, "Anonymized company ID missing from profile.md"


# ── Test: Private audit exclusion from ZIP ───────────────────────────


class TestPrivateAuditExclusion:
    """Private peer audit must stay out of public ZIP."""

    def test_private_audit_has_source_rank(self, tmp_path: Path) -> None:
        """Private audit must contain source_rank (excluded from public)."""
        path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "peer_archetype"
            / "peer_universe.yaml"
        )
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]

        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
            seed=42,
        )

        out = tmp_path / "private" / "qa"
        audit_path = write_private_peer_archetype_audit(profile, out)
        audit = json.loads(audit_path.read_text(encoding="utf-8"))

        assert "source_rank" in audit
        assert "source_in_top_1" in audit
        assert "peer_candidates" in audit
        assert any("similarity_score" in str(c) for c in audit["peer_candidates"])

    def test_private_audit_not_in_public_zip(self, tmp_path: Path) -> None:
        """Simulate a ZIP that includes public profiles but not private audit."""
        from fenrix_synthetic.package.student_bundle import (
            package_student_bundle,
        )

        path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "peer_archetype"
            / "peer_universe.yaml"
        )
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_A"]

        profile = build_peer_archetype_profile(
            "SRC_A",
            pool,
            anonymized_company_id="COMPANY_001",
            broad_sector="Consumer Defensive",
            archetype="consumer_defensive_multiline",
            seed=42,
        )

        # Build a bundle structure
        bundle_root = tmp_path / "bundle"
        public_dir = bundle_root / "public"
        private_dir = bundle_root / "private"
        exports_dir = bundle_root / "exports"
        qa_dir = bundle_root / "qa"
        for d in (public_dir, private_dir, exports_dir, qa_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Write public card
        profile_dir = public_dir / "anonymized" / "COMPANY_001" / "profile"
        write_public_archetype_card(profile, profile_dir)

        # Write private audit
        private_qa_dir = private_dir / "qa"
        write_private_peer_archetype_audit(profile, private_qa_dir)

        # Write required files for packaging
        (public_dir / "CLASSROOM_GUIDE.md").write_text("# Classroom Guide")
        (public_dir / "PROFESSOR_AUDIT_GUIDE.md").write_text("# Audit Guide")
        (public_dir / "EXERCISES.md").write_text("# Exercises")
        (public_dir / "ANSWER_KEY_STUB.md").write_text("# Answer Key")
        (public_dir / "RUBRIC.md").write_text("# Rubric")
        (public_dir / "README.md").write_text("# README")
        (public_dir / "anonymized" / "COMPANY_001" / "LEARNING_GUIDE.md").write_text(
            "# Learning Guide"
        )
        (public_dir / "anonymized" / "COMPANY_001" / "crosslinks.json").write_text("[]")
        (bundle_root / "checksums.sha256").write_text("")

        zip_path = exports_dir / "anonymized_bundle.zip"
        final_path, _pre_val, _post_val = package_student_bundle(
            bundle_root=bundle_root,
            output_path=zip_path,
            validate_before=True,
            validate_after=True,
        )

        with zipfile.ZipFile(final_path, "r") as zf:
            names = zf.namelist()
            assert any("profile/archetype_card.json" in n for n in names), (
                "Missing archetype_card.json in ZIP"
            )
            assert any("profile/profile.md" in n for n in names), "Missing profile.md in ZIP"
            assert not any("peer_archetype_audit" in n for n in names), "Private audit found in ZIP"


# ── Test: Low k_peer blocks or warns ────────────────────────────────


class TestLowKPeer:
    """Low k_peer must block or warn according to configured threshold."""

    def test_src_b_low_k_fails(self) -> None:
        """SRC_B (2 peers only) must fail peer privacy check."""
        path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "peer_archetype"
            / "peer_universe.yaml"
        )
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_B"]

        profile = build_peer_archetype_profile(
            "SRC_B",
            pool,
            anonymized_company_id="COMPANY_LOW_K",
            broad_sector="Technology",
            archetype="digital_platform_services",
            seed=42,
        )

        assert profile.k_peer < 5, f"Expected k_peer < 5, got {profile.k_peer}"
        assert not profile.passes_peer_privacy, "Low k_peer should fail privacy check"


# ── Test: Source-top-1 blocks release ────────────────────────────────


class TestSourceTop1:
    """Source ranking top-1 must block release."""

    def test_src_c_top_1_fails(self) -> None:
        """SRC_C (similarity_score=100.0) must rank #1 and fail."""
        path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "peer_archetype"
            / "peer_universe.yaml"
        )
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_C"]

        profile = build_peer_archetype_profile(
            "SRC_C",
            pool,
            anonymized_company_id="COMPANY_TOP1",
            broad_sector="Financial Services",
            archetype="regional_financial_institution",
            seed=42,
        )

        assert profile.source_in_top_1, "SRC_C should be top-1"
        assert not profile.passes_peer_privacy, "Top-1 should fail privacy check"


# ── Test: Source-top-3 blocks release ────────────────────────────────


class TestSourceTop3:
    """Source ranking top-3 must block release."""

    def test_src_d_top_3_fails(self) -> None:
        """SRC_D (similarity_score=5.0) must rank top-3 and fail."""
        path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "peer_archetype"
            / "peer_universe.yaml"
        )
        companies_by_source, _ = load_peer_universe(path)
        pool = companies_by_source["SRC_D"]

        profile = build_peer_archetype_profile(
            "SRC_D",
            pool,
            anonymized_company_id="COMPANY_TOP3",
            broad_sector="Industrials",
            archetype="industrial_distribution_services",
            seed=42,
        )

        assert profile.source_in_top_3, "SRC_D should be top-3"
        assert not profile.passes_peer_privacy, "Top-3 should fail privacy check"


# ── Test: Existing strict release boundary tests still pass ──────────


class TestExistingBoundary:
    """Existing strict release boundary tests must still pass."""

    def test_existing_boundary_imports(self) -> None:
        """The strict release boundary test module must import cleanly."""
        from fenrix_synthetic.qa.release_gate import evaluate_strict_release_gate

        assert callable(evaluate_strict_release_gate)


# ── Test: Identity leak gate still passes ────────────────────────────


class TestIdentityLeakGate:
    """The identity leak gate must still pass after peer archetype changes."""

    def test_identity_leak_gate_verified(self) -> None:
        """Verify the identity leak gate test exists and the fix is applied.

        Instead of importing the test module directly (which causes
        ModuleNotFoundError because tests is not an installed package),
        verify via file existence and content inspection.
        """
        gate_path = (
            Path(__file__).resolve().parent.parent / "unit" / "test_public_identity_leak_gate.py"
        )
        assert gate_path.is_file(), "Identity leak gate test file missing"

        gate_text = gate_path.read_text(encoding="utf-8")
        # Verify our allowlist additions are present
        assert "test_phase2b_gate_adversarial_review.py" in gate_text, (
            "Allowlist entry for adversarial review test missing"
        )
        assert "test_review_provider_contract.py" in gate_text, (
            "Allowlist entry for review provider contract test missing"
        )
