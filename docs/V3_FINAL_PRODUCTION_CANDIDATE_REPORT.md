# Phase 8F: Final Production Candidate Report

> **Status:** PHASE 8F COMPLETE / PARTIAL (filled at end of run)
> **Branch:** `feature/professor-bundle-pipeline`
> **Date:** 2026-06-22

## 1. Run identity

- **Branch:** `feature/professor-bundle-pipeline`
- **Start SHA:** `<HEAD before changes>`
- **End SHA:** `<HEAD after commit>`
- **Release date:** 2026-06-22
- **Build kind:** `multi_company_production`
- **Phase:** 8F
- **Operating mode:** production (no `--fast-fixtures`)

## 2. Production command

The exact production command is:

```bash
python -m fenrix_synthetic.cli build-production-bundle \
  --output ~/fenrix-data/runs/professor_alpha_v3_final_prod \
  --source-mapping ~/fenrix-data/private/source_mapping/source_companies.yaml \
  --source-archive-inventory ~/fenrix-data/private/source_archive/scott_1/inventory/source_archive_inventory.json \
  --release-date 2026-06-22
```

NOT used in production: `--fast-fixtures`, `--allow-provider-skip-for-local-dev`.

## 3. Archive inventory

- **Path (private):** `~/fenrix-data/private/source_archive/scott_1/inventory/source_archive_inventory.json`
- **Status:** Loaded. Values not disclosed in this report.

## 4. Source mapping status

- **Path (private):** `~/fenrix-data/private/source_mapping/source_companies.yaml`
- **Mapped company IDs:** 8 (COMPANY_001 … COMPANY_008)
- **Status:** Loaded for scoring only. Tickers and company names are NOT
  embedded in any public output. This report does not disclose values.

## 5. Final ZIP

- **Path:** `~/fenrix-data/runs/professor_alpha_v3_final_prod/exports/anonymized_bundle.zip`
- **Entry count:** `<filled at end>`
- **Company directories covered:** `<filled at end>`

## 6. Strict release gate

- **Mode:** `strict`
- **Result:** `<filled at end>`
- **Forbidding reasons (if any):** `<filled at end>`

## 7. Direct identifier scan

- **Status:** Evaluated by the strict release gate.
- **Result:** `<filled at end>`

## 8. Metadata scan

- **Status:** Evaluated by the strict release gate.
- **Result:** `<filled at end>`

## 9. Exact-number attack

- **Status:** Evaluated by the strict release gate.
- **Result:** `<filled at end>`

## 10. Trajectory attack

- **Status:** Phase 6 — covered synthetic trajectory outputs.
- **Result:** `<filled at end>`

## 11. Filing reconstruction attack

- **Status:** Phase 6 — covered filing reconstructions.
- **Result:** `<filled at end>`

## 12. News reconstruction attack

- **Status:** Phase 6 — covered synthetic news briefs.
- **Result:** `<filled at end>`

## 13. Live NVIDIA model

- **Model:** `<filled at end>`
- **Status:** `<filled at end>`
- **HTTP code:** `<filled at end>`

## 14. Live LLM per-company result

| Company | Provider | Verdict | Confidence | Top-1 Pick | Top-3 |
|---------|----------|---------|-----------|-----------|-------|
| COMPANY_001 | NVIDIA Online | `<filled at end>` | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_002 | NVIDIA Online | `<filled at end>` | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_003 | NVIDIA Online | `<filled at end>` | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_004 | NVIDIA Online | `<filled at end>` | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_005 | NVIDIA Online | `<filled at end>` | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_006 | NVIDIA Online | `<filled at end>` | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_007 | NVIDIA Online | `<filled at end>` | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_008 | NVIDIA Online | `<filled at end>` | `<filled at end>` | `<filled at end>` | `<filled at end>` |

## 15. Actual source top-1 / top-3 status

- **Companies with actual source in top-1:** `<filled at end>`
- **Companies with actual source in top-3:** `<filled at end>`
- **Companies with high-confidence guesses:** `<filled at end>`
- **Privacy gate:** `<filled at end>`

## 16. Utility preservation per-company result

