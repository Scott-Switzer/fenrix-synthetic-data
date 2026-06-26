# V3.1 Artifact Quality Audit

> **Date:** 2026-06-25
> **Branch:** `feature/professor-bundle-pipeline`
> **Scope:** Full audit of Phase 8F production bundle artifacts and pipeline code
> **Verdict:** NOT_PROFESSOR_READY — 8 defects found, 4 are structural blockers

## Executive Summary

The Phase 8F ZIP passes privacy gates (0 top-1, 0 top-3, 0 high-confidence, 0 forbidden entries)
but fails product-quality requirements for a professor-facing academic artifact. The bundle is
correctly classified as a **privacy proof-of-concept**, not a final product. This audit catalogs
every defect found and classifies each as BLOCKER (structural, must fix), WARN (quality gap),
or COSMETIC (docs/packaging).

---

## Defect #1: Identical Archetypes Across All Companies [BLOCKER]

**Location:** `src/fenrix_synthetic/professor/multi_orchestrator.py`

**Finding:** The `_build_archetype_card` function does select distinct archetype keys
from `_ARCHETYPE_OPTIONS` (10 options) by company index. However, the utility-scoring
`_build_source_thesis` function hardcodes `business_model="financial services"` for
every company, which means:

1. All 8 public `profile.md` files are structurally identical except for the archetype
   label and company ID placeholder.
2. All 8 utility preservation scores measure against the same thesis
   (`financial services`), rendering the utility metric useless.
3. The investment-relevant traits are identical boilerplate for all companies.
4. Students receive no meaningful differentiation between companies beyond a label swap.

**Impact:** The bundle offers no genuine variety for financial analysis. This is fatal
for academic utility.

**Fix:** Each company must receive a distinct (company_id → archetype) mapping that
produces genuinely different financial profiles, SEC stubs, market series, and
investment theses. See `docs/V3_1_REBUILD_PLAN.md`.

---

## Defect #2: SEC Content Is Generic Stubs, Not Archive-Backed [BLOCKER]

**Location:** `src/fenrix_synthetic/professor/sec_providers.py` — `ArchiveInventorySecProvider`

**Finding:** The `ArchiveInventorySecProvider` explicitly documents itself as
"archive-indexed deterministic reconstructed stubs, NOT archive-backed reconstructed content."
Key evidence:

1. `discover_filings()` hardcodes `period_end="2024-12-31"`, `filing_date="2025-02-15"`,
   and `accession_ref="[ARCHIVE_PROXY]"`.
2. `parse_sections()` emits four generic 10-K-shaped stub sections with sector-neutral
   text that is identical across all companies.
3. The archive inventory (`source_archive_inventory.json`) is loaded but its
   `text_path` pointers are never read.
4. The `_load_inventory()` docstring confirms "best-effort" loading with no
   per-filing HTML parsing.
5. The `get_provider_report()` returns `"private_cache_location": ""` confirming
   no per-company text is loaded.

**Impact:** All 8 companies receive identical boilerplate SEC text. The bundle
documentation in `RELEASE_MANIFEST.json` admits "SEC text is deterministic sanitized
stubs" but `filing_coverage.md` still claims per-section content is "sanitized"
in a tone that implies actual filing data was processed.

**Fix:** Upgrade `ArchiveInventorySecProvider` to actually:
1. Read `text_path` pointers from the archive inventory
2. Extract and sanitize sections from real 10-K/10-Q/8-K text
3. Fail loudly if text_path files are missing
4. Document honest coverage (e.g., "3 years archive-backed, rest extrapolated")

---

## Defect #3: Financial Metrics Cover Only 5 Years [BLOCKER]

**Location:** `src/fenrix_synthetic/professor/multi_orchestrator.py` — `_emit_financial_outputs`

**Finding:** The `_emit_financial_outputs` function hardcodes `n_years = 5` with metrics
spanning only 2020–2024:

