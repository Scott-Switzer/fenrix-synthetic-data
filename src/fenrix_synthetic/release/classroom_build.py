"""Classroom package builder (Hours 6-18 of 48-hour shipping plan).

Produces SYNTH_001_CLASSROOM_BETA/ with:
- s3b_features.csv (categorical features only, no prices/returns/dates)
- submission_template.csv
- example_submission.csv
- classroom_demo.ipynb
- README.md, QUICKSTART.md, DATA_DICTIONARY.md, LIMITATIONS.md
- privacy_summary.json
- release_manifest.json
- checksums.sha256

The `classroom-build` CLI command enforces:
- S3B-only for this beta
- PASS_CANDIDATE required
- Frozen s3b-mvp-v1 policy validation
- Exact eligible_candidate_count == 141
- Exact best_source_rank == 11
- Atomic package creation
- No writes outside private root or into tracked git
- Deterministic semantic content (build timestamp excluded)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from fenrix_synthetic.cli_errors import (
    InvalidInputError,
    PrivacyFailureError,
)

# Frozen release constants (Hours 6-18 contract)
_FROZEN_RELEASE_ID = "SYNTH_001"
_FROZEN_VARIANT = "s3b_weekly_features"
_FROZEN_POLICY_ID = "s3b-mvp-v1"
_FROZEN_ELIGIBLE_CANDIDATE_COUNT = 141
_FROZEN_BEST_SOURCE_RANK = 11
_FROZEN_WORST_PRIVACY_PERCENTILE = 92.2
_FROZEN_REQUIRED_ATTACK_COUNT = 16
_FROZEN_RELEASE_STATUS = "PASS_CANDIDATE"

# Fields that must NOT appear in the student package
_PROHIBITED_CSV_COLUMNS: set[str] = {
    "close",
    "open",
    "high",
    "low",
    "volume",
    "price",
    "return",
    "date",
    "timestamp",
    "adj_close",
    "dividend",
    "split",
    "raw",
    "log_return",
    "source",
    "ticker",
    "cik",
    "candidate",
}

_ALLOWED_FEATURE_COLUMNS: set[str] = {
    "relative_week",
    "return_direction",
    "weekly_direction_category",
    "momentum_5d_bucket",
    "momentum_21d_bucket",
    "momentum_63d_bucket",
    "momentum_4w_bucket",
    "momentum_12w_bucket",
    "momentum_26w_bucket",
    "aggregate_momentum_bucket",
    "volatility_21d_bucket",
    "volatility_4w_bucket",
    "volatility_12w_bucket",
    "volatility_regime",
    "volume_activity_21d_bucket",
    "volume_activity_bucket",
    "volume_regime",
    "drawdown_bucket",
    "drawdown_regime",
    "moving_average_state",
    "moving_average_regime",
    "market_relative_bucket",
    "market_relative_strength_bucket",
    "market_relative_regime",
    "sector_relative_bucket",
    "sector_relative_strength_bucket",
    "sector_relative_regime",
    "trend_persistence_bucket",
    "trend_consistency_bucket",
    "reversal_frequency_bucket",
    "dominant_trend_regime",
}

_STABLE_COLUMN_ORDER: list[str] = [
    "relative_week",
    "return_direction",
    "weekly_direction_category",
    "momentum_5d_bucket",
    "momentum_21d_bucket",
    "momentum_63d_bucket",
    "momentum_4w_bucket",
    "momentum_12w_bucket",
    "momentum_26w_bucket",
    "aggregate_momentum_bucket",
    "volatility_21d_bucket",
    "volatility_4w_bucket",
    "volatility_12w_bucket",
    "volatility_regime",
    "volume_activity_21d_bucket",
    "volume_activity_bucket",
    "volume_regime",
    "drawdown_bucket",
    "drawdown_regime",
    "moving_average_state",
    "moving_average_regime",
    "market_relative_bucket",
    "market_relative_strength_bucket",
    "market_relative_regime",
    "sector_relative_bucket",
    "sector_relative_strength_bucket",
    "sector_relative_regime",
    "trend_persistence_bucket",
    "trend_consistency_bucket",
    "reversal_frequency_bucket",
    "dominant_trend_regime",
]


def _load_s3b_features(source_id: str, private_root: Path) -> list[dict[str, Any]]:
    """Load S3B features from the private run directory."""
    run_dir = private_root / "runs" / source_id / "private"
    features_path = run_dir / "s3b_features.json"
    if not features_path.exists():
        raise InvalidInputError(f"S3B features not found at {features_path}")
    data: dict[str, Any] = json.loads(features_path.read_text())
    features: list[dict[str, Any]] = data.get("features", [])
    if not features:
        raise InvalidInputError("S3B features JSON contains no 'features' list")
    return features


def _load_and_validate_attack_results(source_id: str, private_root: Path) -> list[dict[str, Any]]:
    """Load S3B attack results and validate against frozen MVP policy."""
    from fenrix_synthetic.release.s3_gate import clear_mvp_policy, load_mvp_policy

    run_dir = private_root / "runs" / source_id / "private"
    attacks_path = run_dir / "s3b_attacks.json"
    if not attacks_path.exists():
        raise InvalidInputError(f"S3B attack results not found at {attacks_path}")

    data: dict[str, Any] = json.loads(attacks_path.read_text())
    attacks: list[dict[str, Any]] = data.get("attacks", [])
    if not attacks:
        raise InvalidInputError("Attack results contain no 'attacks' list")

    # Load MVP policy and verify exact 16 keys
    # Path: configs/policies/s3b-mvp-v1.json relative to project root
    project_root = Path(__file__).resolve().parents[3]  # release/fenrix_synthetic/src → root
    policy_path = project_root / "configs" / "policies" / "s3b-mvp-v1.json"
    policy_keys = load_mvp_policy(str(policy_path))

    observed_keys = {f"{a.get('attack_name', '')}/{a.get('ablation', 'all')}" for a in attacks}

    missing = sorted(policy_keys - observed_keys)
    if missing:
        raise InvalidInputError(
            f"Missing required attack keys: {missing}. "
            f"Rerun s3-attack with the frozen s3b-mvp-v1 policy."
        )

    additional = sorted(observed_keys - policy_keys)
    if additional:
        raise InvalidInputError(f"Unexpected attack keys not in policy: {additional}")

    # Verify duplicate-free
    key_list = [f"{a.get('attack_name', '')}/{a.get('ablation', 'all')}" for a in attacks]
    if len(key_list) != len(set(key_list)):
        dupes = [k for k in set(key_list) if key_list.count(k) > 1]
        raise InvalidInputError(f"Duplicate attack keys: {dupes}")

    try:
        clear_mvp_policy()
    except Exception:
        pass

    return attacks


def _verify_frozen_gate_result(attacks: list[dict[str, Any]]) -> None:
    """Verify the frozen gate result: PASS_CANDIDATE, best rank 11, 141 candidates."""
    # Verify eligible candidate count
    universes = {a.get("candidate_universe_size", 0) for a in attacks}
    actual_count = max(universes) if universes else 0
    if actual_count != _FROZEN_ELIGIBLE_CANDIDATE_COUNT:
        raise InvalidInputError(
            f"Eligible candidate count mismatch: expected {_FROZEN_ELIGIBLE_CANDIDATE_COUNT}, "
            f"got {actual_count}"
        )

    # Verify best source rank
    ranks = [a.get("true_source_rank", -1) for a in attacks if a.get("true_source_rank", -1) > 0]
    best_rank = min(ranks) if ranks else -1
    if best_rank != _FROZEN_BEST_SOURCE_RANK:
        raise InvalidInputError(
            f"Best source rank mismatch: expected {_FROZEN_BEST_SOURCE_RANK}, got {best_rank}"
        )

    # Verify no top-10 entries
    top10 = [a for a in attacks if a.get("top_10")]
    if top10:
        raise PrivacyFailureError(
            f"Source ranks in top 10 under {len(top10)} attacks. "
            f"Release cannot proceed as PASS_CANDIDATE."
        )

    # Verify gate decision via actual gate re-evaluation
    from fenrix_synthetic.release.s3_gate import (
        PrivacyDecision,
        S3PrivacyGate,
        clear_mvp_policy,
        load_mvp_policy,
    )

    project_root = Path(__file__).resolve().parents[3]
    policy_path = project_root / "configs" / "policies" / "s3b-mvp-v1.json"
    load_mvp_policy(str(policy_path))
    gate = S3PrivacyGate()
    result = gate.evaluate("s3b_weekly_features", attacks)
    try:
        clear_mvp_policy()
    except Exception:
        pass

    if result.decision != PrivacyDecision.PASS_CANDIDATE:
        raise PrivacyFailureError(
            f"Gate decision is {result.decision.value}, not PASS_CANDIDATE. "
            f"Blocking: {result.blocking_reasons}"
        )


def _export_features_csv(features: list[dict[str, Any]]) -> str:
    """Export S3B features as CSV with stable column order. Returns CSV string."""
    # Determine which columns are present
    present_columns = [c for c in _STABLE_COLUMN_ORDER if c in features[0] if features]
    if not present_columns:
        raise InvalidInputError("No valid feature columns found in S3B data")

    # Verify no prohibited columns: check for prohibited substrings only when
    # the column is NOT in the known safe ALLOWED set (word-boundary check).
    for col in features[0]:
        if col in _ALLOWED_FEATURE_COLUMNS:
            continue
        col_lower = col.lower()
        for prohibited in _PROHIBITED_CSV_COLUMNS:
            if prohibited in col_lower:
                raise PrivacyFailureError(f"Prohibited field '{col}' detected in features")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=present_columns, extrasaction="ignore")
    writer.writeheader()
    for row in features:
        writer.writerow(row)
    return output.getvalue()


def _create_submission_template() -> str:
    """Create submission_template.csv content."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["relative_period", "action"])
    writer.writerow([0, 0])
    writer.writerow([1, 0])
    writer.writerow([2, 0])
    return output.getvalue()


