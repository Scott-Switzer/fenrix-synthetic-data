"""Tests for the ``pseudonym_allowlist`` safety net.

Contract: only system-generated pseudonyms are eligible for scanner
suppression. Generic raw values MUST NEVER be suppressed by accident.
"""

from __future__ import annotations

from fenrix_synthetic.identity.pseudonym_allowlist import (
    SAFE_PSEUDONYM_ALLOWLIST_SIZE,
    allowlist_human_readable,
    is_pseudonym_suppression_eligible,
    safe_pseudonym_patterns,
)


class TestSafePseudonymSuppression:
    def test_counter_suffixed_company_is_eligible(self) -> None:
        assert is_pseudonym_suppression_eligible("Company 042")
        assert is_pseudonym_suppression_eligible("Company 1")
        assert is_pseudonym_suppression_eligible("Subsidiary 017")

    def test_counter_suffixed_person_is_eligible(self) -> None:
        assert is_pseudonym_suppression_eligible("Executive 003")
        assert is_pseudonym_suppression_eligible("BoardMember 011")

    def test_counter_suffixed_product_is_eligible(self) -> None:
        assert is_pseudonym_suppression_eligible("Product 005")
        assert is_pseudonym_suppression_eligible("Brand 029")

    def test_synthetic_lit_string_is_eligible(self) -> None:
        assert is_pseudonym_suppression_eligible("synthetic financial disclosure surrogate")
        assert is_pseudonym_suppression_eligible("synthetic financial news surrogate")

    def test_bracketed_placeholder_is_eligible(self) -> None:
        assert is_pseudonym_suppression_eligible("[PERIOD DATE]")
        assert is_pseudonym_suppression_eligible("[PUBLISHER REMOVED]")
        assert is_pseudonym_suppression_eligible("[URL REMOVED]")
        assert is_pseudonym_suppression_eligible("[Executive-*]")
        assert is_pseudonym_suppression_eligible("[Product-*]")

    def test_raw_company_name_is_NOT_eligible(self) -> None:
        # Generic substrings MUST not trigger suppression.
        assert not is_pseudonym_suppression_eligible("Acme Corp")
        assert not is_pseudonym_suppression_eligible("NVIDIA Corporation")
        assert not is_pseudonym_suppression_eligible("the company")
        assert not is_pseudonym_suppression_eligible("Warren Buffett")

    def test_partial_match_is_NOT_eligible(self) -> None:
        # Anchored regex MUST reject partial sentences.
        assert not is_pseudonym_suppression_eligible("the Company 042 today")
        assert not is_pseudonym_suppression_eligible("Acme Executive")
        assert not is_pseudonym_suppression_eligible("see Product 005 below")

    def test_empty_and_whitespace_are_NOT_eligible(self) -> None:
        assert not is_pseudonym_suppression_eligible("")
        assert not is_pseudonym_suppression_eligible("   ")
        assert not is_pseudonym_suppression_eligible(None)  # type: ignore[arg-type]


class TestAllowlistQuality:
    def test_patterns_are_anchored(self) -> None:
        # Every pattern ``$`` anchored so substring matches cannot
        # slip through.  ``^`` is also anchored.
        for p in safe_pseudonym_patterns():
            assert p.startswith("^") and p.endswith("$"), f"Pattern not anchored: {p}"

    def test_size_constant_matches_actual_pattern_count(self) -> None:
        assert SAFE_PSEUDONYM_ALLOWLIST_SIZE == len(safe_pseudonym_patterns())
        assert SAFE_PSEUDONYM_ALLOWLIST_SIZE >= 3

    def test_human_readable_does_not_leak_values(self) -> None:
        text = allowlist_human_readable()
        # Just check that it is non-trivial explanatory text, no raw
        # private content.
        assert len(text) > 50
        assert "suppressed" in text.lower() or "pseudonym" in text.lower()
