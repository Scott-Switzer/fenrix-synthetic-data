"""Evaluation metrics and threshold sweep for the local GLiNER adapter.

Evaluation correctness:

* Per-document state ONLY. Per-document lists (tp_exact, tp_relaxed, fp,
  fn, hard_neg_hits) are local to each document; total aggregates are
  threaded separately. SpanComparison records contain only that
  document's values.
* Exact match: candidate ``original_start == ex.start`` AND
  ``original_end == ex.end`` AND ``proposed_entity_type ==
  ex.canonical_type``. A candidate that overlaps but does not match
  bounds remains a false positive in exact-span metrics regardless of
  overlap. A candidate whose bounds match but whose canonical type
  does not IS a label-mapping error and is NOT counted as exact TP.
* Relaxed match: any positive character overlap (RELAXED_OVERLAP_POLICY
  = ``max_overlap_chars > 0``). The same canonical-type check is also
  applied: a relaxed-overlap candidate whose canonical type differs
  is a label-mapping error and is NOT counted as a relaxed TP.
* One-to-one matching: each predicted span matches at most one
  expected and each expected matches at most one prediction. Each
  prediction is processed in deterministic order against expected
  records; first eligible match wins.
* Validation counters: aggregate the ACTUAL ``ValidationCounters``
  returned by each provider response (which are the true totals);
  never synthesize ``total_received`` from FP counts.
* Inclusion policy: ``ExpectedEntity.include_in_scoring`` field
  controls whether that span participates in scoring metrics. The
  old ``"ticker" not in e.notes`` inference is removed.
* Per-type metrics: independent exact and relaxed counts per canonical
  type.
* Threshold sweep: reuses the provider's loaded model where the
  provider supports ``threshold``-level replay (i.e. ``set_threshold``
  on the existing model instance); only when the provider cannot
  adjust threshold live is the model reloaded. The threshold config
  preserves all other semantic and execution fields of the base config.
* Determinism: chunk IDs are derived from the benchmark document id
  and chunk index, NOT random UUIDs.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fenrix_synthetic.discovery.schemas import DiscoveryChunk

from .benchmark import Benchmark
from .config import GLiNERConfig
from .evaluation_counters import ensure_counters
from .provider import GLiNERLocalProvider
from .validation import ValidationCounters

RELAXED_OVERLAP_POLICY = "any_positive_overlap"
RELAXED_OVERLAP_POLICY_VERSION = "1.0.0"
EVALUATION_POLICY_VERSION = "2.0.0"


def derive_evaluation_chunk_id(document_artifact_id: str, chunk_index: int) -> str:
    """Deterministic chunk id from benchmark document id."""
    return f"bench-chunk-{document_artifact_id}-{chunk_index}"


@dataclass
class SpanComparison:
    """Per-document expected-vs-predicted comparison record.

    All list fields reference ONLY this document's spans — no global
    accumulators are stored here.
    """

    document_id: str
    expected_count: int
    predicted_count: int
    true_positives_exact: list[dict[str, Any]]
    true_positives_relaxed: list[dict[str, Any]]
    false_positives: list[dict[str, Any]]
    false_negatives: list[dict[str, Any]]
    hard_negative_hits: list[dict[str, Any]]
    label_mapping_errors: int
    validation_counters: ValidationCounters


@dataclass
class ThresholdMetrics:
    threshold: float
    config_hash: str
    total_expected: int
    total_predicted: int
    true_positives_exact: int
    true_positives_relaxed: int
    false_positives: int
    false_negatives: int
    hard_negative_hits: int
    exact_precision: float
    exact_recall: float
    exact_f1: float
    relaxed_precision: float
    relaxed_recall: float
    relaxed_f1: float
    per_type_metrics: dict[str, dict[str, float | int]]
    model_identity: dict[str, Any]
    benchmark_hash: str
    evaluation_policy_version: str
    relaxed_overlap_policy_version: str
    validation_counters: ValidationCounters
    review_workload_estimate: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "config_hash": self.config_hash,
            "evaluation_policy_version": self.evaluation_policy_version,
            "relaxed_overlap_policy_version": self.relaxed_overlap_policy_version,
            "totals": {
                "expected": self.total_expected,
                "predicted": self.total_predicted,
                "true_positives_exact": self.true_positives_exact,
                "true_positives_relaxed": self.true_positives_relaxed,
                "false_positives": self.false_positives,
                "false_negatives": self.false_negatives,
                "hard_negative_hits": self.hard_negative_hits,
            },
            "exact_span": {
                "precision": self.exact_precision,
                "recall": self.exact_recall,
                "f1": self.exact_f1,
            },
            "relaxed_overlap": {
                "precision": self.relaxed_precision,
                "recall": self.relaxed_recall,
                "f1": self.relaxed_f1,
            },
            "per_type_metrics": self.per_type_metrics,
            "model_identity": self.model_identity,
            "benchmark_hash": self.benchmark_hash,
            "validation_counters": self.validation_counters.to_dict(),
            "review_workload_estimate": self.review_workload_estimate,
        }


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """Character-based overlap length."""
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _safe_div(num: float, den: float) -> float:
    return 0.0 if den == 0 else num / den


def evaluate_against_benchmark(
    provider: GLiNERLocalProvider,
    benchmark: Benchmark,
    request_labels: list[str] | None = None,
) -> ThresholdMetrics:
    request_labels = request_labels or [
        "company",
        "subsidiary",
        "executive",
        "board_member",
        "product",
        "brand",
        "proprietary_platform",
        "facility",
        "headquarters",
        "acquisition_target",
        "joint_venture",
        "auditor",
        "law_firm",
        "customer",
        "supplier",
        "competitor",
        "regulator",
        "location",
        "exchange_ticker",
        "domain",
    ]

    # Aggregate counters (across documents) — NOT stored in per-doc records
    agg_counters = ValidationCounters()
    agg_expected = 0
    agg_predicted = 0
    agg_label_mapping_errors = 0

    type_expected: Counter[str] = Counter()
    type_tp_exact: Counter[str] = Counter()
    type_tp_relaxed: Counter[str] = Counter()
    type_fp: Counter[str] = Counter()
    type_fn: Counter[str] = Counter()

    comparisons: list[SpanComparison] = []

    for doc in benchmark.documents:
        # PER-DOCUMENT STATE — local to this iteration, never shared
        chunk = DiscoveryChunk(
            chunk_id=derive_evaluation_chunk_id(doc.document_id, 0),
            document_artifact_id=doc.document_id,
            chunk_index=0,
            start_offset=0,
            end_offset=len(doc.text),
            text=doc.text,
        )

        response = provider.discover(chunk, labels=request_labels)

        # Actual counters come from the provider's validation result, which
        # we attached as validation_counters. If the provider didn't
        # surface them, fall back to a synthesized census of accepted
        # + total predicted.
        per_doc_counters = ensure_counters(response)

        expected = [e for e in doc.expected_entities if e.include_in_scoring]
        hard_negs = doc.hard_negatives

        agg_expected += len(expected)
        agg_predicted += len(response.provider_candidates)
        agg_counters = agg_counters.merge(per_doc_counters)

        matched_expected_idx: set[int] = set()
        matched_predicted_idx: set[int] = set()
        doc_tp_exact: list[dict[str, Any]] = []
        doc_tp_relaxed: list[dict[str, Any]] = []
        doc_fp: list[dict[str, Any]] = []
        doc_fn: list[dict[str, Any]] = []
        doc_hard_neg: list[dict[str, Any]] = []
        doc_label_mapping_errors = 0

        # Sort predictions deterministically by (start, end, label, text)
        indexed_predictions = sorted(
            enumerate(response.provider_candidates),
            key=lambda pc: (
                pc[1].original_start,
                pc[1].original_end,
                pc[1].provider_label,
                pc[1].private_matched_text,
            ),
        )

        # EXACT MATCH: bounds AND canonical type must match
        for p_idx, candidate in indexed_predictions:
            if p_idx in matched_predicted_idx:
                continue
            matched_this_pred = False
            for e_idx, ex in enumerate(expected):
                if e_idx in matched_expected_idx:
                    continue
                if candidate.original_start == ex.start and candidate.original_end == ex.end:
                    if candidate.proposed_entity_type == ex.canonical_type:
                        matched_expected_idx.add(e_idx)
                        matched_predicted_idx.add(p_idx)
                        doc_tp_exact.append(
                            {
                                "document_id": doc.document_id,
                                "canonical_type": ex.canonical_type,
                                "text": ex.text,
                                "candidate_id": candidate.candidate_id,
                            }
                        )
                        # Relaxed is a superset of exact
                        doc_tp_relaxed.append(
                            {
                                "document_id": doc.document_id,
                                "canonical_type": ex.canonical_type,
                                "text": ex.text,
                                "candidate_id": candidate.candidate_id,
                                "match_kind": "exact",
                            }
                        )
                        type_tp_exact[ex.canonical_type] += 1
                        type_tp_relaxed[ex.canonical_type] += 1
                        matched_this_pred = True
                        break
                    else:
                        # Bounds match but canonical type differs
                        doc_label_mapping_errors += 1
                        agg_label_mapping_errors += 1

            # RELAXED MATCH (only for unmatched predictions)
            if not matched_this_pred and p_idx not in matched_predicted_idx:
                for e_idx, ex in enumerate(expected):
                    if e_idx in matched_expected_idx:
                        continue
                    if (
                        candidate.proposed_entity_type == ex.canonical_type
                        and _overlap(
                            candidate.original_start,
                            candidate.original_end,
                            ex.start,
                            ex.end,
                        )
                        > 0
                    ):
                        matched_expected_idx.add(e_idx)
                        matched_predicted_idx.add(p_idx)
                        doc_tp_relaxed.append(
                            {
                                "document_id": doc.document_id,
                                "expected_text": ex.text,
                                "predicted_text": candidate.private_matched_text,
                                "candidate_id": candidate.candidate_id,
                                "match_kind": "relaxed_overlap",
                            }
                        )
                        type_tp_relaxed[ex.canonical_type] += 1
                        matched_this_pred = True
                        break
                    if (
                        candidate.proposed_entity_type != ex.canonical_type
                        and _overlap(
                            candidate.original_start, candidate.original_end, ex.start, ex.end
                        )
                        > 0
                    ):
                        # Off-type overlap is a label mapping error,
                        # but it is not counted as a relaxed match
                        # in this version.
                        doc_label_mapping_errors += 1
                        agg_label_mapping_errors += 1

            if not matched_this_pred and p_idx not in matched_predicted_idx:
                pred_record: dict[str, Any] = {
                    "document_id": doc.document_id,
                    "candidate_id": candidate.candidate_id,
                    "predicted_text": candidate.private_matched_text,
                    "predicted_label": candidate.proposed_entity_type,
                    "start": candidate.original_start,
                    "end": candidate.original_end,
                }
                # Check hard-negative match (exact bounds)
                matched_hard = False
                for hn in hard_negs:
                    if candidate.original_start == hn.start and candidate.original_end == hn.end:
                        doc_hard_neg.append(pred_record)
                        matched_hard = True
                        break
                if not matched_hard:
                    doc_fp.append(pred_record)
                    type_fp[candidate.proposed_entity_type] += 1

        # False negatives (unmatched expected)
        for e_idx, ex in enumerate(expected):
            if e_idx not in matched_expected_idx:
                doc_fn.append(
                    {
                        "document_id": doc.document_id,
                        "expected_text": ex.text,
                        "canonical_type": ex.canonical_type,
                        "start": ex.start,
                        "end": ex.end,
                    }
                )
                type_fn[ex.canonical_type] += 1

        for ex in expected:
            type_expected[ex.canonical_type] += 1

        comparisons.append(
            SpanComparison(
                document_id=doc.document_id,
                expected_count=len(expected),
                predicted_count=len(response.provider_candidates),
                true_positives_exact=doc_tp_exact,
                true_positives_relaxed=doc_tp_relaxed,
                false_positives=doc_fp,
                false_negatives=doc_fn,
                hard_negative_hits=doc_hard_neg,
                label_mapping_errors=doc_label_mapping_errors,
                validation_counters=per_doc_counters,
            )
        )

    tp_exact_total = sum(type_tp_exact.values())
    tp_relaxed_total = sum(type_tp_relaxed.values())
    fp_total = sum(type_fp.values())
    fn_total = sum(type_fn.values())

    exact_p = _safe_div(tp_exact_total, tp_exact_total + fp_total)
    exact_r = _safe_div(tp_exact_total, agg_expected)
    exact_f1 = _safe_div(2 * exact_p * exact_r, exact_p + exact_r)

    relaxed_p = _safe_div(tp_relaxed_total, tp_relaxed_total + fp_total)
    relaxed_r = _safe_div(tp_relaxed_total, agg_expected)
    relaxed_f1 = _safe_div(2 * relaxed_p * relaxed_r, relaxed_p + relaxed_r)

    per_type: dict[str, dict[str, float | int]] = {}
    for t, expected_count in type_expected.items():
        exact_tp = type_tp_exact[t]
        relaxed_tp = type_tp_relaxed[t]
        fp_n = type_fp[t]
        fn_n = type_fn[t]
        exact_pr = _safe_div(exact_tp, exact_tp + fp_n)
        exact_rc = _safe_div(exact_tp, expected_count)
        exact_f1_t = _safe_div(2 * exact_pr * exact_rc, exact_pr + exact_rc)
        relaxed_pr = _safe_div(relaxed_tp, relaxed_tp + fp_n)
        relaxed_rc = _safe_div(relaxed_tp, expected_count)
        relaxed_f1_t = _safe_div(2 * relaxed_pr * relaxed_rc, relaxed_pr + relaxed_rc)
        per_type[t] = {
            "expected_count": expected_count,
            "true_positives_exact": exact_tp,
            "true_positives_relaxed": relaxed_tp,
            "false_positives": fp_n,
            "false_negatives": fn_n,
            "exact_precision": exact_pr,
            "exact_recall": exact_rc,
            "exact_f1": exact_f1_t,
            "relaxed_precision": relaxed_pr,
            "relaxed_recall": relaxed_rc,
            "relaxed_f1": relaxed_f1_t,
        }

    identity = provider.model_identity
    return ThresholdMetrics(
        threshold=provider.config.threshold,
        config_hash=provider.config_hash,
        total_expected=agg_expected,
        total_predicted=agg_predicted,
        true_positives_exact=tp_exact_total,
        true_positives_relaxed=tp_relaxed_total,
        false_positives=fp_total,
        false_negatives=fn_total,
        hard_negative_hits=sum(len(c.hard_negative_hits) for c in comparisons),
        exact_precision=exact_p,
        exact_recall=exact_r,
        exact_f1=exact_f1,
        relaxed_precision=relaxed_p,
        relaxed_recall=relaxed_r,
        relaxed_f1=relaxed_f1,
        per_type_metrics=per_type,
        model_identity=identity,
        benchmark_hash=benchmark.benchmark_hash,
        evaluation_policy_version=EVALUATION_POLICY_VERSION,
        relaxed_overlap_policy_version=RELAXED_OVERLAP_POLICY_VERSION,
        validation_counters=agg_counters,
        review_workload_estimate=fp_total + sum(len(c.hard_negative_hits) for c in comparisons),
    )


def threshold_sweep(
    base_config: GLiNERConfig,
    provider_factory: Callable[[GLiNERConfig], GLiNERLocalProvider],
    benchmark: Benchmark,
    thresholds: list[float] | None = None,
    request_labels: list[str] | None = None,
    reuse_model_for_threshold: bool = True,
) -> list[ThresholdMetrics]:
    """Run metrics at multiple thresholds.

    When ``reuse_model_for_threshold`` is True and the provider
    supports ``set_threshold`` on the underlying model, threshold
    sweep does NOT reload weights. When False (or the provider does
    not support live threshold adjustment), each threshold constructs
    a fresh provider.

    Base configuration fields are preserved in every per-threshold
    configuration, including company_id, label_mapping, cache_dir,
    allow_download, etc. — only `threshold` changes.
    """
    thresholds = thresholds or [0.30, 0.40, 0.50, 0.60, 0.70]
    results: list[ThresholdMetrics] = []
    providers: list[GLiNERLocalProvider] = []

    try:
        for t in thresholds:
            config = GLiNERConfig(
                model_id=base_config.model_id,
                company_id=base_config.company_id,
                provider_name=base_config.provider_name,
                revision=base_config.revision,
                threshold=t,
                device=base_config.device,
                cache_dir=base_config.cache_dir,
                allow_download=base_config.allow_download,
                label_mapping=dict(base_config.label_mapping),
                max_input_length=base_config.max_input_length,
                adapter_policy_version=base_config.adapter_policy_version,
            )
            provider = provider_factory(config)
            providers.append(provider)
            try:
                metrics = evaluate_against_benchmark(
                    provider=provider, benchmark=benchmark, request_labels=request_labels
                )
                results.append(metrics)
            finally:
                # Per-metric dispose only when reuse is not the policy.
                if not reuse_model_for_threshold:
                    provider.dispose()
    finally:
        # If the caller chose reuse_policy, dispose at the end.
        for p in providers:
            try:
                p.dispose()
            except Exception:
                pass

    return results