| Company | Score | Signals Preserved | Verdict |
|---------|-------|-------------------|---------|
| COMPANY_001 | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_002 | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_003 | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_004 | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_005 | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_006 | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_007 | `<filled at end>` | `<filled at end>` | `<filled at end>` |
| COMPANY_008 | `<filled at end>` | `<filled at end>` | `<filled at end>` |

- **Average score:** `<filled at end>`
- **Min score:** `<filled at end>`
- **Max score:** `<filled at end>`
- **Utility gate:** `<filled at end>`

## 17. Known limitations (CAUSE — honest Case B)
- **SEC content classification:** `ArchiveInventorySecProvider` is
  *archive-indexed deterministic reconstructed stubs*, NOT
  *archive-backed reconstructed content*. The inventory is loaded
  (so per-company ticker routing wires correctly) but
  `discover_filings` hardcodes `period_end`, `accession_ref`, and
  `filing_date`, and `parse_sections` emits four generic 10-K-shaped
  stub sections (Item 1, 1A, 7, 8) with only sector-neutral text.
  No per-filing HTML text is currently read from the archive's
  `text_path` pointers. Claims of "full 20-year filing recreation"
  are NOT supported by the current code path; this Phase 8F bundle
  MUST NOT be marketed as archive-backed.
- **SEC text content:** Per-iteration SEC text is sanitized 10-K-shaped
  stubs. Real per-filing HTML parsing of archived documents remains a
  Phase 6 deliverable.
- **Archive inventory:** Used for source routing (the orchestrator reads
  ticker mappings from the inventory), but per-document text is not
  currently derived from the inventory's `text_path` pointers.
- **Production candidates:** Default utility threshold is **`>= 0.70`**.
  If scores cluster between 0.55–0.70 the bundle is marked WARN with
  explicit `lost_signals` listed in `qa/utility_preservation_summary.json`.
  Privacy wins: improving utility MAY increase identification confidence,
  in which case the safer (lower-utility) version is kept and the
  warning is documented here.

## 18. Same-message quality

- **Top-level docs:** README.md, QUICKSTART.md, RUN_SUMMARY.md,
  DATA_DICTIONARY.md, RELEASE_MANIFEST.md/.json, run_summary.json,
  checksums.sha256, artifact_inventory.csv all written at bundle root.
- **Per-company tree standard:** `profile/`, `financials/`, `market/`,
  `sec/`, `news/`.

## 18a. Financial-Quality Perturbation Disclosure (Slack item #1)

The numeric transformation policy applied to all 8 companies is documented in the
per-bundle `README.md`, `QUICKSTART.md`, `RUN_SUMMARY.md`, and `DATA_DICTIONARY.md`,
and at the repo level in `README.md`. The disclosure text is the canonical
`PERTURBATION_DISCLOSURE` constant exported from
`src/fenrix_synthetic/anonymization/numeric_transform.py`.

**Reversible parameters** (per-company scale, family multipliers, year noise
direction, exact seed) are NOT inlined into any public artifact. They are
written only to `private/qa/numeric_transform_audit.json` and never appear in
the bundle ZIP. This is verified by
`tests/unit/test_numeric_transform.py::test_public_docs_disclose_perturbation_without_revealing_parameters`.

## 18b. Quantitative Perturbation Consistency (Slack item #2)

The numeric policy does NOT branch on `company_id`. Every company passes through
the same `NumericTransformer(company_id, seed, scale_range, year_noise_range)`
configuration; the only per-company variation is the deterministic seed feeding
the SHA-256 keyed scaler. There is no hard-coded `+20%` boost or per-source
special case. The transformer enforces:

- company-level scale factor in `(0.65, 1.35)`,
- metric-family multipliers in `(0.85, 1.15)`,
- bounded year noise (default `±2%–±6%`, capped to the configured range),
- aggressive rounding by magnitude,
- exact source value detection with violation reporting.

Consistency is verified by
`tests/unit/test_numeric_transform.py::test_numeric_policy_is_consistent_across_companies`.

## 18c. Business-Model Inference Limitation (Slack items #3, #4)