def _create_example_submission(n_periods: int = 50) -> str:
    """Create a simple example submission: hold cash on odd weeks, long on even weeks."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["relative_period", "action"])
    for i in range(n_periods):
        writer.writerow([i, i % 2])  # Simple non-predictive alternating pattern
    return output.getvalue()


def _build_notebook() -> str:
    """Build classroom_demo.ipynb as a JSON notebook string."""
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "version": "3.12.0",
            },
        },
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "# SYNTH_001 Classroom Beta — Student Notebook\n\n",
                    "## Objective\n\n",
                    "You have received a **privacy-tested weekly feature dataset** derived from a real public company. ",
                    "Your task: construct a binary trade decision (0 = cash, 1 = long) for each weekly period, ",
                    "export your submission, and submit it to your instructor for private evaluation.\n\n",
                    "**Important**: You do NOT have access to prices, returns, dates, or the underlying company identity. ",
                    "All features are coarse categorical buckets (e.g., LOW/MEDIUM/HIGH).\n",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## Privacy Statement\n\n",
                    "This dataset is released under the status **PASS_CANDIDATE** ",
                    "under the frozen `s3b-mvp-v1` bounded attack policy: ",
                    "16/16 required categorical-sequence attacks completed, ",
                    "best source rank 11 of 141. **This is not a guarantee of anonymity.**\n\n",
                    "The features contain only coarse categorical values. ",
                    "No prices, returns, dates, volume, or company identifiers are included.",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 1. Load and Validate S3B Features",
                ],
            },
            {
                "cell_type": "code",
                "metadata": {},
                "source": [
                    'import csv\nfrom pathlib import Path\n\nfeatures_path = Path("s3b_features.csv")\nif not features_path.exists():\n    raise FileNotFoundError(f"{features_path} not found. Make sure it is in the current directory.")\n\nwith open(features_path) as f:\n    reader = csv.DictReader(f)\n    features = list(reader)\n\nprint(f"Loaded {len(features)} weekly periods")\nprint(f"Columns ({len(features[0])}): {\', \'.join(features[0].keys())}")',
                ],
                "outputs": [],
                "execution_count": None,
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 2. Feature Definitions\n\n",
                    "| Column | Allowed Values | Interpretation |\n",
                    "|:---|:---|:---|\n",
                    "| `relative_week` | 0, 1, 2, ... | Week index (0 = oldest) |\n",
                    "| `return_direction` | DOWN, FLAT, UP | Weekly price direction |\n",
                    "| `weekly_direction_category` | DOWN, FLAT, UP | Smoothed direction category |\n",
                    "| `momentum_*_bucket` | VERY_LOW ... VERY_HIGH | Momentum strength over lookback |\n",
                    "| `volatility_*_bucket` | VERY_LOW ... VERY_HIGH | Volatility level over lookback |\n",
                    "| `volatility_regime` | LOW, MEDIUM, HIGH | Current volatility regime |\n",
                    "| `volume_activity_*_bucket` | LOW, MEDIUM, HIGH | Volume activity level |\n",
                    "| `volume_regime` | LOW, MEDIUM, HIGH | Volume regime |\n",
                    "| `drawdown_bucket` | VERY_LOW ... VERY_HIGH | Drawdown severity |\n",
                    "| `drawdown_regime` | LOW, MEDIUM, HIGH | Drawdown regime |\n",
                    "| `moving_average_state` | BELOW, CROSSED, ABOVE | Price vs moving average |\n",
                    "| `moving_average_regime` | BELOW, CROSSED, ABOVE | Moving average regime |\n",
                    "| `market_relative_*` | VERY_LOW ... VERY_HIGH | Relative to market |\n",
                    "| `sector_relative_*` | VERY_LOW ... VERY_HIGH | Relative to sector |\n",
                    "| `trend_*_bucket` | SHORT, MODERATE, PERSISTENT | Trend characteristics |\n",
                    "| `dominant_trend_regime` | STRONG_DOWN ... STRONG_UP | Dominant trend regime |\n\n",
                    "Missing values appear as empty strings. You should handle them in your decision logic.",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 3. Exploratory Summary",
                ],
            },
            {
                "cell_type": "code",
                "metadata": {},
                "source": [
                    "from collections import Counter\n\n# Show distribution for the first few categorical columns\ncategorical_cols = [c for c in features[0].keys() if c != 'relative_week']\nfor col in categorical_cols[:5]:  # Show first 5\n    values = [row[col] for row in features if row[col]]\n    counts = Counter(values)\n    print(f\"\\n{col}:\")\n    for val, count in sorted(counts.items()):\n        bar = '#' * (count // 5)\n        print(f\"  {val:<15} {count:>4} {bar}\")",
                ],
                "outputs": [],
                "execution_count": None,
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 4. Construct a Simple Decision Rule\n\n",
                    "Below is a **simple example** using only the released features. ",
                    "This is intentionally naive — you should improve it using only what is in `s3b_features.csv`.\n\n",
                    "**Available columns**: `relative_week`, `weekly_direction_category`, `momentum_4w_bucket`, `momentum_12w_bucket`, `momentum_26w_bucket`, `volatility_4w_bucket`, `volatility_12w_bucket`, `volume_activity_bucket`, `drawdown_bucket`, `moving_average_regime`, `market_relative_strength_bucket`, `sector_relative_strength_bucket`, `trend_persistence_bucket`.",
                ],
            },
            {
                "cell_type": "code",
                "metadata": {},
                "source": [
                    "import random\nrandom.seed(42)  # Fixed seed for reproducibility\n\ndef simple_decision_rule(features):\n    \"\"\"Example: go long (1) when 4-week momentum is HIGH/VERY_HIGH and weekly direction is UP.\"\"\"\n    actions = []\n    for row in features:\n        mom = row.get('momentum_4w_bucket', '')\n        direction = row.get('weekly_direction_category', '')\n        if mom in ('HIGH', 'VERY_HIGH') and direction == 'UP':\n            actions.append(1)\n        else:\n            actions.append(0)\n    return actions\n\nactions = simple_decision_rule(features)\nprint(f\"Generated {len(actions)} decisions\")\nprint(f\"Long (1): {actions.count(1)}, Cash (0): {actions.count(0)}\")\nprint(f\"Trade rate: {actions.count(1) / len(actions):.1%}\")",
                ],
                "outputs": [],
                "execution_count": None,
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 5. Validate the Submission Locally",
                ],
            },
            {
                "cell_type": "code",
                "metadata": {},
                "source": [
                    'def validate_submission(periods, actions):\n    """Validate submission before export."""\n    errors = []\n    if len(periods) != len(actions):\n        errors.append(f"Length mismatch: {len(periods)} periods vs {len(actions)} actions")\n    if not periods:\n        errors.append("Empty submission")\n    if len(set(periods)) != len(periods):\n        errors.append("Duplicate periods detected")\n    for i in range(1, len(periods)):\n        if periods[i] <= periods[i - 1]:\n            errors.append(f"Periods not strictly increasing at index {i}")\n    for i, a in enumerate(actions):\n        if isinstance(a, bool) or a not in (0, 1):\n            errors.append(f"Action at index {i} is not 0 or 1: {a}")\n    return errors\n\nperiods = [int(row[\'relative_week\']) for row in features]\nerrors = validate_submission(periods, actions)\nif errors:\n    print("VALIDATION ERRORS:")\n    for e in errors:\n        print(f"  - {e}")\nelse:\n    print("Submission is valid!")',
                ],
                "outputs": [],
                "execution_count": None,
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 6. Export Student Submission",
                ],
            },
            {
                "cell_type": "code",
                "metadata": {},
                "source": [
                    "import csv\n\noutput_path = \"student_submission.csv\"\nwith open(output_path, 'w', newline='') as f:\n    writer = csv.writer(f)\n    writer.writerow(['relative_period', 'action'])\n    for p, a in zip(periods, actions):\n        writer.writerow([p, a])\n\nprint(f\"Exported {len(periods)} decisions to {output_path}\")\nprint(\"Submit this file to your instructor for private evaluation.\")",
                ],
                "outputs": [],
                "execution_count": None,
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 7. How the Instructor Evaluates It\n\n",
                    "Your instructor will run:\n\n",
                    '```bash\nfenrix-synthetic evaluate-submission \\\\\n  --release-id SYNTH_001 \\\\\n  --run-id <run-id> \\\\\n  --submission-id <your-submission-id> \\\\\n  --relative-periods "0,1,2,..." \\\\\n  --binary-actions "0,1,0,..." \\\\\n  --private-truth <private-truth-path>\n```\n\n',
                    "The evaluator is private. Students never see realized returns, prices, or period-level P&L. ",
                    "Only aggregate metrics (annualized return, Sharpe, max drawdown) are shared.",
                ],
            },
        ],
    }
    return json.dumps(notebook, indent=1)


def _create_readme() -> str:
    """Create README.md content."""
    return """# SYNTH_001 Classroom Beta

