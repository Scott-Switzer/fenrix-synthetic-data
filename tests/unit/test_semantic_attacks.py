"""Regression tests for the 4 semantic privacy attacks.

Covers:
- rare phrase attack (distinctive ngram extraction + 9-class taxonomy)
- lexical retrieval attack (BM25 vacuous PASS on single-doc corpus)
- multi-document attack (same BM25 driver)
- structured numeric similarity attack (cosine similarity on
  log-bucketed histograms)
- results_to_report dict shape + rare_phrase top-level shape + overall_verdict rule
"""

from __future__ import annotations

import json
from pathlib import Path

from fenrix_synthetic.attacks.semantic_attacks import (
    SemanticAttackResult,
    lexical_retrieval_attack,
    multi_document_attack,
    rare_phrase_attack,
    results_to_report,
    structured_numeric_similarity_attack,
)

# ── lexical / multi-document / structured numeric ──────────────────────


def test_lexical_retrieval_vacuous_pass_on_single_document() -> None:
    res = lexical_retrieval_attack(
        "annual report on form 10-k for fiscal year ended january",
        {"filing_001.html": "Annual Report on Form 10-K body content here"},
        top_k=10,
    )
    assert res.attack_type == "lexical_retrieval"
    assert res.passed is True
    assert res.status == "PASS"
    assert res.details.get("is_vacuous_pass") is True
    assert res.details.get("source_rank") == 1
    assert res.details.get("top_10_hit") is True


def test_lexical_retrieval_empty_corpus_is_not_run() -> None:
    res = lexical_retrieval_attack("any query text here", {}, top_k=10)
    assert res.passed is False
    assert res.status == "FAIL"
    assert res.details.get("error") == "empty_corpus"
    assert res.details.get("source_rank") == -1


def test_multi_document_vacuous_pass_on_single_document() -> None:
    res = multi_document_attack(
        "all masked surrogates concatenated into one query",
        {"filing_001.html": "Annual Report on Form 10-K body content"},
        top_k=10,
    )
    assert res.attack_type == "multi_document"
    assert res.passed is True
    assert res.details.get("vacuous_pass_reason") == "single_document_corpus"


def test_results_to_report_all_pass_overall_pass() -> None:
    results = {
        "rare_phrase": SemanticAttackResult(
            attack_type="rare_phrase",
            passed=True,
            details={
                "surviving_phrase_count": 0,
                "blocking_surviving_phrase_count": 0,
                "nonblocking_phrase_count": 0,
                "counts_by_class": {},
                "top_redacted_blocking_phrase_hashes": [],
                "top_redacted_nonblocking_phrase_hashes": [],
                "top_redacted_phrase_hashes": [],
            },
        ),
        "lexical_retrieval": SemanticAttackResult(
            attack_type="lexical_retrieval",
            passed=True,
            details={"source_rank": 1, "is_vacuous_pass": True},
        ),
        "multi_document": SemanticAttackResult(
            attack_type="multi_document",
            passed=True,
            details={"source_rank": 1, "is_vacuous_pass": True},
        ),
        "structured_numeric_similarity": SemanticAttackResult(
            attack_type="structured_numeric_similarity",
            passed=True,
            details={"max_similarity": 0.10},
        ),
    }
    report = results_to_report(results, "syn_426219f2")
    assert report["overall_verdict"] == "PASS"
    assert report["ticker"] == "syn_426219f2"
    for key in (
        "rare_phrase",
        "lexical_retrieval",
        "multi_document",
        "structured_numeric_similarity",
    ):
        assert key in report["attacks"]
    assert report["implementation_status"] == "implemented"
    # Top-level rare_phrase shape present with all spec keys.
    rp = report.get("rare_phrase")
    assert rp is not None
    assert rp.get("status") == "PASS"
    assert rp.get("blocking_surviving_phrase_count") == 0
    assert rp.get("nonblocking_phrase_count") == 0
    assert isinstance(rp.get("counts_by_class"), dict)
    assert isinstance(rp.get("top_redacted_blocking_phrase_hashes"), list)
    assert isinstance(rp.get("top_redacted_nonblocking_phrase_hashes"), list)