**Known limitation: business-model inference.** The anonymization process
removes direct identifiers, exact public values, raw SEC metadata, original
product names, locations, people, hyperlinks, and other high-confidence lookup
features. It does **not** fully reinvent the underlying business model —
the business model is necessary for the finance exercise and must remain
consistent with transformed financials, risk factors, synthetic news, and
market movement. Therefore an adversarial reviewer may still infer a broad
peer group or sector from the business model. This is accepted as a
best-effort limitation as long as the reviewer cannot identify the exact
source company with high confidence or place the true source in
top-1/top-3 under live LLM review.

The bundle deliberately does not:

- turn an automaker into a software company,
- turn a bank into a retailer,
- remove crisis signals when the stock movement depends on the crisis,
- rewrite the business so ratios and market movement no longer make
  sense, or
- delete the core economic model students are supposed to analyze.

## 18d. Famous-Event Generalization (Slack item #6)

Famous, uniquely identifying source events are NOT preserved verbatim.
Synthetic news briefs use a fixed event-class vocabulary
(`major_restructuring`, `liquidity_crisis`, `regulatory_shock`,
`demand_collapse`, `supply_chain_disruption`, `strategic_pivot`,
`capital_markets_stress`, `litigation_overhang`, plus the four-lexicon
support set already used by Phase 6). The financial / market trajectory of a
crisis is preserved as an economic signal; the exact event label,
calendar, and stakeholders are intentionally withheld.

## 19. Tests run

Listed at end of run (commands and outcomes):

```text
ruff format src tests        → PASS
ruff check src tests         → `<filled at end>`
mypy src                     → `<filled at end>`
pytest (full suite)          → `<filled at end>`
pytest tests/integration/test_production_bundle_mode_separation.py → PASS
```

## 20. Final verdict

- **Aggregate verdict literal:** `PRODUCTION_CANDIDATE_READY_WITH_BUSINESS_MODEL_LIMITATION` (or one of the failure literals `FAIL`, `STRICT_GATE_FAILED`, `PRIVACY_GATE_FAILED`, `UTILITY_GATE_FAILED`).
- **Reason:** `<brief — see §6, §15, §16>`

The bundle is never marketed as:

- "fully anonymous",
- "zero re-identification risk",
- "mathematically private",
- "formally differentially private", or
- "full 20-year filing recreation".

Preferred final verdict language (when all gates pass):

> The bundle is a best-effort anonymized and reconstructed financial-analysis
> dataset. It removes direct identifiers and major lookup paths, perturbs
> financials consistently, generalizes product/event fingerprints, and passed
> live LLM deanonymization review under the tested model. Residual
> business-model inference remains a known limitation because the business
> model must remain useful for the finance exercise.

## 21. Acceptance criteria checklist (Phase 8F + Slack-derived)

Phase 8F criteria (kept):

1. Production command does NOT use `--fast-fixtures` — ✅
2. Real archive inventory is used — ✅
3. Private source map is used only for scoring — ✅
4. All 8 companies are generated — `<verified>`
5. All 8 companies are live-reviewed — `<verified>`
6. No actual source top-1/top-3 — `<verified>`
7. No high-confidence identification — `<verified>`
8. Utility preservation PASS or explicitly justified WARN — `<verified>`
9. Strict release gate passes — `<verified>`
10. Final ZIP has all required files and no forbidden files — `<verified>`
11. Final report exists (this file) — ✅
12. Code/docs fixes are committed — `<verified>`

Slack-derived criteria (added):

13. All 8 companies generated — `<verified>`
14. All 8 companies live-reviewed — `<verified>`
15. Financial perturbation policy disclosed in public docs — ✅ (via `PERTURBATION_DISCLOSURE`)
16. Exact perturbation parameters excluded from public ZIP — ✅ (`PRIVATE_TRANSFORM_KEYS` lint enforced)
17. Business-model limitation documented — ✅ (§18c of this report + `RUN_SUMMARY.md`)
18. Famous events generalized — ✅ (`GENERIC_EVENT_CLASSES` in multi_orchestrator)
19. Product names generalized — ✅ (Phase 6 product generalization)
20. No source top-1/top-3 — `<verified>`
21. No high-confidence exact identification — `<verified>`
22. Utility preservation pass or documented warn — `<verified>`
23. Strict release gate pass — `<verified>`
