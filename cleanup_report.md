# Cleanup Report

## Inventory

- Target branch: `feature/colab-multicompany-anonymization`
- Starting SHA: `f9fc79dc4cd69f4d01553466439306aea7b48039`
- Initial status: clean, ahead of `origin/feature/colab-multicompany-anonymization` by 6 commits
- Stashes observed only, not modified:
  - `stash@{0}`: `wip failed rejected-report wire overharvest post_mask_hits_40`
  - `stash@{1}`: `wip failed overharvest direct privacy patch post_mask_hits_40`
  - `stash@{2}`: `wip reanon scaffold leftovers before 2d69 integration repair`

## Largest Files Before Cleanup

- `src/fenrix_synthetic/cli.py`: 2740 lines
- `src/fenrix_synthetic/reanonymize/orchestrator.py`: 1967 lines
- `scripts/build_submission_fast.py`: 1687 lines
- `tests/unit/test_discovery_core.py`: 1541 lines
- `tests/unit/test_gliner_provider.py`: 1455 lines
- `src/fenrix_synthetic/pipeline/runner.py`: 1408 lines
- `src/fenrix_synthetic/pilot/orchestrator.py`: 1251 lines
- `src/fenrix_synthetic/attacks/semantic_attacks.py`: 1187 lines
- `src/fenrix_synthetic/providers/nvidia_client.py`: 1140 lines

## Artifact Builder Usage Map

The previous `scripts/build_submission_fast.py` imported only stdlib, `requests`, `bs4`, and optional `yfinance`; it did not import local submission/anonymization modules. It wrote public raw-adjacent outputs directly:

- SEC: `latest_10_q_excerpt.md`, `latest_8_k_excerpt.md`, `companyfacts_summary.json`, and section files derived from raw SEC text
- News: `news_items.json` with headline/publisher/timestamp/summary/URL-shaped fields
- Metrics: exact statement CSVs derived from yfinance income statement, balance sheet, and cash flow data

The repaired path keeps the script as a thin wrapper and moves the sanitized builder into capped package modules.

## Safe Bloat Removed

Only tracked generated artifacts were deleted:

- `MagicMock/mock/*/exports/anonymized_bundle.zip`
- `anonymized_bundle/`
- `cleanup_stash_inventory.txt`

No source modules were deleted in this pass.

## Source Deletion Candidates Not Removed

These paths look outside the current fast artifact path, but broad deletion was deferred because tests/import tracing have not proven they are unused across the branch:

- `src/fenrix_synthetic/reanonymize/`
- `src/fenrix_synthetic/release/`
- `src/fenrix_synthetic/providers/nvidia_*`
- `src/fenrix_synthetic/pilot/`
- `src/fenrix_synthetic/pipeline/runner.py`
- `src/fenrix_synthetic/collectors/`
- `src/fenrix_synthetic/transforms/`
- `src/fenrix_synthetic/utility/`
- `src/fenrix_synthetic/anonymization/`
- `src/fenrix_synthetic/atlas/`
- `src/fenrix_synthetic/attacks/semantic_attacks.py`

Recommended follow-up before deletion: run import graph checks, targeted test collection after each candidate removal, and decide whether each path is still part of the branch's intended Phase 4/5 scope.

## Dependency Decision

- `sec-parser`, `edgartools`, and Presidio are available on PyPI but were not installed locally.
- No heavy default dependency was added.
- `yfinance` remains optional under the new `submission` extra because the fast public artifact build uses it for live metrics/news, while unit tests stay deterministic and network-isolated.
- SEC parsing uses the existing installed `beautifulsoup4`/`lxml` stack plus strict content rejection and focused regex scrubbers.
