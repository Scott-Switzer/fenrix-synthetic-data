# FENRIX Synthetic Data — Project Contract

## 1. Pilot Scope

This contract governs the Phase 4 anonymity pilot for one private source company.

| Field | Value |
|-------|-------|
| Internal source identifier | `SRC_001` |
| Release identifier | `SYNTH_001` |
| Analysis unit | Company / stock |
| Target history | 7–8 years of financial data |
| Data required | Structured (OHLCV, ratios) and unstructured (filings, news) |
| Branch | `feature/anonymity-threat-model-pilot` |

## 2. Non-Negotiable Constraints

### 2.1 No Committed Private Data

- No raw source data may be committed to the repository.
- No private company mapping (real name, ticker, CIK, EIN, LEI) may be committed.
- `SRC_001` identifiers replace real company identifiers in all tracked files.
- Generated data directories (`raw/`, `bronze/`, `silver/`, `gold/`, `release/`) are `.gitignore`-covered.

### 2.2 No Hardcoded Real Identifiers

The following must never appear in tracked source code, configs, test fixtures, or documentation:

- Exact names, tickers, people, products, locations, legal identifiers, URLs, metadata
- Semantic fingerprints (acquisitions, legal proceedings, distinctive events, unique segment names, slogans, named partnerships)
- Model weights, raw filings, raw prices, private mappings, provider credentials

### 2.3 No Automatic Model Trust

- GLiNER and other models are discovery systems, not privacy authorities.
- No model candidate may be auto-accepted, auto-promoted, or auto-remasked.
- All model output enters the review queue only.
- Confidence scores are not calibrated leakage probability.

### 2.4 Release Safety Requires Attack Testing

- A release candidate must pass the configured re-identification attack suite.
- Missing evidence is NOT converted into a pass.
- The release gate must fail when any blocking condition is present.

### 2.5 Pipeline Gating

- The LLM signal/trading pipeline is blocked until the data release pipeline is demonstrated.
- Synthetic unstructured expansion is blocked until the real-data pipeline is demonstrated.
- Structured-price privacy is explicitly part of the threat model.

## 3. Quality Guarantees Preserved

The following guarantees from prior phases must not be weakened:

- ruff check: clean
- mypy: 0 errors across source files
- pytest --disable-socket: all tests pass offline
- No live API calls in unit tests
- Deterministic offline tests
- No model candidate auto-acceptance
- No model candidate registry mutation
- No model candidate automatic remasking
- No release claim of anonymity or safety without attack evidence

## 4. Private Data Boundary

All real source data, identity mappings, and private intermediate artifacts must live under:

```
FENRIX_PRIVATE_ROOT
```

This path must:

- Be configured through the `FENRIX_PRIVATE_ROOT` environment variable.
- Reside outside the Git repository.
- Never be committed or tracked.
- Be rejected if it resolves inside the repository or through a symlink escape.
- Be rejected if the environment variable is missing or invalid.

## 5. Release Dossier Structure

Release candidates are written to:

```
release/SYNTH_001/
  README.md
  manifest.json
  structured/
  unstructured/
  privacy_report.json
  utility_report.json
  attack_summary.json
  transformation_summary.json
  release_decision.json
  checksums.json
```

The dossier must contain:

- No real company identity
- No raw source data
- No private path
- No private replacement map
- No unredacted attack guesses
- Explicit structured-transformation disclosure
- Explicit limitations
- Reproducibility hashes
- Policy version
- Source hashes represented only through safe one-way hashes
- Model and tool provenance

## 6. Version

| Field | Value |
|-------|-------|
| Contract version | `1.0.0` |
| Pilot policy version | `pilot_v1` |
| Effective date | 2026-06-19 |
