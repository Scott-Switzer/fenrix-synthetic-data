"""Regression tests for the 4 semantic privacy attacks.

Covers:
- rare phrase attack (distinctive ngram extraction + private_value exclusion)
- lexical retrieval attack (BM25 vacuous PASS on single-doc corpus)
- multi-document attack (same BM25 driver)
- structured numeric similarity attack (cosine similarity on
  log-bucketed histograms)
- results_to_report dict shape + overall_verdict rule
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


def test_rare_phrase_detects_capitalised_ngrams() -> None:
    masked = [
        (
            "doc1",
            "The Annual Report on Form 10-K was filed with regulators.",
        )
    ]
    private: dict[str, list[str]] = {"names": ["John Doe"]}
    res = rare_phrase_attack(masked, private, min_ngram=3, max_ngram=7)
    assert res.attack_type == "rare_phrase"
    # The 4-gram "annual report on form" carries capitalisation
    # signals. Result should expose at least one surviving phrase.
    assert res.details.get("surviving_phrase_count", 0) >= 1
    assert len(res.details.get("top_redacted_phrase_hashes", [])) >= 1


def test_rare_phrase_excludes_strings_in_private_values() -> None:
    masked = [("doc1", "John Doe announced the new graphics product line.")]
    private: dict[str, list[str]] = {"names": ["john doe"]}
    res = rare_phrase_attack(masked, private, min_ngram=2, max_ngram=4)
    # "john doe" appears verbatim in private_values, even though it
    # carries no caps. It must be EXCLUDED from surviving phrases.
    surviving_phrases = {h.phrase.lower() for h in res.hits}
    assert "john doe" not in surviving_phrases


def test_rare_phrase_lowercase_only_phrases_excluded() -> None:
    masked = [
        (
            "doc1",
            "the quick brown fox jumps over the lazy dog fox jumps.",
        )
    ]
    private: dict[str, list[str]] = {}
    res = rare_phrase_attack(masked, private, min_ngram=3, max_ngram=7)
    # No cap-bearing words => no distinctive ngrams.
    assert res.details.get("surviving_phrase_count", 0) == 0


def test_results_to_report_all_pass_overall_pass() -> None:
    results = {
        "rare_phrase": SemanticAttackResult(
            attack_type="rare_phrase",
            passed=True,
            details={"surviving_phrase_count": 0, "top_redacted_phrase_hashes": []},
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
    assert "rare_phrase" in report["attacks"]
    assert "lexical_retrieval" in report["attacks"]
    assert "multi_document" in report["attacks"]
    assert "structured_numeric_similarity" in report["attacks"]
    assert report["implementation_status"] == "implemented"


def test_results_to_report_failure_drives_overall_fail() -> None:
    results = {
        "rare_phrase": SemanticAttackResult(
            attack_type="rare_phrase",
            passed=False,
            details={"surviving_phrase_count": 5, "top_redacted_phrase_hashes": ["abc"]},
        ),
        "lexical_retrieval": SemanticAttackResult(
            attack_type="lexical_retrieval",
            passed=True,
            details={},
        ),
        "multi_document": SemanticAttackResult(
            attack_type="multi_document",
            passed=True,
            details={},
        ),
        "structured_numeric_similarity": SemanticAttackResult(
            attack_type="structured_numeric_similarity",
            passed=True,
            details={},
        ),
    }
    report = results_to_report(results, "syn_426219f2")
    # A single failing attack flips overall to FAIL.
    assert report["overall_verdict"] == "FAIL"


def test_results_to_report_status_normalisation_to_pass_fail() -> None:
    res = SemanticAttackResult(
        attack_type="rare_phrase",
        passed=True,
    )
    assert res.status == "PASS"
    assert res.verdict == "PASS"

    res2 = SemanticAttackResult(
        attack_type="rare_phrase",
        passed=False,
    )
    assert res2.status == "FAIL"
    assert res2.verdict == "FAIL"


def test_structured_numeric_no_data_returns_pass(tmp_path: Path) -> None:
    """Empty public + source dirs -> vacuous PASS via ``no_data_pass``."""
    res = structured_numeric_similarity_attack(
        public_numeric_dir=tmp_path,
        source_run=tmp_path,
        ticker="NVDA",
    )
    assert res.attack_type == "structured_numeric_similarity"
    assert res.passed is True
    assert res.details.get("no_data_pass") is True


def test_structured_numeric_different_distributions_pass(tmp_path: Path) -> None:
    """Synthetic public (small magnitudes) vs SEC source (large magnitudes)."""
    pub_root = tmp_path / "public_root" / "NVDA" / "classroom_safe"
    pub_root.mkdir(parents=True)
    public_payload = {
        "ticker_seed": "SYNTH_AX",
        "statements": [
            {"revenue": 100.0 + i, "net_income": 50.0 + i, "total_assets": 1000.0 + 2 * i}
            for i in range(5)
        ],
    }
    (pub_root / "annual_statements.json").write_text(json.dumps(public_payload))

    src_root = tmp_path / "src_root"
    (src_root / "originals" / "NVDA" / "sec").mkdir(parents=True)
    sec_html = (
        "Annual revenue was $50.0 billion in fiscal 2024. "
        "Total assets stand at $200.0 billion. Net income grew 15% year over year."
    )
    (src_root / "originals" / "NVDA" / "sec" / "filing.html").write_text(sec_html)

    res = structured_numeric_similarity_attack(
        public_numeric_dir=pub_root,
        source_run=src_root,
        ticker="NVDA",
        similarity_threshold=0.95,
    )
    # Two completely different distributions -> cosine must be low ->
    # PASS.
    assert res.passed is True
    assert res.details["public_numeric_values"] >= 1
    assert res.details["source_numeric_values"] >= 1