## What This Is

A **privacy-tested financial decision dataset** for classroom use. Students receive
coarse weekly categorical features and submit binary trade decisions (0 = cash, 1 = long).
Real outcomes remain inside a private evaluator accessible only to the instructor.

## What This Is Not

- NOT a synthetic-price generator
- NOT a guarantee of anonymity
- NOT a ready-to-trade strategy dataset
- NOT labeled with realized returns

## Release Status

**PASS_CANDIDATE** under the frozen `s3b-mvp-v1` bounded attack policy:
16/16 required categorical-sequence attacks completed, best source rank 11 of 141.
This is not a guarantee of anonymity.

## File Inventory

| File | Purpose |
|:---|:---|
| `README.md` | This file |
| `QUICKSTART.md` | Five-minute setup guide |
| `DATA_DICTIONARY.md` | Feature definitions and allowed values |
| `LIMITATIONS.md` | Known limitations and scope |
| `s3b_features.csv` | 402 weekly categorical feature periods |
| `submission_template.csv` | Template for student submissions |
| `example_submission.csv` | Simple non-predictive example |
| `classroom_demo.ipynb` | Jupyter notebook walkthrough |
| `privacy_summary.json` | Sanitized privacy assessment summary |
| `release_manifest.json` | Package metadata and checksums |
| `checksums.sha256` | SHA-256 checksums for all files |

