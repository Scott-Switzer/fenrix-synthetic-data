# 8-Company Submission Artifact Closeout

Date: 2026-06-21

## Repository State

- Branch: `feature/colab-multicompany-anonymization`
- Validation/code SHA before this closeout commit: `faf7398d051b8e7bc93a6ce2fd0ff2ac1a7f5d3d`
- Upstream: `origin/feature/colab-multicompany-anonymization`
- Push status before closeout commit: local branch ahead of upstream by 5 commits.
- No push performed.

Unpushed commits before this closeout commit:

```text
faf7398 feat: build fast 8-company anonymized submission
c3a299d chore: clean repo hygiene before submission build
dbc398a feat: add fast NVIDIA smoke-slice primitive
51e555e fix: make NVIDIA review bounded and fail-closed
12dda6b feat: add NVIDIA gated review and secret-safe API handling
```

## Artifact

- Output folder: `/tmp/scottthomasswitzer/Desktop/FENRIX_8_COMPANY_ANON_SUBMISSION_20260621_140041`
- ZIP path: `/tmp/scottthomasswitzer/Desktop/FENRIX_8_COMPANY_ANON_SUBMISSION_20260621_140041/exports/anonymized_bundle.zip`
- ZIP byte size: `834258`
- ZIP entry count: `183`
- Artifact verdict: `PASS`
- NVIDIA verdict: `INCOMPLETE`

## ZIP Structure Validation

Command: `unzip -t /tmp/scottthomasswitzer/Desktop/FENRIX_8_COMPANY_ANON_SUBMISSION_20260621_140041/exports/anonymized_bundle.zip`

Result: `PASS`

Top-level ZIP entries:

```text
DATA_DICTIONARY.md
LIMITATIONS.md
QUICKSTART.md
README.md
RUN_SUMMARY.md
anonymized/
artifact_inventory.csv
checksums.sha256
qa/
run_summary.json
```

Required structure:

| Check | Status |
| --- | --- |
| Docs present | PASS |
| `anonymized/` present | PASS |
| `qa/` present | PASS |
| `originals/` absent | PASS |
| `private_maps/` absent | PASS |
| `smoke_excerpts/` absent | PASS |
| `.env` absent | PASS |
| `__pycache__` absent | PASS |

## Deep Text Scan

Public text bodies scanned: `.md`, `.json`, `.csv`, `.txt`, `.sha256`.

| Forbidden body value | Status |
| --- | --- |
| `nvapi-` | PASS: zero hits |
| `NVIDIA_API_KEY` | PASS: zero hits |
| `/tmp/` | PASS: zero hits |
| `/tmp/` | PASS: zero hits |
| `private_maps` | PASS: zero hits |
| `originals/` | PASS: zero hits |
| `smoke_excerpts/` | PASS: zero hits |
| Standalone target tickers in public text bodies | PASS: zero hits |

Harmless substring candidates were found only as non-standalone substrings in SEC excerpts:

```text
CHC6: anonymized/CHC6/sec/latest_8_k_excerpt.md
CL: anonymized/CL/sec/latest_10_q_excerpt.md, anonymized/CL/sec/latest_8_k_excerpt.md
CHC: anonymized/CHC/sec/latest_8_k_excerpt.md
CHC2: anonymized/CHC2/sec/latest_10_q_excerpt.md, anonymized/CHC2/sec/latest_8_k_excerpt.md
PM: anonymized/PM/sec/latest_8_k_excerpt.md
```

Validation bug fixed during closeout: public metadata bodies no longer include raw ticker symbols in `artifact_inventory.csv`, `checksums.sha256`, or `run_summary.json`; they use pseudonymized path IDs and ticker IDs.

## Product Completeness

| Input ticker | Public ticker ID | Metrics files | SEC files | News files | QA files | Metrics | SEC | News | Residual | NVIDIA |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| CL | `TICKER_001` | 10 | 9 | 1 | 1 | OK | OK | OK | PASS | INCOMPLETE |
| CHC2 | `TICKER_002` | 10 | 9 | 1 | 1 | OK | OK | OK | PASS | INCOMPLETE |
| CHC3 | `TICKER_003` | 10 | 9 | 1 | 1 | OK | OK | OK | PASS | INCOMPLETE |
| PM | `TICKER_004` | 10 | 9 | 1 | 1 | OK | OK | OK | PASS | INCOMPLETE |
| CHC5 | `TICKER_005` | 10 | 9 | 1 | 1 | OK | OK | OK | PASS | INCOMPLETE |
| CHC | `TICKER_006` | 10 | 9 | 1 | 1 | OK | OK | OK | PASS | INCOMPLETE |
| CHC6 | `TICKER_007` | 10 | 5 | 1 | 1 | OK | INCOMPLETE | OK | PASS | INCOMPLETE |
| CHC8 | `TICKER_008` | 10 | 9 | 1 | 1 | OK | OK | OK | PASS | INCOMPLETE |