def test_results_to_report_failure_drives_overall_fail() -> None:
    results = {
        "rare_phrase": SemanticAttackResult(
            attack_type="rare_phrase",
            passed=False,
            details={
                "surviving_phrase_count": 5,
                "blocking_surviving_phrase_count": 3,
                "nonblocking_phrase_count": 2,
                "counts_by_class": {
                    "company_specific_business_phrase": 3,
                    "date_period_boilerplate": 2,
                },
                "top_redacted_blocking_phrase_hashes": ["abc"],
                "top_redacted_nonblocking_phrase_hashes": ["def"],
                "top_redacted_phrase_hashes": ["abc"],
            },
        ),
        "lexical_retrieval": SemanticAttackResult(
            attack_type="lexical_retrieval", passed=True, details={}
        ),
        "multi_document": SemanticAttackResult(
            attack_type="multi_document", passed=True, details={}
        ),
        "structured_numeric_similarity": SemanticAttackResult(
            attack_type="structured_numeric_similarity",
            passed=True,
            details={},
        ),
    }
    report = results_to_report(results, "syn_426219f2")
    assert report["overall_verdict"] == "FAIL"
    rp = report.get("rare_phrase")
    assert rp is not None
    assert rp.get("status") == "FAIL"
    assert rp.get("blocking_surviving_phrase_count") == 3
    assert rp.get("nonblocking_phrase_count") == 2
    assert rp.get("counts_by_class", {}).get("company_specific_business_phrase") == 3


def test_results_to_report_status_normalisation_to_pass_fail() -> None:
    res = SemanticAttackResult(attack_type="rare_phrase", passed=True)
    assert res.status == "PASS"
    assert res.verdict == "PASS"
    res2 = SemanticAttackResult(attack_type="rare_phrase", passed=False)
    assert res2.status == "FAIL"
    assert res2.verdict == "FAIL"


def test_structured_numeric_no_data_returns_pass(tmp_path: Path) -> None:
    res = structured_numeric_similarity_attack(
        public_numeric_dir=tmp_path,
        source_run=tmp_path,
        ticker="NVDA",
    )
    assert res.attack_type == "structured_numeric_similarity"
    assert res.passed is True
    assert res.details.get("no_data_pass") is True


def test_structured_numeric_different_distributions_pass(tmp_path: Path) -> None:
    pub_root = tmp_path / "public_root" / "NVDA" / "classroom_safe"
    pub_root.mkdir(parents=True)
    public_payload = {
        "ticker_seed": "SYNTH_AX",
        "statements": [
            {
                "revenue": 100.0 + i,
                "net_income": 50.0 + i,
                "total_assets": 1000.0 + 2 * i,
            }
            for i in range(5)
        ],
    }
    (pub_root / "annual_statements.json").write_text(json.dumps(public_payload))
    src_root = tmp_path / "src_root"
    (src_root / "originals" / "NVDA" / "sec").mkdir(parents=True)
    sec_html = (
        "Annual revenue was $50.0 billion in fiscal 2024. "
        "Total assets stand at $200.0 billion. "
        "Net income grew 15% year over year."
    )
    (src_root / "originals" / "NVDA" / "sec" / "filing.html").write_text(sec_html)
    res = structured_numeric_similarity_attack(
        public_numeric_dir=pub_root,
        source_run=src_root,
        ticker="NVDA",
        similarity_threshold=0.95,
    )
    assert res.passed is True
    assert res.details["public_numeric_values"] >= 1
    assert res.details["source_numeric_values"] >= 1


# ── Rare phrase taxonomy tests ─────────────────────────────────────────


def test_rare_phrase_detects_capitalised_ngrams() -> None:
    masked = [("doc1", "The Annual Report on Form 10-K was filed with regulators.")]
    private: dict[str, list[str]] = {"names": ["John Doe"]}
    res = rare_phrase_attack(masked, private, min_ngram=3, max_ngram=7)
    assert res.attack_type == "rare_phrase"
    assert res.details.get("surviving_phrase_count", 0) >= 1
    assert len(res.details.get("top_redacted_phrase_hashes", [])) >= 1


