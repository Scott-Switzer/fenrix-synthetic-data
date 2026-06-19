# Phase 4 Runbook: Anonymity Threat Model Pilot (R3)

## 1. Purpose and Limitations

This runbook documents how to run the Phase 4 anonymity pilot pipeline,
which transforms private company data (`SRC_001`) into a pseudonymous
release candidate (`SYNTH_001`), runs re-identification attacks, evaluates
utility, and produces a release decision.

**This pipeline does NOT claim cryptographic anonymity.** It is a
feasibility pilot that demonstrates deterministic masking, structured
privacy transformations, and evidence-based release gating.

**Critical caveat:** If S1 and S2 both rank the true source first in the
candidate universe, structured-data anonymization has failed. The pilot
results are usable evidence of failure, not a release green light.

---

## 2. Canonical Private Directory Structure

```
FENRIX_PRIVATE_ROOT/
├── sources/
│   └── SRC_001/
│       ├── source_manifest.yaml       # REQUIRED (mandatory for all pilots)
│       ├── identity_atlas.yaml        # REQUIRED
│       ├── structured/
│       │   ├── prices.json            # OHLCV data
│       │   ├── candidate_universe.json # Peer returns for ranking attack
│       │   ├── market_reference.json  # Broad-market index returns (SPY etc.)
│       │   └── sector_reference.json  # Sector index returns (XLF etc.)
│       └── unstructured/
│           ├── documents.jsonl        # SEC filings and transcripts (JSONL)
│           └── headlines.jsonl        # News headlines (JSONL)
├── runs/
│   └── run-SRC_001-{timestamp}/
│       ├── private/                   # Private intermediate artifacts
│       │   ├── run_manifest.json      # All 18 stages with statuses
│       │   ├── evidence_manifest.json # Evidence hashes for the gate
│       │   ├── structured_attacks.json
│       │   ├── structured_utility.json
│       │   ├── text_attacks.json
│       │   ├── utility.json
│       │   ├── release_decision.json
│       │   └── {doc_id}_masked.txt    # One per document
│       └── exports/
└── exports/
    └── SYNTH_001/                     # Sanitized release dossier
        ├── README.md
        ├── manifest.json
        ├── privacy_report.json
        ├── utility_report.json
        ├── attack_summary.json
        ├── transformation_summary.json
        ├── release_decision.json
        ├── checksums.json
        ├── unstructured/              # Masked text documents
        └── structured/                # Transformed price series
```

---

## 3. FENRIX_PRIVATE_ROOT Setup

```bash
export FENRIX_PRIVATE_ROOT=/absolute/path/outside/repo
mkdir -p "$FENRIX_PRIVATE_ROOT/sources/SRC_001"/{structured,unstructured}
mkdir -p "$FENRIX_PRIVATE_ROOT"/{runs,exports}
```

Run the boundary diagnostic to verify:

```bash
python -m fenrix_synthetic.cli boundary-diag
```

Expected output (redacted paths):
```json
{
  "private_root_configured": true,
  "inside_repo": false,
  "private_root_valid": true,
  "private_root_location": "[PRIVATE_ROOT]"
}
```

---

## 4. Source-Manifest Schema

**REQUIRED** — the pilot fails immediately if this file is missing.
Located at `sources/SRC_001/source_manifest.yaml`:

```yaml
schema_version: "1.0.0"
source_id: SRC_001
readiness_state: approved_for_private_pilot
data_start: "PRIVATE"
data_end: "PRIVATE"
expected_history_years: 8

identity_atlas: identity_atlas.yaml

structured:
  prices: structured/prices.json
  candidate_universe: structured/candidate_universe.json
  market_reference: structured/market_reference.json
  sector_reference: structured/sector_reference.json

unstructured:
  documents: unstructured/documents.jsonl
  headlines: unstructured/headlines.jsonl

provenance:
  structured_source: private_internal
  filing_source: private_internal
  news_source: private_internal
```

The manifest is validated by the orchestrator in **Stage 2**
(`validate_source_manifest`). Missing → `FAIL` immediately.

---

## 5. Identity-Atlas Schema

Required YAML file at `sources/SRC_001/identity_atlas.yaml`.

Each entry must have:
- `entry_id`: unique identifier
- `category`: one of `issuer`, `people`, `organizations`, `products`,
  `locations`, `digital`, `semantic_fingerprints`