CHC6 SEC is documented as `INCOMPLETE` in `run_summary.json`; available SEC public files are present and included.

## Docs Validation

| File | Required content | Status |
| --- | --- | --- |
| `README.md` | States this is not a mathematical anonymity guarantee | PASS |
| `LIMITATIONS.md` | States bounded NVIDIA QA and semantic clues may remain | PASS |
| `RUN_SUMMARY.md` | Lists attempted/completed ticker IDs | PASS |
| `DATA_DICTIONARY.md` | Explains pseudonyms and NVIDIA QA | PASS |
| `QUICKSTART.md` | Explains how to inspect the ZIP and QA | PASS |

## Quality Gates

All commands were run from `/tmp/scottthomasswitzer/Documents/GitHub/fenrix-synthetic-data` using the repo `.venv`.

| Command | Result |
| --- | --- |
| `python -m ruff format --check .` | PASS: 188 files already formatted |
| `python -m ruff check .` | PASS |
| `python -m mypy src/fenrix_synthetic` | PASS: no issues in 131 source files |
| `python -m pytest --disable-socket -q` | PASS: 952 passed, 5 skipped, 9 warnings |
| `python -m compileall src` | PASS |
| `git diff --check` | PASS |

Warnings remaining in tests:

- Unknown `pytest.mark.timeout` in `tests/integration/test_phase4_vertical_slice.py`.
- `nbformat` missing cell ID warning in classroom notebook tests.
- Expected `pytest_socket` warnings in socket-blocking tests.

## Known Limitations

- No mathematical anonymity is claimed.
- NVIDIA QA is `INCOMPLETE` because the bounded provider review did not run successfully/configure as a pass condition.
- NVIDIA is not product-blocking for ZIP creation.
- CHC6 SEC extraction is partial: SEC status is `INCOMPLETE`, but metrics/news/QA are present and the failure is documented.
- Residual scan is literal-only and does not prove semantic anonymity.
- Numeric fingerprints, business model details, and public filing structure may remain identifying.
- Public ZIP entry paths intentionally retain the required `anonymized/<ticker>/...` folder contract; public text metadata uses pseudonymized IDs.

## Branches Likely Stale Or Requiring Review

No branches were deleted.

```text
chore/github-actions-quality-gates                       423a845e93e3fdb97183f276e3d923de67c60be3
feature/anonymity-threat-model-pilot                     75cc8527173112403937ae03e9b34c613dbbd08c
feature/c001-deterministic-masking                       d1b1c198e41a8bfb4a1fa417ad3db1de131b6bcf
feature/local-gliner-adapter                             5234333f6bdf2b0e8018c921fe39aeb53d520378
feature/residual-entity-discovery                        8c68dcaa71a0f6ab52182930752afeb68967a1bf
feature/reviewed-entity-discovery                        890ea7a701100e89d35f2614da7c54b9bc23b0a7
feature/s3-feature-only-private-evaluator                2631cd497f630045d9fc87e714f60dd8e981a1f5 local ahead 3
opencode/curious-sailor                                  f9c40d5b7421e3bca262a6455509379cbae34052
safety/pre-cleanup-20260621-133447                       dbc398a60a0fcd856f93fb567b0fc3afb8239bd5
```

## Stash Inventory

No stashes were dropped.

```text
stash@{0}: On feature/colab-multicompany-anonymization: wip failed rejected-report wire overharvest post_mask_hits_40
stash@{1}: On feature/colab-multicompany-anonymization: wip failed overharvest direct privacy patch post_mask_hits_40
stash@{2}: On feature/colab-multicompany-anonymization: wip reanon scaffold leftovers before 2d69 integration repair
```

## What Not To Touch Next

- Do not push without explicit approval.
- Do not delete Desktop run folders.
- Do not delete branches or drop stashes.
- Do not modify the deployed FENRIX Render application.
- Do not modify PPE, Bloomberg x Zion, Hermes, or Finfluencer Alpha.
- Do not claim anonymity from this artifact.
- Do not put `originals/`, `private_maps/`, secrets, API keys, or raw filings into the ZIP.
- Do not switch back to the full-document NVIDIA/reanonymize orchestrator as the main product path.
- Do not start new feature work until this ZIP is reviewed as the product deliverable.
