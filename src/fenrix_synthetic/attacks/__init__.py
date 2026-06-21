"""Re-identification attack subsystem.

Text attacks:
- Exact residual scanning for identity values in unmasked text

Structured attacks:
- Correlation and ranking attacks against structured transforms

Categorical attacks (Phase 5A):
- Sequence similarity, Hamming distance, DTW, n-gram, Jaccard, ablation
"""

from .categorical_attacks import (
    CategoricalAttackResult,
    categorical_dtw_distance,
    exact_categorical_sequence_similarity,
    feature_n_gram_similarity,
    jaccard_similarity,
    rank_in_universe,
    run_s3_attack_suite,
    state_transition_matrix_similarity,
    weighted_hamming_distance,
)
from .exact_match import ExactResidualScanner, ScanResult
from .structured_attacks import (
    StructuredAttackResult,
    candidate_universe_rank,
    direct_correlation,
    run_structured_attacks,
)

__all__ = [
    "CategoricalAttackResult",
    "categorical_dtw_distance",
    "ExactResidualScanner",
    "exact_categorical_sequence_similarity",
    "feature_n_gram_similarity",
    "jaccard_similarity",
    "rank_in_universe",
    "run_s3_attack_suite",
    "run_structured_attacks",
    "ScanResult",
    "StructuredAttackResult",
    "candidate_universe_rank",
    "direct_correlation",
    "state_transition_matrix_similarity",
    "weighted_hamming_distance",
]