```python
n_years = 5
for y in range(2020, 2020 + n_years):
```

The metrics are synthetic (seeded random values, not real perturbations), with only 8
metric families, single-year values that don't connect across years into trend analysis.

**Impact:** 5 years is too thin for meaningful financial analysis. Standard practice
requires 7-10 years. The random-seeded values do not reflect any underlying business
dynamics.

**Fix:**
1. Expand to 7-10 years where source coverage permits
2. Use the NumericTransformer's actual perturbation pipeline on real source data
3. Emit `statement_summary.csv`, `ratio_summary.csv`, and `reconciliation_checks.json`
4. Document exact coverage honestly in RUN_SUMMARY

---

## Defect #4: Market Data Is Short Relative-Day Snippets [WARN]

**Location:** `src/fenrix_synthetic/professor/multi_orchestrator.py` — `_emit_market_outputs`

**Finding:** `_emit_market_outputs` generates `max(60, 200 - (seed % 50))` price points
— approximately 150-200 values. Each point is a synthetic `DAY_NNNN` with a random
price between 100-160. There is:

1. No connection to actual market data from the source company
2. No multi-year coverage (150 days ≈ 7 months)
3. No event-window returns around synthetic events
4. No `event_window_returns.csv` file emitted

**Impact:** Students cannot perform multi-year return analysis, event studies, or
meaningful market-based valuation.

**Fix:** Expand to multi-year relative daily/monthly series. Emit `event_window_returns.csv`
tied to synthetic news events. Target minimum 1000 rows.

---

## Defect #5: Public QA Stage Registries Contain Local-Dev Flags [BLOCKER]

**Location:** `src/fenrix_synthetic/professor/multi_orchestrator.py` — `_migrate_inner_outputs`

**Finding:** The multi-orchestrator's `_migrate_inner_outputs` has a comment stating:

```python
# Per-company stage_registry files are internal QA artifacts that
# carry inner build_mode=local_dev — they are NOT copied to the
# public QA directory and are excluded from the student ZIP.
```

However, the `_restructure_company_public_dir` writes per-company QA files via the
`inner_dir / "qa"` path, and the `qa/` allowlist prefix in `student_bundle.py` would
allow `qa/stage_registry_*.json` through if it's present.

The `_redact_private_filenames` function redacts private audit filenames but does NOT
strip `build_mode`, `professor_ready`, `release_safe`, or `LOCAL_DEV_NOT_READY` flags
from the stage registry data.

**Impact:** If a stage registry leaks into public QA, it contains:
- `professor_ready: false`
- `release_safe: false`
- `beta_status: "LOCAL_DEV_NOT_READY"`
- Private audit filenames (redacted, but path structure visible)

**Fix:**
1. Ensure `qa/stage_registry_*.json` is excluded from public ZIP via the allowlist
2. Add explicit test verifying no stage_registry files enter the student ZIP
3. If stage registries are needed in QA, generate clean public-safe summaries

---

## Defect #6: README/QUICKSTART/DATA_DICTIONARY All Claim 8 Companies With Financial Services Archetype [COSMETIC]

**Location:** `src/fenrix_synthetic/professor/multi_orchestrator.py` — `write_top_level_bundle_files`

**Finding:** The top-level docs are written deterministically and reference correct
company IDs, but:

1. QUICKSTART.md references `RELEASE_MANIFEST.md` which exists ✓
2. RUN_SUMMARY.md references `RUN_SUMMARY.md` which exists ✓
3. DATA_DICTIONARY.md references expected per-company files ✓
4. All four docs embed `PERTURBATION_DISCLOSURE` ✓

However, the docs claim the bundle covers "8 anonymized companies" without
mentioning they all share the same archetype or that SEC content is stubs.

**Fix:** Update docs to honestly reflect the actual bundle contents. If
limitations exist, document them in LIMITATIONS.md.

---

## Defect #7: Utility Score Is Artificially Low Because All Companies Share Same Thesis [WARN]