- `sub_type`: specific type within category
- `private_value`: the actual private string
- `normalized_value`: NFKC-normalized form
- `match_policy`: one of `exact`, `normalized`, `case_insensitive`,
  `fuzzy`, `possessive`, `punctuation_variant`, `whitespace_variant`,
  `abbreviation`, `domain`, `url`, `phone`, `regex`
- `priority`: integer 1-100 (higher = wins conflicts)
- `reason`: why this entry exists
- `reviewer_id`: who approved it

Categories and required coverage for real pilots:
| Category | Minimum | Examples |
|----------|---------|---------|
| `issuer` | 1+ legal name, 1+ ticker/CIK | Current name, former names, ticker, CIK, LEI |
| `people` | Key executives | CEO, CFO, board members |
| `organizations` | Subsidiaries, auditors | Acquired companies, accounting firm |
| `products` | Major brands, services | Product lines, service names |
| `locations` | HQ, key sites | Headquarters address, major campuses |
| `digital` | Domains, URLs | Website, email domains, social media |
| `semantic_fingerprints` | Distinctive phrases | Taglines, unique disclosures |

An empty atlas is **REJECTED** for real pilots.

All categories must be explicitly declared:
- Include `reviewed_none_found: true` for categories with no entries
- Atlas must have version, reviewer, approval state, creation timestamp

---

## 6. Identity Atlas Population Guidance

Do not rely on GLiNER to populate the atlas automatically. Use it
afterward to find omissions.

Include:
- Current and former legal names
- Ticker and exchange forms
- CIK, SIC, and other identifiers
- Executives and directors
- Subsidiaries and acquired businesses
- Product and service names
- Headquarters and distinctive locations
- Domains, URLs, and email domains
- Auditors and transfer agents
- Unique segment names
- Named partnerships
- Major acquisitions
- Distinctive litigation or regulatory events
- Common abbreviations and punctuation variants

---

## 7. Normalized Document Format

### Text documents

JSONL file at `unstructured/documents.jsonl`:

```jsonl
{"document_id": "hban-10k-2025", "document_type": "10-K", "text": "...", "source": "sec"}
{"document_id": "hban-10q-2025q3", "document_type": "10-Q", "text": "...", "source": "sec"}
```

Each record:
- `document_id`: unique identifier
- `document_type`: `10-K`, `10-Q`, `8-K`, `news_headline`, `earnings_release`, `earnings_transcript`
- `text`: normalized plain text (NFKC unicode), no raw HTML, no PDF binaries
- `source`: provenance identifier

### Headlines

JSONL file at `unstructured/headlines.jsonl`:

```jsonl
{"headline_id": "news-001", "text": "Huntington Bancshares reports Q3 earnings beat", "date": "2025-10-15", "source": "marketaux"}
```

---

## 8. Structured OHLCV Format

JSON file at `structured/prices.json`:

```json
{
  "records": [
    {
      "date": "2025-01-15",
      "open": 100.0,
      "high": 102.5,
      "low": 99.0,
      "close": 101.2,
      "volume": 1500000.0
    }
  ]
}
```

Fields:
- `date`: ISO date string (YYYY-MM-DD)
- `open`, `high`, `low`, `close`: float prices
- `volume`: float (can be fractional after adjustments)
- 7-8 years of daily data recommended

---

## 9. Candidate-Universe Format

JSON file at `structured/candidate_universe.json`:

```json
{
  "universe_id": "univ-v1",
  "candidates": [
    {
      "candidate_id": "PEER-0001",
      "returns": [0.001, -0.002, 0.0015, ...]
    },
    {
      "candidate_id": "SRC_001",
      "returns": [0.0005, 0.001, -0.001, ...]
    }
  ]
}
```

- Returns are **log returns** computed from close prices
- All candidates must have the same number of observations
- At least 100 candidates recommended for meaningful rankings
- Include true source (`SRC_001`) among the candidates
- Deterministic distractors with similar vol/beta, unrelated tickers, and
  shifted near-copy series improve attack quality

---

## 10. Market-Reference Format

JSON file at `structured/market_reference.json`:

```json
{
  "records": [
    {
      "date": "2025-01-15",
      "open": 450.0,
      "high": 452.0,
      "low": 448.0,
      "close": 451.5,
      "volume": 100000000.0
    }
  ]
}
```

