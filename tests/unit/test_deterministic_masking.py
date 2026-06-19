from __future__ import annotations

import re

import pytest

from fenrix_synthetic.identity import (
    EntityRegistry,
    EntityType,
    MatchPolicy,
)
from fenrix_synthetic.masking.deterministic import (
    MatchEntry,
    build_accession_dashed_pattern,
    build_cik_padded_pattern,
    build_domain_url_pattern,
    build_email_pattern,
    build_possessive_pattern,
    build_ticker_exchange_pattern,
    build_ticker_parenthesized_pattern,
    get_patterns_for_alias,
    is_unsafe_short_token,
    normalize_text,
)


class TestNormalizeText:
    def test_nfkc_normalization(self):
        result = normalize_text("\uff34\uff45\uff53\uff54")  # Fullwidth "Test"
        assert result == "Test"

    def test_smart_apostrophes(self):
        result = normalize_text("It\u2019s test\u2018s")
        assert result == "It's test's"

    def test_smart_quotes(self):
        result = normalize_text("\u201cHello\u201d \u2018world\u2019")
        assert result == "\"Hello\" 'world'"

    def test_whitespace_collapse(self):
        result = normalize_text("Too    many   spaces")
        assert result == "Too many spaces"

    def test_mixed_content(self):
        text = "Canary\u2019s\u00a0Corp"
        result = normalize_text(text)
        assert "'" in result


class TestPatternBuilders:
    def test_build_ticker_exchange_pattern(self):
        pattern = build_ticker_exchange_pattern("CHC")
        assert re.search(pattern, "NYSE: CHC")
        assert re.search(pattern, "NASDAQ:CHC")
        assert re.search(pattern, "NYSE Arca: CHC")
        assert not re.search(pattern, "CHC")  # bare ticker

    def test_build_ticker_parenthesized_pattern(self):
        pattern = build_ticker_parenthesized_pattern("CHC")
        assert re.search(pattern, "(CHC)")
        assert not re.search(pattern, "CHC")
        assert not re.search(pattern, "(CHCC)")

    def test_build_cik_padded_pattern(self):
        pattern = build_cik_padded_pattern("0000999999")
        assert re.search(pattern, "CIK 0000999999")
        assert re.search(pattern, "CIK #999999")
        assert re.search(pattern, "999999")
        assert not re.search(pattern, "9999990")

    def test_build_accession_dashed_pattern(self):
        pattern = build_accession_dashed_pattern("0000999999-26-000001")
        assert re.search(pattern, "0000999999-26-000001")
        assert not re.search(pattern, "000099999926000001")

    def test_build_domain_url_pattern(self):
        pattern = build_domain_url_pattern("canary-test.invalid")
        assert re.search(pattern, "https://www.canary-test.invalid")
        assert re.search(pattern, "http://canary-test.invalid/path")
        assert re.search(pattern, "canary-test.invalid")

    def test_build_email_pattern(self):
        pattern = build_email_pattern("canary-test.invalid")
        assert re.search(pattern, "info@canary-test.invalid")
        assert re.search(pattern, "support@canary-test.invalid")
        assert not re.search(pattern, "canary-test.invalid")  # bare domain

    def test_build_possessive_pattern(self):
        pattern = build_possessive_pattern("Canary Holdings")
        assert re.search(pattern, "Canary Holdings's"), str(pattern)
        assert re.search(pattern, "Canary Holdings' "), str(pattern)
        assert not re.search(pattern, "Canary Holdings "), "bare text should not match"  # bare


class TestIsUnsafeShortToken:
    def test_short_tokens_flagged(self):
        assert is_unsafe_short_token("A") is True
        assert is_unsafe_short_token("AB") is True
        assert is_unsafe_short_token("CHC") is False

    def test_common_words_flagged(self):
        assert is_unsafe_short_token("THE") is True
        assert is_unsafe_short_token("AND") is True

    def test_meaningful_tickers_allowed(self):
        assert is_unsafe_short_token("AAPL") is False
        assert is_unsafe_short_token("MSFT") is False
        assert is_unsafe_short_token("GOOGL") is False