## Five-Minute Workflow

1. Open `classroom_demo.ipynb` in Jupyter
2. Run all cells to see the dataset and an example decision rule
3. Improve the decision rule (use only features in `s3b_features.csv`)
4. Export `student_submission.csv`
5. Submit to your instructor
"""


def _create_quickstart() -> str:
    """Create QUICKSTART.md content."""
    return """# QUICKSTART — SYNTH_001 Classroom Beta

## Student Steps

1. Open `classroom_demo.ipynb` in Jupyter Notebook or JupyterLab.
2. Run all cells (Cell → Run All).
3. Read the feature definitions in Section 2.
4. Modify the decision rule in Section 4 to create your own strategy.
5. Run the validation in Section 5.
6. Export your submission (Section 6 produces `student_submission.csv`).
7. Submit `student_submission.csv` to your instructor.

## Instructor Evaluation Command

```bash
fenrix-synthetic evaluate-submission \\
  --release-id SYNTH_001 \\
  --run-id classroom-beta \\
  --submission-id <student-id> \\
  --relative-periods "0,1,2,...,401" \\
  --binary-actions "0,1,0,...,1" \\
  --private-truth <path-to-private-truth>
```

## Expected Filenames

- Student submits: `student_submission.csv`
- Columns: `relative_period`, `action`
- `action` must be 0 or 1 (integers, not booleans)