Use a broad-market ETF such as SPY. Same date range as OHLCV data.
Required for S2 to produce `s2_privacy` (not `s2_incomplete_reference_data`).

---

## 11. Sector-Reference Format

JSON file at `structured/sector_reference.json`:

```json
{
  "records": [
    {
      "date": "2025-01-15",
      "open": 35.0,
      "high": 35.5,
      "low": 34.8,
      "close": 35.2,
      "volume": 50000000.0
    }
  ]
}
```

Use a sector ETF matching the company's industry (e.g., XLF for banks).
Same date range as OHLCV data.

---

## 12. Boundary Diagnostic

Before any pilot run, verify the private boundary:

```bash
python -m fenrix_synthetic.cli boundary-diag
```

This checks:
- `FENRIX_PRIVATE_ROOT` is set
- Path exists and is outside the Git repository
- No symlink escape issues

---

## 13. Atlas Validation

The pipeline validates atlas completeness during `pilot-run`.
To validate standalone:

```bash
python -m fenrix_synthetic.cli identities-compile \
  --atlas $FENRIX_PRIVATE_ROOT/sources/SRC_001/identity_atlas.yaml \
  --output $FENRIX_PRIVATE_ROOT/silver/replacement_plan.json
```

---

## 14. Full Private Pilot-Run Command

Exact command:

```bash
export FENRIX_PRIVATE_ROOT="/absolute/path/outside/the/repository"

python -m fenrix_synthetic.cli pilot-run \
  --source-id SRC_001 \
  --release-id SYNTH_001 \
  --candidate-universe \
    "$FENRIX_PRIVATE_ROOT/sources/SRC_001/structured/candidate_universe.json" \
  --market-reference \
    "$FENRIX_PRIVATE_ROOT/sources/SRC_001/structured/market_reference.json" \
  --sector-reference \
    "$FENRIX_PRIVATE_ROOT/sources/SRC_001/structured/sector_reference.json" \
  --offline
```

**Do not manually invoke individual stages** unless debugging a failure.

All flags:
| Flag | Required | Purpose |
|------|----------|---------|
| `--source-id` | Yes | Source company identifier |
| `--release-id` | Yes | Release identifier |
| `--private-root` | No (defaults to env var) | Explicit private root |
| `--candidate-universe` | Yes for structured attacks | Peer returns |
| `--market-reference` | Recommended for S2 | Market index |
| `--sector-reference` | Recommended for S2 | Sector index |
| `--policy` | No | Release policy YAML path |
| `--offline` | Yes for private data | No network calls |
| `--force` | No | Rerun even if stages exist |
| `--run-id` | No | Explicit run ID |

---

## 15. Run ID and Reports Location

Run ID format: `run-SRC_001-{YYYYMMDD}T{HHMMSS}`

All results are under `$FENRIX_PRIVATE_ROOT/runs/{run_id}/private/`:

| File | Contents |
|------|----------|
| `run_manifest.json` | All 18 stage statuses, overall status |
| `evidence_manifest.json` | Evidence hashes for gate assessment |
| `structured_attacks.json` | Per-variant rank, percentile, correlations |
| `structured_utility.json` | Per-variant utility metrics |
| `text_attacks.json` | Exact and digital identifier scan results |
| `utility.json` | Unstructured utility per document |
| `release_decision.json` | Gate PASS / FAIL / REVIEW_REQUIRED |
| `{doc_id}_masked.txt` | Masked output per document |

**Always reference runs by exact run_id.** Do not assume "latest run."

---

## 16. Stage Status Meanings

| Status | Meaning |
|--------|---------|
| `passed` | Stage completed successfully |
| `failed` | Stage failed (blocking) |
| `review_required` | Stage passed but has non-blocking warnings |
| `skipped_optional` | Stage skipped because optional component missing |
| `skipped_not_configured` | Stage skipped because required config missing (→ FAIL for required stages) |
| `blocked_upstream` | Stage blocked by previous failure |
| `error` | Unexpected error |

A pilot is `completed` only when no stage is `failed`, `error`, or
`skipped_not_configured` for required stages.

---

## 17. S2 Behavior With Missing or Incomplete References

S2 requires market and/or sector reference data for genuine residual
regression. Behavior depends on what is available:

