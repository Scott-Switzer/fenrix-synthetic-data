# Phase 4 Runbook: Anonymity Threat Model Pilot

## 1. Purpose and Privacy Limitations

This runbook documents how to run the Phase 4 anonymity pilot pipeline.
The pipeline transforms private company data (SRC_001) into a pseudonymous
release candidate (SYNTH_001), runs re-identification attacks, evaluates
utility, and produces a release decision.

**This pipeline does NOT claim cryptographic anonymity.** It is a
feasibility pilot that demonstrates deterministic masking, structured
privacy transformations, and evidence-based release gating.

## 2. Canonical Private Directory Structure

```
FENRIX_PRIVATE_ROOT/
  sources/
    SRC_001/
      source_manifest.yaml        # Optional: source document registry
      identity_atlas.yaml         # REQUIRED: private identity mappings
      structured/
        prices.json               # OHLCV data
        candidate_universe.json   # Peer company returns for ranking attack
        market_reference.json     # Optional: market index returns
        sector_reference.json     # Optional: sector index returns
      unstructured/
        report_q1.txt             # Normalized 10-K text
        report_q2.txt             # Normalized 10-Q text
        news_2025.txt             # News headlines
  runs/
    run-SRC_001-{timestamp}/
      private/                    # Private intermediate artifacts
        run_manifest.json
        evidence_manifest.json
        release_decision.json
      exports/
  exports/
    SYNTH_001/                    # Sanitized release dossier
```

## 3. FENRIX_PRIVATE_ROOT Setup

```bash
export FENRIX_PRIVATE_ROOT=/absolute/path/outside/repo
mkdir -p $FENRIX_PRIVATE_ROOT/sources/SRC_001/{structured,unstructured}
```

Run the boundary diagnostic to verify:

```bash
python -m fenrix_synthetic.cli boundary-diag
```

## 4. Source-Manifest Schema

Optional YAML file at `sources/SRC_001/source_manifest.yaml`:

```yaml
manifest_id: manifest-SRC_001-v1
schema_version: "1.0.0"
company_id: SRC_001
documents: []
series: []
```

## 5. Identity-Atlas Schema

Required YAML file at `sources/SRC_001/identity_atlas.yaml`.

Each entry must have: entry_id, category, sub_type, private_value,
normalized_value, match_policy, priority, reason, reviewer_id.

Categories: issuer, people, organizations, products, locations, digital, semantic_fingerprints.

## 6. Required Atlas Review States

Real pilots require:
- At least one legal issuer name
- At least one ticker or issuer identifier
- At least one digital identity when the company has a website/domain
- Explicit reviewed_none_found declarations for empty categories
- Atlas version, reviewer, approval state, creation timestamp

An empty atlas is REJECTED for real pilots.

## 7. Normalized Document Format

Plain text files (.txt) in `unstructured/`. One document per file.
No raw HTML, no PDF binaries. Content should be normalized (NFKC unicode).

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

## 9. Candidate-Universe Format

JSON file at `structured/candidate_universe.json`:

```json
{
  "universe_id": "univ-v1",
  "candidates": [
    {
      "candidate_id": "SRC_001",
      "returns": [0.001, -0.002, ...]
    },
    {
      "candidate_id": "PEER-0001",
      "returns": [0.0005, 0.001, ...]
    }
  ]
}
```

## 10-11. Market and Sector Reference Formats

Optional JSON files with `returns` arrays matching the same time period
as the source OHLCV data.

## 12. Boundary Diagnostic

```bash
python -m fenrix_synthetic.cli boundary-diag
```

## 13. Atlas Validation

The pipeline validates atlas completeness during `pilot-run`.
To validate standalone:

```bash
python -m fenrix_synthetic.cli identities-compile \
  --atlas $FENRIX_PRIVATE_ROOT/sources/SRC_001/identity_atlas.yaml \
  --output $FENRIX_PRIVATE_ROOT/silver/replacement_plan.json
```

## 14. Offline Invented Pilot

A complete invented fixture test exists at:
`tests/integration/test_phase4_vertical_slice.py`