class TestPatternsForAlias:
    def test_literal_pattern(self, sample_registry):
        entity = sample_registry.get_entity("ent-001")
        alias = sample_registry.get_alias("ali-001")
        assert alias is not None
        patterns = get_patterns_for_alias(alias, sample_registry)
        assert len(patterns) >= 1
        ptype, pat, repl, _, _flags = patterns[0]
        assert ptype == "literal"
        assert repl == entity.assigned_pseudonym

    def test_ticker_patterns(self, sample_registry):
        alias = sample_registry.get_alias("ali-002")
        assert alias is not None
        patterns = get_patterns_for_alias(alias, sample_registry)
        ptypes = [p[0] for p in patterns]
        assert "ticker" in ptypes
        assert "ticker_exchange" in ptypes

    def test_domain_patterns(self, sample_registry):
        alias = sample_registry.get_alias("ali-006")
        assert alias is not None
        patterns = get_patterns_for_alias(alias, sample_registry)
        ptypes = [p[0] for p in patterns]
        assert "url" in ptypes
        assert "domain" in ptypes


class TestMatchEntry:
    def test_span_id_required(self):
        entry = MatchEntry(
            span_id="span-001",
            document_artifact_id="doc-001",
            original_start=10,
            original_end=25,
            entity_id="ent-001",
            alias_id="ali-001",
            entity_type="company",
            match_policy="literal",
            priority=100,
            matched_text="Canary Holdings",
            replacement="Company 001",
        )
        assert entry.span_id == "span-001"
        assert entry.original_start == 10
        assert entry.original_end == 25
        assert len(entry.matched_text_hash) == 64

    def test_matched_text_hash(self):
        entry1 = MatchEntry(
            span_id="s1",
            document_artifact_id="d1",
            original_start=0,
            original_end=5,
            entity_id="e1",
            alias_id="a1",
            entity_type="company",
            match_policy="literal",
            priority=100,
            matched_text="Hello",
            replacement="World",
        )
        entry2 = MatchEntry(
            span_id="s2",
            document_artifact_id="d1",
            original_start=0,
            original_end=5,
            entity_id="e1",
            alias_id="a1",
            entity_type="company",
            match_policy="literal",
            priority=100,
            matched_text="Hello",
            replacement="World",
        )
        assert entry1.matched_text_hash == entry2.matched_text_hash

    def test_different_text_different_hash(self):
        entry1 = MatchEntry(
            span_id="s1",
            document_artifact_id="d1",
            original_start=0,
            original_end=5,
            entity_id="e1",
            alias_id="a1",
            entity_type="company",
            match_policy="literal",
            priority=100,
            matched_text="Hello",
            replacement="World",
        )
        entry2 = MatchEntry(
            span_id="s2",
            document_artifact_id="d1",
            original_start=0,
            original_end=5,
            entity_id="e1",
            alias_id="a1",
            entity_type="company",
            match_policy="literal",
            priority=100,
            matched_text="World",
            replacement="Hello",
        )
        assert entry1.matched_text_hash != entry2.matched_text_hash


@pytest.fixture
def sample_registry() -> EntityRegistry:
    reg = EntityRegistry.create("C001", "reg-test-mask")
    reg.add_entity("ent-001", EntityType.COMPANY, "Canary Holdings Corporation")
    reg.add_entity("ent-002", EntityType.TICKER, "CHC")
    reg.add_entity("ent-003", EntityType.CIK, "0000999999")
    reg.add_entity("ent-004", EntityType.EXECUTIVE, "Eleanor Testperson")
    reg.add_entity("ent-005", EntityType.COMPANY_DOMAIN, "canary-test.invalid")
    reg.add_alias(
        "ali-001",
        "ent-001",
        "Canary Holdings Corporation",
        entity_type=EntityType.COMPANY,
        match_policy=MatchPolicy.LITERAL,
        priority=100,
    )
    reg.add_alias(
        "ali-002",
        "ent-002",
        "CHC",
        entity_type=EntityType.TICKER,
        match_policy=MatchPolicy.TICKER_EXACT,
    )
    reg.add_alias(
        "ali-003",
        "ent-003",
        "0000999999",
        entity_type=EntityType.CIK,
        match_policy=MatchPolicy.CIK_PADDED,
    )
    reg.add_alias(
        "ali-004",
        "ent-001",
        "Canary Holdings",
        entity_type=EntityType.COMPANY,
        match_policy=MatchPolicy.LITERAL,
        priority=150,
    )
    reg.add_alias(
        "ali-005",
        "ent-004",
        "Eleanor Testperson",
        entity_type=EntityType.EXECUTIVE,
        match_policy=MatchPolicy.LITERAL,
        priority=100,
    )
    reg.add_alias(
        "ali-006",
        "ent-005",
        "canary-test.invalid",
        entity_type=EntityType.COMPANY_DOMAIN,
        match_policy=MatchPolicy.DOMAIN_FULL,
        priority=100,
    )
    return reg