**No references provided:**
- Stage status: `review_required`
- S2 variant: `s2_incomplete_reference_data`
- Returns true immediately with warning
- No residual removal performed
- Dossier marks S2 as non-releasable

**References provided but insufficient overlap (< 60 days):**
- Stage status: `review_required`
- S2 variant: `s2_incomplete_reference_data`
- Warning includes overlap length

**Full references available:**
- Stage status: `passed`
- S2 variant: `s2_privacy`
- Performs OLS regression on in-sample fit window (60 days)
- Removes market and sector components
- Winsorizes residuals at 2.5th/97.5th percentiles
- Reconstructs pseudo-price index

---

## 18. Resume Invalidation Rules

The `--resume` flag is implemented with hash validation:

- **Each stage stores input hashes and config hashes**
- A stage is reusable only when ALL of the following match:
  1. Source data hash matches
  2. Configuration hash matches
  3. All output artifacts exist and their hashes validate
  4. Pipeline version matches
- **Changed input or configuration invalidates the checkpoint**
- **Corrupted output artifact invalidates the checkpoint**
- **Dependent stages must also be invalidated** (if source manifest changes,
  all downstream stages re-run)

The current implementation is single-run (no resume across runs).
Resume safety will be fully validated in a follow-up milestone.

---

## 19. PASS, FAIL, REVIEW_REQUIRED Interpretation

| Decision | Meaning | Export allowed? |
|----------|---------|----------------|
| **PASS** | All blocking conditions met. Dossier may be exported. | Yes (shareable) |
| **FAIL** | At least one blocking condition failed. | No (blocked) |
| **REVIEW_REQUIRED** | Non-blocking warnings exist. Private dossier may be generated. | No (not shareable) |

### Blocking conditions:
1. Exact identity match found in masked text
2. Unique phrase / semantic fingerprint hit exceeds threshold
3. Digital identifier found in masked text
4. Source filename/metadata identifier found
5. True source ranks within top-k of candidate universe (default k=10)
6. LLM attack identifies company (if enabled and required)
7. Deterministic hash mismatch (evidence not reproducible)
8. Required attack skipped
9. Provenance incomplete
10. Private paths in release artifacts
11. Unhandled errors

---

## 20. Release-Assess Command

To re-evaluate a run's release decision:

```bash
python -m fenrix_synthetic.cli release-assess \
  --attack-results $FENRIX_PRIVATE_ROOT/runs/{run_id}/private/text_attacks.json \
  --structured-attack-results $FENRIX_PRIVATE_ROOT/runs/{run_id}/private/structured_attacks.json \
  --config configs/policies/pilot_v1.yaml \
  --output $FENRIX_PRIVATE_ROOT/runs/{run_id}/private/release_decision.json
```

---

## 21. Release-Export Command

Only permissible when gate decision is PASS.
Always use an exact run_id:

```bash
python -m fenrix_synthetic.cli release-export \
  --release-id SYNTH_001 \
  --output $FENRIX_PRIVATE_ROOT/exports/SYNTH_001
```

Refuses to export into the repository unless `--allow-repo-export` is
explicitly supplied (DANGEROUS — only for PASS decisions with complete
leakage scan).

---

## 22. S0 Interpretation

**S0_CONTROL** is a deliberately vulnerable variant:
- Preserves exact source return sequence rebased to base_price (100)
- Relative trading-day index (0, 1, 2, ...)
- **Always non-releasable**
- Used for attack benchmarking only

---

## 23. S1 Interpretation

**S1_BASIC**:
- Prices rebased to base_price (100)
- Volume normalized to rolling percentile (20-day window)
- OHLC relationships preserved through normalized price relationships
- Absolute shares and market cap removed
- Deterministic output, marked releasable

---

## 24. S2 Interpretation

**S2_PRIVACY** (when references available):
- Log returns → market/sector residual regression → winsorization → pseudo-price
- OLS coefficients estimated on in-sample fit window (60 days)
- Residuals normalized to source volatility
- Winsorized at 2.5th/97.5th percentiles
- Pseudo-price index reconstructed from winsorized residuals
- **S2 ranked first = structured anonymity has failed**

**S2_INCOMPLETE** (when references missing/insufficient):
- No residual removal performed
- Marked non-releasable
- Stage status: `review_required`

