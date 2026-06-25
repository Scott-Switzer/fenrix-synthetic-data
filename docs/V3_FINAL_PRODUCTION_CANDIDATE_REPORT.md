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

- **Aggregate verdict:** `<filled at end: PRODUCTION_CANDIDATE_READY or NOT_READY>`
- **Reason:** `<brief — see §6, §15, §16>`

## 21. Acceptance criteria checklist

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
