# Cleanup Report Update — 2026-06-21

## Largest Files (tracked)

| File | Size | Notes |
|------|------|-------|
| `src/fenrix_synthetic/cli.py` | 92K | Active CLI; not deleted |
| `src/fenrix_synthetic/reanonymize/orchestrator.py` | 88K | Deferred reanonymize path; superseded by submission_fast |
| `tests/unit/test_discovery_core.py` | 60K | Active Phase 3B tests |
| `src/fenrix_synthetic/pipeline/runner.py` | 60K | Active pipeline runner |
| `tests/unit/test_gliner_provider.py` | 56K | Phase 3C GLiNER tests; preserved per Decision 022-029 |
| `src/fenrix_synthetic/pilot/orchestrator.py` | 56K | Pilot orchestrator; deferred |
| `src/fenrix_synthetic/release/classroom_build.py` | 44K | Classroom package builder; active |
| `src/fenrix_synthetic/providers/nvidia_client.py` | 44K | Full-document NVIDIA orchestrator; NOT revived per Phase 2 spec |

## Unused Scripts

No unused scripts found. `scripts/build_submission_fast.py` is the only script and is referenced by tests and the Colab notebook.

## Duplicate Artifact Builders

No duplicate artifact builders found. `submission_fast.py` is the active builder; `reanonymize/orchestrator.py` is the deferred full-document path.

## Generated Artifact Files Tracked by Git

None. No ZIPs, CSVs, or generated JSON artifacts are tracked. `configs/companies.csv` is a config file, not generated.

## Stale Docs

- `docs/DECISIONS.md` Decision 031 references the failed reanonymize `post_mask_hits_40` work — kept as historical record.
- No stale docs referring to failed old pipelines were deleted.

## Stale NVIDIA/Reanonymize Full-Document Paths

- `src/fenrix_synthetic/reanonymize/` — deferred, not deleted (Decision 031 documents the open follow-up).
- `src/fenrix_synthetic/providers/nvidia_client.py` — the full-document orchestrator; NOT revived. The new `submission_nvidia.py` is a minimal verifier, separate from this module.

## What Was Deleted Now

- **Local branches (merged, 0 unique commits)**:
  - `chore/github-actions-quality-gates`
  - `feature/c001-deterministic-masking`
  - `feature/residual-entity-discovery`
  - `feature/reviewed-entity-discovery`
- **Local branches (unique commits, patches archived)**:
  - `feature/anonymity-threat-model-pilot` (13 unique commits; patch exported)
  - `feature/local-gliner-adapter` (8 unique commits; patch exported)
  - `feature/s3-feature-only-private-evaluator` (16 unique commits; patch exported)
- **Stashes (all failed/obsolete, patches exported)**:
  - `stash@{0}`: wip failed rejected-report wire overharvest post_mask_hits_40
  - `stash@{1}`: wip failed overharvest direct privacy patch post_mask_hits_40
  - `stash@{2}`: wip reanon scaffold leftovers before 2d69 integration repair

## What Is Deferred

- Remote branch cleanup — deferred until final PR is merged into `main`.
- `reanonymize/` module cleanup — deferred per Decision 031 open follow-up.
- `providers/nvidia_client.py` cleanup — deferred; may be needed for future Phase 4 work.
- `pilot/orchestrator.py` cleanup — deferred; not in current scope.
- GLiNER Phase 3C — preserved per Decisions 022-029.

## Archive

Stash and branch patches exported to `archive/stashes/`:
- `stash_0_wip_failed_rejected-report_wire_overharv.patch`
- `stash_1_wip_failed_overharvest_direct_privacy_pa.patch`
- `stash_2_wip_reanon_scaffold_leftovers_before_2d6.patch`
- `branch_feature_anonymity-threat-model-pilot.patch`
- `branch_feature_local-gliner-adapter.patch`
- `branch_feature_s3-feature-only-private-evaluator.patch`