def test_rare_phrase_excludes_strings_in_private_values() -> None:
    masked = [("doc1", "John Doe announced the new graphics product line.")]
    private: dict[str, list[str]] = {"names": ["john doe"]}
    res = rare_phrase_attack(masked, private, min_ngram=2, max_ngram=4)
    surviving_phrases = {h.phrase.lower() for h in res.hits}
    assert "john doe" not in surviving_phrases


def test_rare_phrase_lowercase_only_phrases_excluded() -> None:
    masked = [("doc1", "the quick brown fox jumps over the lazy dog fox jumps.")]
    private: dict[str, list[str]] = {}
    res = rare_phrase_attack(masked, private, min_ngram=3, max_ngram=7)
    assert res.details.get("surviving_phrase_count", 0) == 0


# ── Taxonomy classification: non-blocking classes ─────────────────────


def test_rare_phrase_classifies_sec_xbrl_boilerplate_as_nonblocking() -> None:
    """``cik 002 us-gaap`` → sec_xbrl_boilerplate, NOT blocking."""
    masked_docs = [
        (
            f"doc{i}",
            f"<html><body>CIK 002 US-GAAP filing {i} details ok.</body></html>",
        )
        for i in range(25)
    ]
    private: dict[str, list[str]] = {
        "names": [],
        "ticker": [],
        "company_name": [],
    }
    res = rare_phrase_attack(masked_docs, private, min_ngram=3, max_ngram=3)
    counts = res.details.get("counts_by_class", {})
    assert counts.get("sec_xbrl_boilerplate", 0) >= 1, f"got {counts}"
    assert res.details.get("blocking_surviving_phrase_count", -1) == 0
    assert res.passed is True


def test_rare_phrase_classifies_date_period_boilerplate_as_nonblocking() -> None:
    """Sec date/period phrases → date_period_boilerplate, NOT blocking.

    Fixture tokens must carry capitalisation so ``_distinctive_ngrams``
    extracts phrases (the heuristic requires >=1 cap-bearing word).
    """
    masked_docs = [
        (
            f"doc{i}",
            "As of April the Fiscal Year ended on January 29 2023.",
        )
        for i in range(5)
    ]
    private: dict[str, list[str]] = {
        "names": [],
        "ticker": [],
        "company_name": [],
    }
    res = rare_phrase_attack(masked_docs, private, min_ngram=3, max_ngram=4)
    counts = res.details.get("counts_by_class", {})
    assert counts.get("date_period_boilerplate", 0) >= 1, f"got {counts}"
    assert res.details.get("blocking_surviving_phrase_count", -1) == 0
    assert res.passed is True


def test_rare_phrase_classifies_accounting_boilerplate_as_nonblocking() -> None:
    """``Consolidated Financial Statements`` → accounting, NOT blocking."""
    masked_docs = [
        (
            f"doc{i}",
            f"The Consolidated Financial Statements for fiscal {2020 + i}.",
        )
        for i in range(20)
    ]
    private: dict[str, list[str]] = {
        "names": [],
        "ticker": [],
        "company_name": [],
    }
    res = rare_phrase_attack(masked_docs, private, min_ngram=3, max_ngram=3)
    counts = res.details.get("counts_by_class", {})
    assert counts.get("accounting_taxonomy_boilerplate", 0) >= 1, f"got {counts}"
    assert res.passed is True


def test_rare_phrase_classifies_synthetic_placeholder_as_nonblocking() -> None:
    """``Company 001`` / ``syn_<hash>`` → synthetic_placeholder, NOT blocking."""
    masked_docs = [
        (
            f"doc{i}",
            "Company 001 announced syn_deadbeef Version for ops.",
        )
        for i in range(5)
    ]
    private: dict[str, list[str]] = {
        "names": [],
        "ticker": [],
        "company_name": [],
    }
    res = rare_phrase_attack(masked_docs, private, min_ngram=3, max_ngram=3)
    counts = res.details.get("counts_by_class", {})
    assert counts.get("synthetic_placeholder", 0) >= 1, f"got {counts}"
    assert res.passed is True, (
        f"All fixture phrases should be non-blocking, "
        f"blocking={res.details.get('blocking_surviving_phrase_count')}, "
        f"counts={counts}"
    )


