"""Evaluation metrics and threshold sweep for the local GLiNER adapter."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .benchmark import Benchmark
from .config import GLiNERConfig
from .provider import GLiNERLocalProvider
from .validation import ValidationCounters


@dataclass
class SpanComparison:
    """One expected-vs-predicted pair for a benchmark document."""

    document_id: str
    expected_count: int
    predicted_count: int
    true_positives: list[dict[str, Any]]
    false_positives: list[dict[str, Any]]
    false_negatives: list[dict[str, Any]]
    hard_negative_hits: list[dict[str, Any]]
    label_mapping_errors: int
    malformed_rejected: int


@dataclass
class ThresholdMetrics:
    threshold: float
    config_hash: str
    total_expected: int
    total_predicted: int
    true_positives: int
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
    validation_counters: dict[str, int]
    review_workload_estimate: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "config_hash": self.config_hash,
            "totals": {
                "expected": self.total_expected,
                "predicted": self.total_predicted,
                "true_positives": self.true_positives,
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
            "validation_counters": self.validation_counters,
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
    validation_counters_aggregate: ValidationCounters | None = None,
) -> ThresholdMetrics:
    del validation_counters_aggregate  # kept for API parity
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
    tp_exact: list[dict[str, Any]] = []
    tp_relaxed: list[dict[str, Any]] = []
    fp: list[dict[str, Any]] = []
    fn: list[dict[str, Any]] = []
    hard_neg_hits: list[dict[str, Any]] = []
    label_mapping_errors = 0
    predicted_count_total = 0
    expected_count_total = 0
    type_counts: Counter[str] = Counter()
    type_tp: Counter[str] = Counter()
    type_fp: Counter[str] = Counter()
    type_fn: Counter[str] = Counter()

    comparisons: list[SpanComparison] = []

    for doc in benchmark.documents:
        from ...schemas import DiscoveryChunk

        chunk = DiscoveryChunk(
            chunk_id=f"bench-chunk-{uuid.uuid4().hex[:8]}",
            document_artifact_id=doc.document_id,
            chunk_index=0,
            start_offset=0,
            end_offset=len(doc.text),
            text=doc.text,
        )

        response = provider.discover(chunk, labels=request_labels)
        expected = [
            e
            for e in doc.expected_entities
            if e.blocking or "ticker" not in e.notes  # include both blocking and blocking_reference
        ]
        hard_negs = doc.hard_negatives

        expected_count_total += len(expected)
        predicted_count_total += len(response.provider_candidates)

        matched_expected_idx: set[int] = set()
        matched_predicted_idx: set[int] = set()

        for p_idx, candidate in enumerate(response.provider_candidates):
            for e_idx, ex in enumerate(expected):
                if e_idx in matched_expected_idx:
                    continue
                if candidate.original_start == ex.start and candidate.original_end == ex.end:
                    matched_expected_idx.add(e_idx)
                    matched_predicted_idx.add(p_idx)
                    rec = {
                        "document_id": doc.document_id,
                        "text": ex.text,
                        "canonical_type": ex.canonical_type,
                    }
                    tp_exact.append(rec)
                    tp_relaxed.append(rec)
                    type_tp[ex.canonical_type] += 1
                    if candidate.proposed_entity_type != ex.canonical_type:
                        label_mapping_errors += 1
                    break

        for p_idx, candidate in enumerate(response.provider_candidates):
            if p_idx in matched_predicted_idx:
                continue
            for e_idx, ex in enumerate(expected):
                if e_idx in matched_expected_idx:
                    continue
                if (
                    _overlap(
                        candidate.original_start,
                        candidate.original_end,
                        ex.start,
                        ex.end,
                    )
                    > 0
                ):
                    matched_expected_idx.add(e_idx)
                    matched_predicted_idx.add(p_idx)
                    rec = {
                        "document_id": doc.document_id,
                        "expected_text": ex.text,
                        "predicted_text": candidate.private_matched_text,
                        "predicted_label": candidate.proposed_entity_type,
                    }
                    tp_relaxed.append(rec)
                    type_tp[ex.canonical_type] += 1
                    if candidate.proposed_entity_type != ex.canonical_type:
                        label_mapping_errors += 1
                    break

            if p_idx not in matched_predicted_idx:
                rec = {
                    "document_id": doc.document_id,
                    "predicted_text": candidate.private_matched_text,
                    "predicted_label": candidate.proposed_entity_type,
                    "start": candidate.original_start,
                    "end": candidate.original_end,
                }
                for hn in hard_negs:
                    if candidate.original_start == hn.start and candidate.original_end == hn.end:
                        hard_neg_hits.append(rec)
                        break
                else:
                    fp.append(rec)
                    type_fp[candidate.proposed_entity_type] += 1

        for e_idx, ex in enumerate(expected):
            if e_idx not in matched_expected_idx:
                fn.append(
                    {
                        "document_id": doc.document_id,
                        "expected_text": ex.text,
                        "canonical_type": ex.canonical_type,
                        "start": ex.start,
                        "end": ex.end,
                    }
                )
                type_fn[ex.canonical_type] += 1

        comparisons.append(
            SpanComparison(
                document_id=doc.document_id,
                expected_count=len(expected),
                predicted_count=len(response.provider_candidates),
                true_positives=tp_relaxed,
                false_positives=fp,
                false_negatives=fn,
                hard_negative_hits=hard_neg_hits,
                label_mapping_errors=label_mapping_errors,
                malformed_rejected=0,
            )
        )

        for ex in expected:
            type_counts[ex.canonical_type] += 1

    exact_p = _safe_div(len(tp_exact), len(tp_exact) + len(fp))
    exact_r = _safe_div(len(tp_exact), expected_count_total)
    exact_f1 = _safe_div(2 * exact_p * exact_r, exact_p + exact_r)

    relaxed_p = _safe_div(len(tp_relaxed), len(tp_relaxed) + len(fp))
    relaxed_r = _safe_div(len(tp_relaxed), expected_count_total)
    relaxed_f1 = _safe_div(2 * relaxed_p * relaxed_r, relaxed_p + relaxed_r)

    per_type: dict[str, dict[str, float | int]] = {}
    for t, expected_count in type_counts.items():
        tp_n = type_tp[t]
        fp_n = type_fp[t]
        fn_n = type_fn[t]
        precision = _safe_div(tp_n, tp_n + fp_n)
        recall = _safe_div(tp_n, expected_count)
        f1 = _safe_div(2 * precision * recall, precision + recall)
        per_type_entry: dict[str, float | int] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "expected_count": expected_count,
            "true_positives": tp_n,
            "false_positives": fp_n,
            "false_negatives": fn_n,
        }
        per_type[t] = per_type_entry

    identity = provider.model_identity
    return ThresholdMetrics(
        threshold=provider.config.threshold,
        config_hash=provider.config_hash,
        total_expected=expected_count_total,
        total_predicted=predicted_count_total,
        true_positives=len(tp_exact),
        false_positives=len(fp),
        false_negatives=len(fn),
        hard_negative_hits=len(hard_neg_hits),
        exact_precision=exact_p,
        exact_recall=exact_r,
        exact_f1=exact_f1,
        relaxed_precision=relaxed_p,
        relaxed_recall=relaxed_r,
        relaxed_f1=relaxed_f1,
        per_type_metrics=per_type,
        model_identity=identity,
        benchmark_hash=benchmark.benchmark_hash,
        validation_counters={"total_received": predicted_count_total + len(fp)},
        review_workload_estimate=len(fp) + len(hard_neg_hits),
    )


def threshold_sweep(
    base_config: GLiNERConfig,
    provider_factory: Any,
    benchmark: Benchmark,
    thresholds: list[float] | None = None,
    request_labels: list[str] | None = None,
) -> list[ThresholdMetrics]:
    """Run metrics at multiple thresholds.

    Args:
        base_config: configuration whose `model_id`, `revision`, `device`,
            `cache_dir`, `company_id`, `label_mapping`, `adapter_policy_version`,
            and `provider_name` are reused for each threshold.
        provider_factory: callable `(GLiNERConfig) -> GLiNERLocalProvider`.
            Must accept a fresh GLiNERConfig per call and may be the
            ``Initialization`` method of a provider if a fixed loader is in use.
        benchmark: benchmark dataset.
        thresholds: list of thresholds to evaluate. Defaults to
            ``[0.30, 0.40, 0.50, 0.60, 0.70]``.
    """
    thresholds = thresholds or [0.30, 0.40, 0.50, 0.60, 0.70]
    results: list[ThresholdMetrics] = []
    for t in thresholds:
        config = GLiNERConfig(
            model_id=base_config.model_id,
            revision=base_config.revision,
            threshold=t,
            device=base_config.device,
            cache_dir=base_config.cache_dir,
            allow_download=base_config.allow_download,
            label_mapping=base_config.label_mapping,
            max_input_length=base_config.max_input_length,
            model_load_timeout_seconds=base_config.model_load_timeout_seconds,
            adapter_policy_version=base_config.adapter_policy_version,
            company_id=base_config.company_id,
            provider_name=base_config.provider_name,
        )
        provider = provider_factory(config)
        try:
            metrics = evaluate_against_benchmark(
                provider=provider,
                benchmark=benchmark,
                request_labels=request_labels,
            )
            results.append(metrics)
        finally:
            provider.dispose()
    return results
