# Colab Quickstart: FENRIX Multi-Company Anonymization

This guide walks through reproducing the masked/anonymized submission artifact using the Colab notebook.

## Open in Colab

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Scott-Switzer/fenrix-synthetic-data/blob/feature/colab-multicompany-anonymization/notebooks/FENRIX_MultiCompany_Anonymization_Reproducible.ipynb)

Notebook path: `notebooks/FENRIX_MultiCompany_Anonymization_Reproducible.ipynb`

## Prerequisites

### Required: SEC User Agent

The SEC EDGAR fair-access policy requires a User-Agent header with a contact email. Set this before running the build:

- In Colab: add a code cell with `os.environ["SEC_USER_AGENT"] = "Your Name your-email@example.com"`
- Or set it as a Colab secret named `SEC_USER_AGENT`

Without a valid user agent, SEC downloads will fail.

### Optional: NVIDIA API Key

NVIDIA artifact verification is optional. If `NVIDIA_API_KEY` is set, the verifier reviews public artifact text and produces QA reports. If absent, the build still succeeds with QA status `INCOMPLETE`.

- In Colab: add as a secret named `NVIDIA_API_KEY` (notebook reads it via `os.environ`)
- The key is never printed, logged, or written to artifacts
- The verifier only receives public artifact text, never originals or private maps

## Reproducing the Artifact

1. Open the notebook in Colab (link above).
2. Run cells in order:
   - **Cell 1**: Clones the repo and installs dependencies.
   - **Cell 2**: Configures `SEC_USER_AGENT` (edit to your contact email).
   - **Cell 3**: Runs `build_submission_fast.py` for 8 tickers (CHC1, CHC2, CHC3, CHC4, CHC5, CHC, CHC6, CHC8).
   - **Cell 4**: Runs local validation scans (SEC cover-page scan + broad scan).
   - **Cell 5**: Previews COMPANY_001 sanitized summaries.
   - **Cell 6**: Downloads `anonymized_bundle.zip`.

## Final ZIP Location

The ZIP is written to:
```
<output_root>/exports/anonymized_bundle.zip
```

In Colab, the default output root is `/tmp/fenrix_output`.

## What the Artifact Contains

- `anonymized/COMPANY_001..008/sec/*.md` — sanitized SEC summaries
- `anonymized/COMPANY_001..008/news/news_briefs.json` — sanitized news briefs
- `anonymized/COMPANY_001..008/metrics/fundamentals_binned.csv` — binned metrics
- `anonymized/COMPANY_001..008/qa/nvidia_review.json` — per-company NVIDIA QA
- `qa/nvidia_artifact_review.json` — bundle-level NVIDIA review
- `qa/zip_validation.json` — ZIP content validation
- `run_summary.json`, `artifact_inventory.csv`, `checksums.sha256`

## What the Artifact Excludes

- `originals/` — raw source files
- `private_maps/` — private-to-public identity mappings
- `smoke_excerpts/` — raw excerpts used during development
- API keys, local paths, raw filing bodies

## Limitations

- This is a **masked/anonymized submission artifact with stated limitations**.
- No mathematical anonymity guarantee is claimed or implied.
- NVIDIA QA is optional. Without `NVIDIA_API_KEY`, QA status is `INCOMPLETE`.
- The artifact is intended for research review, not production release.

## Troubleshooting

### SEC downloads fail
- Verify `SEC_USER_AGENT` is set to a real contact email.
- SEC may rate-limit; the builder throttles to 4 requests/second.

### NVIDIA QA shows INCOMPLETE
- This is expected when `NVIDIA_API_KEY` is not set. The build still succeeds.

### ZIP validation fails
- Check `qa/zip_validation.json` for which files were flagged.
- Re-run the build after fixing the flagged writer.

### Notebook cannot clone the repo
- Verify the branch name in cell 1 matches `feature/colab-multicompany-anonymization`.
- If the repo is private, set a GitHub token in `FENRIX_REPO_URL`.