---

## 25. Structured Attack Interpretation

The candidate-universe attack ranks the transformed returns among
a universe of peer companies.

| Metric | Good privacy | Poor privacy |
|--------|-------------|--------------|
| True-source rank | High number (e.g., 150th out of 200) | Low number (e.g., 1st out of 200) |
| Percentile rank | Low (< 5%) | High (> 95%) |
| Top-10 | False | True |

**If S1 and S2 both rank the true source first, the structured release
design has failed.** Do not compensate by adding more filings or more
LLM layers. The next experiment should be a feature-only variant (S3).

---

## 26. Utility Interpretation

| Metric | What it measures |
|--------|-----------------|
| Return sign agreement | Fraction of days with same direction |
| Rank correlation | Spearman correlation of price ranks |
| Volatility distortion | Relative diff in annualized vol |
| Max drawdown distortion | Relative diff in max drawdown |
| Momentum agreement | Same 20-day momentum direction |
| MA crossover agreement | Same 5/20 MA crossover state |
| OHLC valid | All invariants hold |

---

## 27. Evidence-Manifest Inspection

```bash
cat $FENRIX_PRIVATE_ROOT/runs/{run_id}/private/evidence_manifest.json | \
  python -m json.tool
```

Contains all stage outputs with hashes. The release gate validates:
- All required evidence types present
- No placeholder entries
- All evidence from same run
- Consistent source/release IDs

---

## 28. Inspection Focus Areas

Ignore the dossier initially. Focus on private evaluation artifacts:

### Text privacy
- Number of deterministic replacements
- Number of documents passing leakage scans
- Remaining GLiNER review findings
- Unique-phrase findings
- Semantic fingerprint findings
- Numeric fingerprint findings
- Whether any model or human can infer the issuer

### Structured privacy (per variant)
| Metric | S0 | S1 | S2 |
|--------|:--:|:--:|:--:|
| True-source rank | | | |
| Candidate count | | | |
| Percentile rank | | | |
| Correlation rank | | | |
| DTW rank | | | |
| Volatility rank | | | |
| Drawdown rank | | | |
| Aggregate score margin | | | |

### Utility
- Return-sign agreement
- Return correlation
- Momentum agreement
- Moving-average crossover agreement
- Trade-decision agreement
- Volatility distortion
- Drawdown distortion
- Text token retention
- Financial-number retention

---

## 29. Decision Rule After the Pilot

### If S2 no longer ranks near the top
Continue refining the identity atlas and textual masking. Then run
independent model and teammate attacks.

### If S2 still ranks the source first or within the top five
**Do not** compensate by adding more filings or more LLM layers.
The structured release design has failed.

The next experiment should be a **feature-only structured variant**
(e.g., S3_FEATURE_ONLY):
- Relative trading-day index
- Return direction
- Rolling volatility percentile
- Rolling volume percentile
- Momentum bucket
- Drawdown bucket
- Moving-average state
- Fundamental ratio buckets
- No reconstructable pseudo-price path

### If even feature-only data identifies the issuer
The team must choose between:
1. Weakening the anonymity requirement
2. Withholding structured company-level history from the released package
3. Permitting stronger structured-data synthesis or temporal distortion

There is no credible engineering trick that guarantees both an almost
unchanged public-market path and resistance to historical matching.

---

## 30. Known Limitations

- S2 reference-series residual removal is implemented with in-sample
  OLS regression; out-of-sample / walk-forward not yet tested
- LLM attack interface exists but no live provider is integrated
- GLiNER review-only guarantees are enforced at ingest, not at runtime
- Resume safety across runs is not yet fully validated
- Case-insensitive matching uses `re.IGNORECASE` with word-boundary
  guards that omit `\b` after punctuation
- Candidate-universe ranking uses close-price returns only
- No Parquet format support; JSON is the canonical format

---

## 31. Cleanup

Remove run directories when no longer needed:

```bash
rm -rf $FENRIX_PRIVATE_ROOT/runs/run-SRC_001-*
```

---

## 32. Secret Handling

- Never commit `FENRIX_PRIVATE_ROOT` contents
- Never commit `identity_atlas.yaml`
- Never log private values
- Use `--sanitize` on registry-inventory
- All logs filter keys matching `(?i)(key|token|secret|password|auth|credential)`