# ── Taxonomy classification: blocking classes ─────────────────────────


def test_rare_phrase_blocks_product_or_platform_phrase() -> None:
    """Product registry match → product_or_platform_phrase BLOCKING."""
    masked_docs = [
        (
            "doc1",
            "The Product-A Architecture accelerates AI inference 10x.",
        )
    ]
    private: dict[str, list[str]] = {
        "names": [],
        "ticker": [],
        "company_name": [],
        "product": ["Product-A"],
    }
    res = rare_phrase_attack(masked_docs, private, min_ngram=3, max_ngram=3)
    counts = res.details.get("counts_by_class", {})
    assert counts.get("product_or_platform_phrase", 0) >= 1, f"got {counts}"
    assert res.details.get("blocking_surviving_phrase_count", 0) >= 1
    assert res.passed is False


def test_rare_phrase_blocks_raw_direct_identifier() -> None:
    """Private-value match → raw_direct_identifier BLOCKING."""
    masked_docs = [
        ("doc1", "Smith Holdings announced its Smith Holdings Platform."),
    ]
    private: dict[str, list[str]] = {
        "names": ["Smith Holdings", "Smith Holdings Platform"],
        "ticker": [],
        "company_name": [],
    }
    res = rare_phrase_attack(masked_docs, private, min_ngram=3, max_ngram=4)
    assert res.passed is False
    assert res.details.get("blocking_surviving_phrase_count", 0) >= 1
    counts = res.details.get("counts_by_class", {})
    assert counts.get("raw_direct_identifier", 0) >= 1, f"got {counts}"


def test_rare_phrase_blocks_company_specific_business_phrase() -> None:
    """Distinctive business phrase with no recipe match → blocking fallback."""
    masked_docs = [
        (
            "doc1",
            "The new data center GPU architecture accelerates AI workloads.",
        )
    ]
    private: dict[str, list[str]] = {
        "names": [],
        "ticker": [],
        "company_name": [],
    }
    res = rare_phrase_attack(masked_docs, private, min_ngram=3, max_ngram=3)
    counts = res.details.get("counts_by_class", {})
    assert counts.get("company_specific_business_phrase", 0) >= 1, f"got {counts}"
    assert res.details.get("blocking_surviving_phrase_count", 0) >= 1
    assert res.passed is False


# ── Over-classification safety ────────────────────────────────────────


def test_rare_phrase_does_not_overclassify_dates_in_product_names() -> None:
    """Negative control: product-name n-gram routes BEFORE date tier.

    ``April 2025 Acquisition`` registered as a product name must route
    to ``product_or_platform_phrase`` (BLOCKING, Tier 2), even though
    ``re.search` date patterns would match ``april 2025`` inside it.
    Other n-grams like ``the april 2025`` legitimately route to
    ``date_period_boilerplate`` (non-blocking).
    """
    masked_docs = [
        ("doc1", "The April 2025 Acquisition closed successfully."),
    ]
    private: dict[str, list[str]] = {
        "names": [],
        "ticker": [],
        "company_name": [],
        "product": ["April 2025 Acquisition"],
    }
    res = rare_phrase_attack(masked_docs, private, min_ngram=3, max_ngram=3)
    counts = res.details.get("counts_by_class", {})
    # The product n-gram must route to product_or_platform_phrase (BLOCKING).
    assert counts.get("product_or_platform_phrase", 0) >= 1, f"got {counts}"
    # Other n-grams like "the april 2025" MAY hit date_period_boilerplate.
    # That is correct — broad re.search date patterns legitimately catch
    # pure-date n-grams.  The key invariant is that the product-NAME n-gram
    # itself is NOT classified as date_period_boilerplate.
    assert res.passed is False  # BLOCKING classes survive