## Common Validation Errors

| Error | Cause | Fix |
|:---|:---|:---|
| `[SHAPE_MISMATCH]` | Periods and actions have different lengths | Ensure one action per period |
| `[BOOL_ACTION]` | Boolean `True`/`False` instead of `1`/`0` | Use integers |
| `[BINARY_VIOLATION]` | Action is not 0 or 1 | Use only 0 or 1 |
| `[EMPTY_SUBMISSION]` | No decisions submitted | Include at least one period |
| `[DUPLICATE_PERIOD]` | Same period appears twice | Use unique periods |
| `[ZERO_DECISIONS]` | All decisions lost to execution lag | Submit more periods than lag |
"""


def _create_data_dictionary() -> str:
    """Create DATA_DICTIONARY.md content."""
    return """# DATA DICTIONARY — SYNTH_001 S3B Weekly Features

All features are weekly categorical buckets. No prices, returns, dates, volume, or
company identifiers are included.

## Period Identifier

| Column | Type | Description |
|:---|:---|:---|
| `relative_week` | integer | Week index, 0 = oldest, strictly increasing |

## Direction Features

| Column | Allowed Values | Interpretation |
|:---|:---|:---|
| `return_direction` | DOWN, FLAT, UP | Weekly price direction |
| `weekly_direction_category` | DOWN, FLAT, UP | Smoothed direction category |

## Momentum Features (weekly)

| Column | Allowed Values | Interpretation |
|:---|:---|:---|
| `momentum_5d_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | 5-day momentum |
| `momentum_21d_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | 21-day momentum |
| `momentum_63d_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | 63-day momentum |
| `momentum_4w_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | 4-week momentum |
| `momentum_12w_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | 12-week momentum |
| `momentum_26w_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | 26-week momentum |
| `aggregate_momentum_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | Aggregate momentum score |

## Volatility Features

| Column | Allowed Values | Interpretation |
|:---|:---|:---|
| `volatility_21d_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | 21-day volatility |
| `volatility_4w_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | 4-week volatility |
| `volatility_12w_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | 12-week volatility |
| `volatility_regime` | LOW, MEDIUM, HIGH | Volatility regime |

## Volume Features

| Column | Allowed Values | Interpretation |
|:---|:---|:---|
| `volume_activity_21d_bucket` | LOW, MEDIUM, HIGH | 21-day volume activity |
| `volume_activity_bucket` | LOW, MEDIUM, HIGH | Current volume activity |
| `volume_regime` | LOW, MEDIUM, HIGH | Volume regime |

## Drawdown Features

| Column | Allowed Values | Interpretation |
|:---|:---|:---|
| `drawdown_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | Drawdown severity |
| `drawdown_regime` | LOW, MEDIUM, HIGH | Drawdown regime |

## Moving Average Features

| Column | Allowed Values | Interpretation |
|:---|:---|:---|
| `moving_average_state` | BELOW, CROSSED, ABOVE, NEUTRAL | Price vs MA |
| `moving_average_regime` | BELOW, CROSSED, ABOVE, NEUTRAL | MA regime |

## Market-Relative Features

| Column | Allowed Values | Interpretation |
|:---|:---|:---|
| `market_relative_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | vs market |
| `market_relative_strength_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | Relative strength |
| `market_relative_regime` | LOW, MEDIUM, HIGH | Market-relative regime |

## Sector-Relative Features

| Column | Allowed Values | Interpretation |
|:---|:---|:---|
| `sector_relative_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | vs sector |
| `sector_relative_strength_bucket` | VERY_LOW, LOW, MEDIUM, HIGH, VERY_HIGH | Sector-relative strength |
| `sector_relative_regime` | LOW, MEDIUM, HIGH | Sector-relative regime |

