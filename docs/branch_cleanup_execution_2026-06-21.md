# Branch Cleanup Execution — 2026-06-21

## Pre-Cleanup State

### Local Branches (before cleanup)

| Branch | HEAD | Status |
|--------|------|--------|
| `chore/github-actions-quality-gates` | 423a845 | merged into main, 0 unique |
| `feature/anonymity-threat-model-pilot` | 75cc852 | 13 unique commits vs main |
| `feature/c001-deterministic-masking` | d1b1c19 | merged into main, 0 unique |
| `feature/colab-multicompany-anonymization` | e772d55 | active (ahead 10) |
| `feature/local-gliner-adapter` | 5234333 | 8 unique commits vs main |
| `feature/residual-entity-discovery` | 8c68dca | merged into main, 0 unique |
| `feature/reviewed-entity-discovery` | 890ea7a | merged into main, 0 unique |
| `feature/s3-feature-only-private-evaluator` | 2631cd4 | 16 unique commits vs main |
| `main` | 06bd468 | base |
| `opencode/curious-sailor` | f9c40d5 | opencode worktree |
| `opencode/jolly-orchid` | 222b715 | opencode worktree |
| `safety/pre-cleanup-20260621-133447` | dbc398a | safety branch |
| `safety/pre-final-release-20260621-160957` | f871700 | safety branch |

### Stashes (before cleanup)

| Stash | Description |
|-------|-------------|
| `stash@{0}` | wip failed rejected-report wire overharvest post_mask_hits_40 |
| `stash@{1}` | wip failed overharvest direct privacy patch post_mask_hits_40 |
| `stash@{2}` | wip reanon scaffold leftovers before 2d69 integration repair |

## Classification

### KEEP

- `main` — base branch
- `feature/colab-multicompany-anonymization` — active working branch
- `safety/pre-cleanup-20260621-133447` — safety branch
- `safety/pre-final-release-20260621-160957` — safety branch
- `opencode/curious-sailor` — opencode-managed worktree
- `opencode/jolly-orchid` — opencode-managed worktree

### DELETE_LOCAL_NOW (merged, 0 unique commits)

- `chore/github-actions-quality-gates` — deleted with `git branch -d`
- `feature/c001-deterministic-masking` — deleted with `git branch -d`
- `feature/residual-entity-discovery` — deleted with `git branch -d`
- `feature/reviewed-entity-discovery` — deleted with `git branch -d`

### ARCHIVE_PATCH_FIRST (unique commits, patches exported, then deleted)

- `feature/anonymity-threat-model-pilot` — 13 unique commits; patch exported to `archive/stashes/branch_feature_anonymity-threat-model-pilot.patch`; deleted with `git branch -D`
- `feature/local-gliner-adapter` — 8 unique commits; patch exported to `archive/stashes/branch_feature_local-gliner-adapter.patch`; deleted with `git branch -D`
- `feature/s3-feature-only-private-evaluator` — 16 unique commits; patch exported to `archive/stashes/branch_feature_s3-feature-only-private-evaluator.patch`; deleted with `git branch -D`

### DELETE_REMOTE_AFTER_PR_MERGE

- `origin/feature/anonymity-threat-model-pilot`
- `origin/feature/c001-deterministic-masking`
- `origin/feature/local-gliner-adapter`
- `origin/feature/residual-entity-discovery`
- `origin/feature/reviewed-entity-discovery`
- `origin/feature/s3-feature-only-private-evaluator`
- `origin/chore/github-actions-quality-gates`
- `origin/feature/colab-multicompany-anonymization` — delete after PR merges

### DROP_STASH_AFTER_ARCHIVE (patches exported, then dropped)

- `stash@{0}` — patch exported; dropped
- `stash@{1}` — patch exported; dropped
- `stash@{2}` — patch exported; dropped

## Post-Cleanup State

### Local Branches (after cleanup)

| Branch | HEAD | Notes |
|--------|------|-------|
| `feature/colab-multicompany-anonymization` | e772d55 | active |
| `main` | 06bd468 | base |
| `opencode/curious-sailor` | f9c40d5 | opencode worktree |
| `opencode/jolly-orchid` | 222b715 | opencode worktree |
| `safety/pre-cleanup-20260621-133447` | dbc398a | safety |
| `safety/pre-final-release-20260621-160957` | f871700 | safety |

### Stashes (after cleanup)

None remaining.

## Archive

All patches exported to `archive/stashes/`:
- `stash_0_wip_failed_rejected-report_wire_overharv.patch` (1189 lines)
- `stash_1_wip_failed_overharvest_direct_privacy_pa.patch` (1059 lines)
- `stash_2_wip_reanon_scaffold_leftovers_before_2d6.patch` (632 lines)
- `branch_feature_anonymity-threat-model-pilot.patch` (16426 lines)
- `branch_feature_local-gliner-adapter.patch` (8017 lines)
- `branch_feature_s3-feature-only-private-evaluator.patch` (25884 lines)

## Remote Branches

Remote branches were NOT deleted. They will be cleaned up after the final PR merges into `main`.