Run it:
```bash
python -m pytest tests/integration/test_phase4_vertical_slice.py -v \
  --disable-socket --allow-unix-socket
```

## 15. Full Private Pilot-Run Command

```bash
python -m fenrix_synthetic.cli pilot-run \
  --source-id SRC_001 \
  --release-id SYNTH_001 \
  --private-root $FENRIX_PRIVATE_ROOT \
  --candidate-universe $FENRIX_PRIVATE_ROOT/sources/SRC_001/structured/candidate_universe.json \
  --market-reference $FENRIX_PRIVATE_ROOT/sources/SRC_001/structured/market_reference.json \
  --sector-reference $FENRIX_PRIVATE_ROOT/sources/SRC_001/structured/sector_reference.json \
  --offline
```

## 16. Review Queue Workflow

After running `pilot-run`, inspect the review queue for any
unresolved high-risk findings. These appear in the evidence manifest.

## 17. Manual Atlas Approval Workflow

1. Review GLiNER candidates in the review queue
2. For approved identities, add entries to `identity_atlas.yaml`
3. Update atlas version and reviewer metadata
4. Rerun `pilot-run` deterministically

## 18. Deterministic Rerun

```bash
python -m fenrix_synthetic.cli pilot-run \
  --source-id SRC_001 --release-id SYNTH_001 --private-root $FENRIX_PRIVATE_ROOT \
  --force
```

Evidence hashes must match between runs for the gate to pass.

## 19. S0 Interpretation

S0_CONTROL is a deliberately vulnerable variant. It preserves exact
source return sequence rebased to 100. Always non-releasable.
Used for attack benchmarking.

## 20. S1 Interpretation

S1_BASIC rebases prices and normalizes volume. Preserves valid OHLC
relationships. Deterministic output.

## 21. S2 Interpretation

S2_PRIVACY applies log returns, winsorization, and pseudo-price
reconstruction. Requires market/sector references for full privacy.
Without references, marked S2_NO_REFERENCE (incomplete).

## 22. Structured Attack Interpretation

The candidate-universe attack ranks the transformed returns among
a universe of peer companies. A low rank (high number) indicates
better privacy. A rank in the top-k is a blocking finding.

## 23. Utility Interpretation

Utility metrics measure how well the masking preserves non-identity
information: token retention, financial number retention,
section/table preservation, return sign agreement, rank correlation.

## 24. Evidence-Manifest Inspection

The evidence manifest is at:
`runs/RUN_ID/private/evidence_manifest.json`

It contains all stage outputs with hashes.

## 25. PASS, FAIL, REVIEW_REQUIRED Interpretation

- **PASS:** All blocking conditions met. Dossier may be exported.
- **FAIL:** At least one blocking condition failed. No export.
- **REVIEW_REQUIRED:** Non-blocking warnings exist. Private dossier
  may be generated but not released.

## 26. Private Blocked Dossier Behavior

When the decision is FAIL, no dossier is exported. When
REVIEW_REQUIRED, a private dossier is generated but marked
RELEASE_BLOCKED.

## 27. Shareable Export Behavior

```bash
python -m fenrix_synthetic.cli release-export \
  --release-id SYNTH_001 \
  --output $FENRIX_PRIVATE_ROOT/exports/SYNTH_001
```

Only permitted when gate decision is PASS.

## 28. Cleanup

Remove run directories when no longer needed:
```bash
rm -rf $FENRIX_PRIVATE_ROOT/runs/run-SRC_001-*
```

## 29. Secret Handling

- Never commit `FENRIX_PRIVATE_ROOT` contents
- Never commit `identity_atlas.yaml`
- Never log private values
- Use `--sanitize` on registry-inventory

## 30. Known Limitations

- S2 reference-series residual removal is partially implemented
- LLM attack interface exists but no live provider is integrated
- GLiNER review-only guarantees are enforced at ingest, not at runtime
- Case-insensitive matching in the deterministic masker uses character
  matching without `re.IGNORECASE` flag (pre-existing)
- Candidate-universe ranking uses close-price returns only
- No Parquet format support; JSON is the canonical format