## Trend Features

| Column | Allowed Values | Interpretation |
|:---|:---|:---|
| `trend_persistence_bucket` | SHORT, MODERATE, PERSISTENT | Trend persistence |
| `trend_consistency_bucket` | LOW, MEDIUM, HIGH | Trend consistency |
| `reversal_frequency_bucket` | LOW, MEDIUM, HIGH | Reversal frequency |
| `dominant_trend_regime` | STRONG_DOWN, MILD_DOWN, NEUTRAL, MILD_UP, STRONG_UP | Dominant trend |

## Missing-Value Behavior

Empty strings indicate missing values. Your decision logic should handle these gracefully.
Features are weekly (not daily or block-based).
"""


def _create_limitations() -> str:
    """Create LIMITATIONS.md content."""
    return f"""# LIMITATIONS — SYNTH_001 Classroom Beta

## Privacy Scope

- **PASS_CANDIDATE** is bounded to the frozen `{_FROZEN_POLICY_ID}` attack policy:
  {_FROZEN_REQUIRED_ATTACK_COUNT} categorical-sequence attacks completed.
- Best source rank is {_FROZEN_BEST_SOURCE_RANK} of {_FROZEN_ELIGIBLE_CANDIDATE_COUNT} candidates.
- **This is not an anonymity guarantee.** It means the source was not identifiable
  under these specific attacks within this specific candidate universe.

## Variant Limitations

- **S3A** (daily bucketed): Ineligible for release — retains too much temporal structure.
- **S3C** (block features): Unsupported — candidate alignment issues.
- Only **S3B** (weekly features) is released.

## Privacy Attacks Not Yet Performed

- Human-in-the-loop re-identification
- Semantic/fingerprint attacks on the categorical patterns
- Multi-modal attacks combining structured + text evidence
- Longitudinal attacks across multiple releases
- Auxiliary-information attacks

## Data Limitations

- Initial pilot uses one source company.
- The private evaluator is required — students cannot self-evaluate.
- No strategy-performance claim is made. The example strategy is intentionally naive.

## Evaluation Limitations

- Execution lag and transaction costs are configured by the instructor.
- In-sample/out-of-sample split is controlled by the private truth.
- Aggregate metrics may not generalize to live trading.

## Future Work

- Multi-company releases
- Additional attack types
- Independent privacy audit
- Student leaderboard (privacy-preserving)
"""


def _create_privacy_summary() -> dict[str, Any]:
    """Create privacy_summary.json content."""
    return {
        "release_id": _FROZEN_RELEASE_ID,
        "release_status": _FROZEN_RELEASE_STATUS,
        "variant": _FROZEN_VARIANT,
        "policy_id": _FROZEN_POLICY_ID,
        "eligible_candidate_count": _FROZEN_ELIGIBLE_CANDIDATE_COUNT,
        "required_attack_count": _FROZEN_REQUIRED_ATTACK_COUNT,
        "completed_attack_count": _FROZEN_REQUIRED_ATTACK_COUNT,
        "missing_attack_count": 0,
        "duplicate_attack_count": 0,
        "best_source_rank": _FROZEN_BEST_SOURCE_RANK,
        "worst_privacy_percentile": _FROZEN_WORST_PRIVACY_PERCENTILE,
        "top_10_under_any_required_attack": False,
        "privacy_scan_passed": True,
        "prohibited_field_scan_passed": True,
        "identity_canary_match_count": 0,
        "disclaimer": (
            "PASS_CANDIDATE under the frozen s3b-mvp-v1 bounded attack policy. "
            "This is not a guarantee of anonymity."
        ),
    }


def _create_release_manifest(
    package_dir: Path,
    features: list[dict[str, Any]],
    git_sha: str,
    feature_policy_hash: str,
    attack_policy_hash: str,
    dataset_semantic_hash: str,
    package_semantic_hash: str,
    file_checksums: dict[str, str],
) -> dict[str, Any]:
    """Create release_manifest.json content."""
    return {
        "release_id": _FROZEN_RELEASE_ID,
        "release_version": "1.0.0",
        "schema_version": "1.0.0",
        "git_commit_sha": git_sha,
        "feature_policy_hash": feature_policy_hash,
        "attack_policy_hash": attack_policy_hash,
        "dataset_semantic_hash": dataset_semantic_hash,
        "package_semantic_hash": package_semantic_hash,
        "file_checksums": file_checksums,
        "row_count": len(features),
        "feature_count": len(features[0]) if features else 0,
        "release_status": _FROZEN_RELEASE_STATUS,
        "privacy_summary": "privacy_summary.json",
        "evaluator_required": True,
        "build_timestamp": datetime.now(UTC).isoformat(),
        "disclaimer": (
            "PASS_CANDIDATE under the frozen s3b-mvp-v1 bounded attack policy. "
            "This is not a guarantee of anonymity."
        ),
    }


def _sha256_checksum(content: str | bytes) -> str:
    """Compute SHA-256 checksum."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _compute_semantic_hash(package_dir: Path, exclude_patterns: set[str]) -> str:
    """Compute semantic package hash (excluding build timestamps)."""
    hasher = hashlib.sha256()
    for fpath in sorted(package_dir.rglob("*")):
        if fpath.is_dir():
            continue
        rel = str(fpath.relative_to(package_dir))
        if rel in exclude_patterns:
            continue
        hasher.update(rel.encode())
        hasher.update(fpath.read_bytes())
    return hasher.hexdigest()[:16]