**Location:** `src/fenrix_synthetic/professor/multi_orchestrator.py` — `_build_source_thesis`

**Finding:** `_build_source_thesis` hardcodes:
```python
business_model="financial services",
product_exposure=["financial services", "consumer", "commercial"],
```

Every company's source thesis is identical. The utility preservation scoring measures
how much of the broad-sector thesis survives anonymization — but since all 8 companies
are scored against the same thesis, the utility score is essentially measuring
whether the anonymized output still says "financial services" somewhere, which it
always does.

This means the 0.6083 average utility score in Phase 8F is an artifact of the
measurement method, not a genuine signal of utility preservation.

**Fix:** Build per-company theses that match each company's assigned archetype.
A "consumer staples" company should measure against a consumer-staples thesis,
not a financial-services thesis.

---

## Defect #8: No Artifact Quality Gate Exists [WARN]

**Location:** N/A — missing module

**Finding:** The pipeline has privacy gates (strict release gate, LLM blind guess,
direct identifier scan) but no product-quality gate. There is no automated check
for:

- Distinct archetypes across companies
- Minimum financial year coverage
- SEC content being archive-backed vs. stubs
- Public QA containing local-dev flags
- README references matching actual ZIP contents
- Market series length for meaningful analysis

**Impact:** The bundle can pass all privacy gates while being academically useless.

**Fix:** Create `src/fenrix_synthetic/qa/artifact_quality_gate.py` with the checks
specified in Phase F of the rebuild plan.

---

## Defect Summary Table

| # | Defect | Severity | Current State | Target State |
|---|--------|----------|--------------|--------------|
| 1 | Identical archetypes across companies | BLOCKER | All 8 = financial services | 8 distinct broad archetypes |
| 2 | SEC content is generic stubs | BLOCKER | 4 hardcoded sections | Archive-backed or honestly labeled |
| 3 | Financials cover only 5 years | BLOCKER | 2020-2024 | 7-10 years where available |
| 4 | Market data is short snippets | WARN | ~150 relative-day points | 1000+ multi-year series |
| 5 | Stage registries at risk of leak | BLOCKER | Not in ZIP but at risk | Explicit exclusion + test |
| 6 | Docs claim more than delivered | COSMETIC | Generic 8-company claims | Honest coverage docs |
| 7 | Utility score measured wrong | WARN | Same thesis for all companies | Per-archetype theses |
| 8 | No artifact quality gate | WARN | Missing | `qa/artifact_quality_gate.json` |

---

## Files To Remove From Public ZIP

The following should be explicitly excluded from the professor ZIP:

1. `qa/stage_registry_*.json` — internal QA with local-dev flags
2. Any file containing `LOCAL_DEV_NOT_READY` in its content
3. Any file containing `/tmp/fenrix_inner_work_` path strings
4. `private/`, `raw/`, `source/`, `identity/`, `mappings/`, `checkpoints/`
   (already handled by allowlist)

---

## Acceptance Criteria For V3.1

- [ ] 8 distinct broad archetypes (not all financial services)
- [ ] Financials cover 7-10 years where source data available
- [ ] Market series ≥ 1000 rows with event-window returns
- [ ] SEC content archive-backed or truthfully labeled as limited
- [ ] Public QA has 0 local-dev flags
- [ ] Public QA has 0 `/tmp/` or `/private/` path strings
- [ ] README, QUICKSTART, RUN_SUMMARY, DATA_DICTIONARY agree with actual ZIP
- [ ] `qa/artifact_quality_gate.json` passes all checks
- [ ] Privacy gate PASS (maintained from Phase 8F)
- [ ] 0 source top-1/top-3 under LLM review
- [ ] 0 forbidden ZIP entries

---

*This audit was generated by code inspection at commit on branch `feature/professor-bundle-pipeline`.*
*The V3.1 rebuild plan follows in `docs/V3_1_REBUILD_PLAN.md`.*
