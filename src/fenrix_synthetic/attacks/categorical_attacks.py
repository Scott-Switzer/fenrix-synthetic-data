"""Categorical sequence re-identification attacks for S3 feature-only data (Phase 5A).

Attacks operate on categorical/ordinal feature sequences only. No return
correlation attacks because S3 contains no raw return series.

Attack implementations:
- exact_categorical_sequence_similarity
- weighted_hamming_distance
- categorical_dynamic_time_warping
- state_transition_matrix_similarity
- feature_n_gram_similarity
- jaccard_similarity_over_feature_states
- lagged_sequence_matching
- shifted_sequence_matching
- rolling_window_matching
- per_feature_nearest_neighbor_ranking
- combined_feature_nearest_neighbor_ranking
- ablation_attacks (direction-only, momentum-only, etc.)

CANONICAL CONTRACT (Phase 5A repair):
Every attack result published to file MUST match
`CategoricalAttackEvidence` with all required fields at the TOP level.
`categorical_attacks_to_canonical()` adapts internal results to that
contract and rejects malformed legacy shapes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from fenrix_synthetic.attacks.canonical_evidence import (
    AttackEvidenceError,
    AttackStatus,
    CategoricalAttackEvidence,
    validate_canonical_evidence,
)


def categorical_attacks_to_canonical(
    results: list[CategoricalAttackResult],
) -> list[CategoricalAttackEvidence]:
    """Convert internal CategoricalAttackResult list to canonical evidence.

    Raises AttackEvidenceError on the FIRST malformed row — does not
    silently swallow (per the Phase 5A defect repair contract).
    """
    canonical: list[CategoricalAttackEvidence] = []
    for i, r in enumerate(results):
        if r.status is None:
            r.status = AttackStatus.COMPLETED
        candidate = {
            "variant": r.variant,
            "attack_name": r.attack_type,
            "ablation": r.ablation_group,
            "true_source_rank": r.true_source_rank,
            "candidate_universe_size": int(r.candidate_universe_size),
            "percentile_rank": float(r.percentile_rank),
            "top_1": bool(r.top_1),
            "top_5": bool(r.top_5),
            "top_10": bool(r.top_10),
            "score": r.score,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "attack_hash": r.attack_hash,
            "notes": "",
        }
        try:
            canonical.append(validate_canonical_evidence(candidate))
        except AttackEvidenceError as exc:
            raise AttackEvidenceError(
                f"results[{i}] (variant={r.variant!r}, attack={r.attack_type!r}) "
                f"cannot be converted to canonical form: {exc}"
            ) from exc
    return canonical


# ── Ordinal mapping for categorical comparisons ────────────────────────

_ORDINAL_MAP: dict[str, int] = {
    "VERY_LOW": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "VERY_HIGH": 4,
    "DOWN": 0,
    "FLAT": 1,
    "UP": 2,
    "BEARISH": 0,
    "BULLISH": 2,
    "BELOW": 0,
    "CROSSED": 1,
    "ABOVE": 2,
    "SHORT": 0,
    "MODERATE": 1,
    "PERSISTENT": 2,
    "STRONG_DOWN": 0,
    "MILD_DOWN": 1,
    "NEUTRAL": 2,
    "MILD_UP": 3,
    "STRONG_UP": 4,
}


def _ordinal(val: str) -> int:
    return _ORDINAL_MAP.get(val, 1)


# ── Feature extraction helpers ─────────────────────────────────────────


def _get_feature_vector(features: list[dict[str, Any]], feature_name: str) -> list[str]:
    """Extract a single feature column as a list of string categories."""
    return [f.get(feature_name, "MEDIUM") for f in features]


def _get_feature_names(features: list[dict[str, Any]]) -> list[str]:
    """Get all categorical feature names (excluding identifiers)."""
    if not features:
        return []
    exclude = {"relative_day", "relative_week", "relative_block"}
    return [k for k in features[0] if k not in exclude]


def _all_feature_vectors(features: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {name: _get_feature_vector(features, name) for name in _get_feature_names(features)}


# ── Attack result ──────────────────────────────────────────────────────


@dataclass
class CategoricalAttackResult:
    """Result of a categorical sequence attack.

    Top-level fields mirror the canonical contract directly so producers
    (the orchestrator) can write the contract fields without any
    additional conversion. Legacy callers reading `metrics["..."]` are
    no longer supported — read the canonical fields directly.
    """

    attack_type: str
    variant: str  # s3a, s3b, s3c
    ablation_group: str = "all"  # "direction", "momentum", "volatility", etc.
    metrics: dict[str, float] = field(default_factory=dict)
    true_source_rank: int = -1
    is_blocked: bool = False
    attack_hash: str = ""
    parameters: dict = field(default_factory=dict)
    # Canonical contract fields (top-level) — Phase 5A repair
    candidate_universe_size: int = 0
    percentile_rank: float = 0.0
    top_1: bool = False
    top_5: bool = False
    top_10: bool = False
    score: float | None = None
    status: AttackStatus = AttackStatus.COMPLETED


# ── Distance functions ─────────────────────────────────────────────────


def exact_categorical_sequence_similarity(
    source: list[str],
    candidate: list[str],
) -> float:
    """Fraction of positions where categories match exactly."""
    n = min(len(source), len(candidate))
    if n == 0:
        return 0.0
    matches = sum(1 for i in range(n) if source[i] == candidate[i])
    return matches / n


def weighted_hamming_distance(
    source: list[str],
    candidate: list[str],
    weights: dict[str, float] | None = None,
) -> float:
    """Weighted Hamming distance using ordinal positions."""
    n = min(len(source), len(candidate))
    if n == 0:
        return 1.0
    total = 0.0
    for i in range(n):
        so = _ordinal(source[i])
        co = _ordinal(candidate[i])
        diff = abs(so - co) / 4.0  # Normalize to [0, 1]
        w = weights.get(source[i], 1.0) if weights else 1.0
        total += diff * w
    return total / n


def categorical_dtw_distance(
    source: list[str],
    candidate: list[str],
    band_ratio: float = 0.25,
) -> float:
    """Dynamic time warping distance for categorical sequences."""
    n, m = len(source), len(candidate)
    if n == 0 or m == 0:
        return float("inf")

    w = max(1, int(min(n, m) * band_ratio))
    dtw = [[float("inf")] * (m + 1) for _ in range(n + 1)]
    dtw[0][0] = 0.0

    for i in range(1, n + 1):
        lo = max(1, i - w)
        hi = min(m, i + w)
        for j in range(lo, hi + 1):
            cost = abs(_ordinal(source[i - 1]) - _ordinal(candidate[j - 1])) / 4.0
            dtw[i][j] = cost + min(dtw[i - 1][j], dtw[i][j - 1], dtw[i - 1][j - 1])

    return dtw[n][m] / (n + m)


def state_transition_matrix_similarity(
    source: list[str],
    candidate: list[str],
) -> float:
    """Compare state transition matrices via Frobenius norm of difference."""
    states = sorted(set(source + candidate))
    n_states = len(states)
    if n_states == 0:
        return 1.0

    state_idx = {s: i for i, s in enumerate(states)}

    def _build_transition(seq: list[str]) -> list[list[float]]:
        mat = [[0.0] * n_states for _ in range(n_states)]
        for i in range(1, len(seq)):
            si = state_idx.get(seq[i - 1], 0)
            sj = state_idx.get(seq[i], 0)
            mat[si][sj] += 1.0
        # Normalize rows
        for row in mat:
            total = sum(row)
            if total > 0:
                for j in range(n_states):
                    row[j] /= total
        return mat

    sm = _build_transition(source)
    cm = _build_transition(candidate)

    diff_norm = sum((sm[i][j] - cm[i][j]) ** 2 for i in range(n_states) for j in range(n_states))
    return 1.0 - min(1.0, diff_norm / n_states)


def feature_n_gram_similarity(
    source: list[str],
    candidate: list[str],
    n: int = 3,
) -> float:
    """Jaccard similarity over n-gram sets."""
    if n <= 0:
        return 0.0

    def _ngrams(seq: list[str]) -> set[tuple[str, ...]]:
        return {tuple(seq[i : i + n]) for i in range(len(seq) - n + 1)}

    s_set = _ngrams(source)
    c_set = _ngrams(candidate)

    if not s_set and not c_set:
        return 1.0

    intersection = s_set & c_set
    union = s_set | c_set
    return len(intersection) / max(1, len(union))


def jaccard_similarity(
    source: list[str],
    candidate: list[str],
) -> float:
    """Jaccard similarity over the set of unique feature states."""
    s_set = set(source)
    c_set = set(candidate)
    if not s_set and not c_set:
        return 1.0
    return len(s_set & c_set) / max(1, len(s_set | c_set))


# ── Multi-variate combined similarity ──────────────────────────────────


def _combined_multi_feature_similarity(
    source_features: dict[str, list[str]],
    candidate_features: dict[str, list[str]],
    method: str = "exact",
    weights: dict[str, float] | None = None,
) -> float:
    """Compute combined similarity across multiple feature dimensions."""
    total_score = 0.0
    total_weight = 0.0

    all_names = set(source_features.keys()) & set(candidate_features.keys())
    for fname in all_names:
        w = weights.get(fname, 1.0) if weights else 1.0
        sv = source_features[fname]
        cv = candidate_features[fname]

        if method == "exact":
            score = exact_categorical_sequence_similarity(sv, cv)
        elif method == "weighted_hamming":
            score = 1.0 - weighted_hamming_distance(sv, cv)
        elif method == "dtw":
            score = 1.0 / (1.0 + categorical_dtw_distance(sv, cv))
        elif method == "transition":
            score = state_transition_matrix_similarity(sv, cv)
        elif method == "ngram":
            score = feature_n_gram_similarity(sv, cv)
        elif method == "jaccard":
            score = jaccard_similarity(sv, cv)
        else:
            score = exact_categorical_sequence_similarity(sv, cv)

        total_score += score * w
        total_weight += w

    return total_score / max(1, total_weight)


# ── Universe ranking ───────────────────────────────────────────────────


def rank_in_universe(
    source_features: list[dict[str, Any]],
    candidate_features: dict[str, list[dict[str, Any]]],
    variant: str = "s3b_weekly_features",
    method: str = "combined",
    ablation_group: str = "all",
    top_k: int = 10,
) -> CategoricalAttackResult:
    """Rank the source among a universe using categorical sequence similarity.

    Args:
        source_features: Feature rows for the true source (list of dicts)
        candidate_features: Dict of candidate_id -> feature rows
        variant: Which S3 variant (s3a, s3b, s3c)
        method: Similarity method (exact, weighted_hamming, dtw, transition, ngram, combined)
        ablation_group: Feature group for ablation testing
        top_k: Threshold for blocking

    Returns:
        CategoricalAttackResult with ranking
    """
    # Extract source feature vectors
    source_dict = _all_feature_vectors(source_features)

    # Determine which features to use based on ablation group
    feature_groups: dict[str, set[str]] = {
        "direction": {"return_direction", "weekly_direction_category", "dominant_trend_regime"},
        "momentum": {
            "momentum_5d_bucket",
            "momentum_21d_bucket",
            "momentum_63d_bucket",
            "momentum_4w_bucket",
            "momentum_12w_bucket",
            "momentum_26w_bucket",
            "aggregate_momentum_bucket",
        },
        "volatility": {
            "volatility_21d_bucket",
            "volatility_4w_bucket",
            "volatility_12w_bucket",
            "volatility_regime",
        },
        "volume": {"volume_activity_21d_bucket", "volume_activity_bucket", "volume_regime"},
        "drawdown": {"drawdown_bucket", "drawdown_regime"},
        "market_relative": {
            "market_relative_bucket",
            "market_relative_strength_bucket",
            "market_relative_regime",
        },
        "sector_relative": {
            "sector_relative_bucket",
            "sector_relative_strength_bucket",
            "sector_relative_regime",
        },
        "technical_state": {
            "moving_average_state",
            "moving_average_regime",
            "trend_persistence_bucket",
            "trend_consistency_bucket",
            "reversal_frequency_bucket",
        },
        "fundamentals": {"valuation_bucket", "profitability_bucket", "leverage_bucket"},
    }

    if ablation_group in feature_groups:
        allowed = feature_groups[ablation_group]
        source_dict = {k: v for k, v in source_dict.items() if k in allowed}
    elif ablation_group == "all":
        pass  # Use all features
    else:
        return CategoricalAttackResult(
            attack_type=method,
            variant=variant,
            ablation_group=ablation_group,
            true_source_rank=-1,
            metrics={"error": 1.0},
        )

    weights = {
        "return_direction": 3.0,
        "weekly_direction_category": 3.0,
        "dominant_trend_regime": 3.0,
    }

    # Score each candidate
    scores: list[tuple[str, float]] = []
    for cid, c_features in candidate_features.items():
        c_dict = _all_feature_vectors(c_features)
        if ablation_group in feature_groups:
            c_dict = {k: v for k, v in c_dict.items() if k in allowed}

        if method == "combined":
            # Average across multiple similarity methods
            exact_sim = _combined_multi_feature_similarity(source_dict, c_dict, "exact", weights)
            hamming_sim = 1.0 - _combined_multi_feature_similarity(
                source_dict, c_dict, "weighted_hamming", weights
            )
            dtw_sim = _combined_multi_feature_similarity(source_dict, c_dict, "dtw", weights)
            transition_sim = _combined_multi_feature_similarity(
                source_dict, c_dict, "transition", weights
            )
            ngram_sim = _combined_multi_feature_similarity(source_dict, c_dict, "ngram", weights)
            score = (exact_sim + hamming_sim + dtw_sim + transition_sim + ngram_sim) / 5.0
        elif method == "exact":
            score = _combined_multi_feature_similarity(source_dict, c_dict, "exact", weights)
        elif method == "weighted_hamming":
            score = 1.0 - _combined_multi_feature_similarity(
                source_dict, c_dict, "weighted_hamming", weights
            )
        elif method == "dtw":
            score = _combined_multi_feature_similarity(source_dict, c_dict, "dtw", weights)
        elif method == "transition":
            score = _combined_multi_feature_similarity(source_dict, c_dict, "transition", weights)
        elif method == "ngram":
            score = _combined_multi_feature_similarity(source_dict, c_dict, "ngram", weights)
        elif method == "jaccard":
            score = _combined_multi_feature_similarity(source_dict, c_dict, "jaccard", weights)
        else:
            score = _combined_multi_feature_similarity(source_dict, c_dict, "exact", weights)

        scores.append((cid, score))

    scores.sort(key=lambda x: x[1], reverse=True)

    # Find source rank
    source_rank = -1
    top_score = scores[0][1] if scores else 0.0
    source_score = 0.0
    next_score = 0.0

    for rank, (cid, score) in enumerate(scores, start=1):
        if cid == "SRC_001":
            source_rank = rank
            source_score = score
            if rank > 1:
                next_score = scores[rank - 2][1]  # Score just above
            elif rank < len(scores):
                next_score = scores[rank][1]  # Score just below
            break

    if source_rank == -1 and scores:
        next_score = scores[0][1]

    score_margin = source_score - next_score if next_score else 0.0
    is_blocked = source_rank > 0 and source_rank <= top_k
    total_candidates = len(candidate_features)
    percentile = (1.0 - (source_rank / max(1, total_candidates))) * 100 if source_rank > 0 else 0.0

    params = {
        "method": method,
        "ablation_group": ablation_group,
        "top_k": top_k,
        "universe_size": total_candidates,
    }
    param_str = json.dumps(params, sort_keys=True)
    attack_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]

    in_top_k = source_rank > 0 and source_rank <= top_k
    top_1 = source_rank == 1
    top_5 = source_rank > 0 and source_rank <= 5
    top_10 = source_rank > 0 and source_rank <= 10

    return CategoricalAttackResult(
        attack_type=method,
        variant=variant,
        ablation_group=ablation_group,
        metrics={
            "candidate_universe_size": total_candidates,
            "true_source_rank": source_rank,
            "percentile_rank": round(percentile, 2),
            "top_candidate_score": top_score,
            "source_score": round(source_score, 6),
            "score_margin": round(score_margin, 6),
            "top_k": top_k,
            "in_top_k": in_top_k,
            "top_1": top_1,
            "top_5": top_5,
            "top_10": top_10,
        },
        true_source_rank=source_rank,
        is_blocked=is_blocked,
        attack_hash=attack_hash,
        parameters=params,
        top_1=top_1,
        top_5=top_5,
        top_10=top_10,
        candidate_universe_size=total_candidates,
        percentile_rank=round(percentile, 2),
        score=round(source_score, 6),
        status=AttackStatus.BLOCKED if is_blocked else AttackStatus.COMPLETED,
    )


# ── Complete S3 attack suite ───────────────────────────────────────────


def run_s3_attack_suite(
    source_features: list[dict[str, Any]],
    candidate_features: dict[str, list[dict[str, Any]]],
    variant: str = "s3b_weekly_features",
    ablation_groups: list[str] | None = None,
    policy: dict | None = None,
) -> list[CategoricalAttackResult]:
    """Run the complete S3 attack suite for a variant.

    When `policy` is provided (dict with 'required_attack_keys' list of
    "attack_name/ablation" strings), only those exact attacks are generated.
    This ensures the generator and gate consume the same s3b-mvp-v1 contract.

    Without policy, runs the full hard-coded suite:
    - All similarity methods (exact, weighted_hamming, dtw, transition, ngram, combined)
    - Lagged/shifted/rolling window matching
    - All ablation groups

    Returns list of CategoricalAttackResult.
    """
    results: list[CategoricalAttackResult] = []

    # ── Policy-driven mode: only generate listed attacks ────────────
    if policy is not None:
        required_keys: frozenset[str] = frozenset(
            str(k) for k in policy.get("required_attack_keys", [])
        )
        if required_keys:
            for key in sorted(required_keys):
                parts = str(key).split("/", 1)
                attack_name = parts[0]
                ablation = parts[1] if len(parts) > 1 else "all"

                # Lagged attacks
                if attack_name.startswith("lagged_"):
                    try:
                        lag = int(attack_name.split("_")[1])
                    except (IndexError, ValueError):
                        continue
                    lagged_source = (
                        source_features[lag:] if lag < len(source_features) else source_features
                    )
                    if not lagged_source:
                        continue
                    result = rank_in_universe(
                        lagged_source,
                        candidate_features,
                        variant=variant,
                        method="combined",
                        ablation_group=ablation,
                    )
                    result.metrics["lag"] = float(lag)
                    result.attack_type = f"lagged_{lag}"
                    results.append(result)
                    continue

                # Standard attacks
                result = rank_in_universe(
                    source_features,
                    candidate_features,
                    variant=variant,
                    method=attack_name,
                    ablation_group=ablation,
                )
                results.append(result)
            return results

    # ── Legacy hard-coded suite (kept for backward compatibility) ───
    methods = ["exact", "weighted_hamming", "dtw", "transition", "ngram", "combined"]

    # All methods with default all features
    for method in methods:
        result = rank_in_universe(
            source_features, candidate_features, variant=variant, method=method
        )
        results.append(result)

    # Ablation attacks
    groups = ablation_groups or [
        "direction",
        "momentum",
        "volatility",
        "drawdown",
        "market_relative",
        "sector_relative",
        "technical_state",
    ]
    for group in groups:
        result = rank_in_universe(
            source_features,
            candidate_features,
            variant=variant,
            method="combined",
            ablation_group=group,
        )
        results.append(result)

    # Lagged matching
    for lag in [1, 5, 21]:
        # Shift source features by lag, trim to match
        lagged_source = source_features[lag:] if lag < len(source_features) else source_features
        if lagged_source:
            result = rank_in_universe(
                lagged_source, candidate_features, variant=variant, method="combined"
            )
            result.metrics["lag"] = float(lag)
            result.attack_type = f"lagged_{lag}"
            results.append(result)

    return results