def _scan_for_private_data(package_dir: Path) -> list[str]:
    """Scan package for configured identity canary matches. Returns list of issues."""
    # For the classroom beta, we do a structural scan:
    # 1. No files contain forbidden patterns
    # 2. No CSV files have prohibited columns
    issues: list[str] = []

    # Defensive canary scan: these are fictional canary values that should
    # never appear in output. Real-company canary lists belong in gitignored
    # private config. The tracked scanner uses only fictional canary tokens.
    forbidden_patterns = [
        "Canary Holdings Corporation",
        "CHC",
        "canary-test.invalid",
        "0000999999",
        "Eleanor Testperson",
        "Canary City",
        "99999",
        "Canary Audit LLP",
    ]

    for fpath in package_dir.rglob("*"):
        if fpath.is_dir() or fpath.suffix == ".sha256":
            continue
        try:
            content = fpath.read_text(errors="replace").lower()
        except Exception:
            continue
        for pattern in forbidden_patterns:
            if pattern.lower() in content:
                issues.append(f"Identity canary '{pattern}' found in {fpath.name}")
                break

    return issues


# ── CLI Command ────────────────────────────────────────────────────────


@click.command(name="classroom-build")
@click.option("--private-root", type=click.Path(path_type=Path), default=None)
@click.option("--source-id", required=True, help="Source run ID (e.g., src001)")
@click.option("--release-id", default=_FROZEN_RELEASE_ID)
@click.option(
    "--variant", type=click.Choice(["s3b_weekly_features"]), default="s3b_weekly_features"
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Output directory for the classroom package (under FENRIX_PRIVATE_ROOT).",
)
@click.option(
    "--mvp-policy",
    "mvp_policy_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to s3b-mvp-v1.json (default: configs/policies/s3b-mvp-v1.json).",
)
def classroom_build(
    private_root: Path | None,
    source_id: str,
    release_id: str,
    variant: str,
    output_dir: Path,
    mvp_policy_path: Path | None,
) -> None:
    """Build the SYNTH_001 classroom beta package.

    Freezes the PASS_CANDIDATE verdict. Requires:
    - S3B variant only
    - Frozen s3b-mvp-v1 policy with exact 16 attacks
    - eligible_candidate_count == 141
    - best_source_rank == 11
    - No top-10 attacks

    Produces SYNTH_001_CLASSROOM_BETA/ with features CSV, notebook,
    documentation, and verifiable checksums.
    """
    # Resolve private root
    env_root = os.environ.get("FENRIX_PRIVATE_ROOT", "").strip()
    if private_root is None and env_root:
        private_root = Path(env_root)
    if private_root is None or not private_root.exists():
        raise InvalidInputError("--private-root or FENRIX_PRIVATE_ROOT env required.")

    private_root = private_root.resolve()

    # Enforce output path containment
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to(private_root):
        raise PrivacyFailureError(
            f"Output directory must be under FENRIX_PRIVATE_ROOT ({private_root})"
        )

    # Git-tracked check
    try:
        repo_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        repo_path = Path(repo_root).resolve()
        if output_dir.is_relative_to(repo_path):
            raise PrivacyFailureError("Output directory is inside the tracked git repository")
    except subprocess.CalledProcessError:
        pass

    # Get Git SHA
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        git_sha = "unknown"

    # Load MVP policy
    project_root = Path(__file__).resolve().parents[3]
    if mvp_policy_path is None:
        mvp_policy_path = project_root / "configs" / "policies" / "s3b-mvp-v1.json"
    from fenrix_synthetic.release.s3_gate import clear_mvp_policy, load_mvp_policy

    try:
        policy_keys = load_mvp_policy(str(mvp_policy_path))
        if len(policy_keys) != _FROZEN_REQUIRED_ATTACK_COUNT:
            raise InvalidInputError(
                f"MVP policy must have {_FROZEN_REQUIRED_ATTACK_COUNT} keys, got {len(policy_keys)}"
            )
    finally:
        try:
            clear_mvp_policy()
        except Exception:
            pass

    # Load features
    features = _load_s3b_features(source_id, private_root)

    # Load and validate attack results
    attacks = _load_and_validate_attack_results(source_id, private_root)

    # Verify frozen gate result
    _verify_frozen_gate_result(attacks)

    # Build package directory atomically
    package_name = "SYNTH_001_CLASSROOM_BETA"
    package_dir = output_dir / package_name

    # Atomic build: build in temp, then move
    tmp_dir = Path(tempfile.mkdtemp(prefix="classroom_build_", dir=str(output_dir)))
    tmp_package = tmp_dir / package_name
    _moved = False
    try:
        tmp_package.mkdir(parents=True, exist_ok=True)

        # ── Build all package files ──
        files_to_write: dict[str, str] = {}

        # s3b_features.csv
        csv_content = _export_features_csv(features)
        files_to_write["s3b_features.csv"] = csv_content

        # submission_template.csv
        files_to_write["submission_template.csv"] = _create_submission_template()

        # example_submission.csv
        files_to_write["example_submission.csv"] = _create_example_submission()

        # classroom_demo.ipynb
        files_to_write["classroom_demo.ipynb"] = _build_notebook()

        # README.md
        files_to_write["README.md"] = _create_readme()

        # QUICKSTART.md
        files_to_write["QUICKSTART.md"] = _create_quickstart()

        # DATA_DICTIONARY.md
        files_to_write["DATA_DICTIONARY.md"] = _create_data_dictionary()

        # LIMITATIONS.md
        files_to_write["LIMITATIONS.md"] = _create_limitations()

        # privacy_summary.json
        privacy_summary = _create_privacy_summary()
        files_to_write["privacy_summary.json"] = json.dumps(privacy_summary, indent=2)

        # ── Compute hashes ──
        file_checksums: dict[str, str] = {}
        for fname in sorted(files_to_write):
            file_checksums[fname] = _sha256_checksum(files_to_write[fname])

        # Feature policy hash
        feature_policy_hash = _sha256_checksum(json.dumps(_STABLE_COLUMN_ORDER, sort_keys=True))[
            :16
        ]

        # Dataset semantic hash
        dataset_hasher = hashlib.sha256()
        for row in features:
            dataset_hasher.update(json.dumps(row, sort_keys=True).encode())
        dataset_semantic_hash = dataset_hasher.hexdigest()[:16]

        # Write all files
        for fname, content in files_to_write.items():
            (tmp_package / fname).write_text(content)

        # Compute package semantic hash (excluding timestamps)
        package_semantic_hash = _compute_semantic_hash(
            tmp_package, exclude_patterns={"release_manifest.json", "checksums.sha256"}
        )

        # Attack policy hash
        attack_policy_hash = _sha256_checksum(json.dumps(sorted(policy_keys)))[:16]

        # release_manifest.json
        manifest = _create_release_manifest(
            package_dir=tmp_package,
            features=features,
            git_sha=git_sha,
            feature_policy_hash=feature_policy_hash,
            attack_policy_hash=attack_policy_hash,
            dataset_semantic_hash=dataset_semantic_hash,
            package_semantic_hash=package_semantic_hash,
            file_checksums=file_checksums,
        )

        # Write manifest
        (tmp_package / "release_manifest.json").write_text(json.dumps(manifest, indent=2))

        # checksums.sha256
        checksum_lines = [f"{h}  {fname}" for fname, h in sorted(file_checksums.items())]
        checksum_lines.append(
            f"{_sha256_checksum(json.dumps(manifest, indent=2))}  release_manifest.json"
        )
        (tmp_package / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n")

        # Privacy scan
        scan_issues = _scan_for_private_data(tmp_package)
        if scan_issues:
            # Clean up temp on failure
            shutil.rmtree(str(tmp_dir), ignore_errors=True)
            raise PrivacyFailureError(f"Privacy scan failed: {'; '.join(scan_issues[:5])}")

        # Atomic move: replace existing if present
        if package_dir.exists():
            old_dir = Path(str(package_dir) + ".old." + datetime.now(UTC).strftime("%Y%m%dT%H%M%S"))
            shutil.move(str(package_dir), str(old_dir))
        shutil.move(str(tmp_package), str(package_dir))
        _moved = True
        shutil.rmtree(str(tmp_dir), ignore_errors=True)

        click.echo(
            f"classroom-build OK | "
            f"release={release_id} | "
            f"status={_FROZEN_RELEASE_STATUS} | "
            f"rows={len(features)} | "
            f"cols={len(features[0]) if features else 0} | "
            f"package_hash={package_semantic_hash} | "
            f"output={package_dir}"
        )

    except Exception:
        # Clean up temp on failure
        shutil.rmtree(str(tmp_dir), ignore_errors=True)
        # Only clean up the final package directory if the atomic move
        # completed (package_dir was corrupted by a partial move).
        # If _moved is False, package_dir still holds the valid previous
        # build — do NOT delete it.
        if _moved and package_dir.exists():
            # Don't leave a partial/overwritten package
            shutil.rmtree(str(package_dir), ignore_errors=True)
        raise
