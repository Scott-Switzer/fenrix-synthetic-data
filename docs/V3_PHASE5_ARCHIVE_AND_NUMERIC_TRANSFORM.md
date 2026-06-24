# V3 Phase 5: Archive Ingestion and Numeric Transform

## Overview

Phase 5 adds:
1. **Archive ingestion** — safe ZIP extraction with zip-slip protection
2. **Filing inventory** — normalized coverage maps by company/type/year
3. **Numeric transformation** — deterministic, accounting-aware perturbation
4. **Accounting sanity checks** — validate transformed financials
5. **Exact-number attack** — QA check for surviving source values

## Modules

### `src/fenrix_synthetic/sources/archive_ingest.py`

- `ingest_source_archive()` — main entry point
- Zip-slip protection via `_is_safe_path()` and `_reject_absolute_or_traversal()`
- File type detection: `.html`, `.txt`, `.xml`, `.xbrl`, `.json`, `.csv`
- Filing type heuristics: 10-K, 10-Q, 8-K, 20-F, DEF 14A
- Year extraction from paths
- Accession and CIK detection (flagged as private identifiers)
- Inventory JSON + coverage JSON + QA report generation

### `src/fenrix_synthetic/normalize/filing_inventory.py`

- `FilingInventory` — normalized inventory with opaque record IDs
- `FilingRecord` — safe metadata-only record
- `CoverageMap` — year/type/extension coverage statistics
- Deduplication by file hash
- Save/load JSON

### `src/fenrix_synthetic/anonymization/numeric_transform.py`

- `NumericTransformer` — deterministic transformer with:
  - Company-level scale factor (0.65–1.35)
  - Metric-family multipliers
  - Year-level smoothed noise (±2–6%)
  - Aggressive rounding by scale
- `TransformResult` — transformed metrics + derived ratios
- Public output writers: CSV, markdown
- Private audit writer

### `src/fenrix_synthetic/anonymization/accounting_sanity.py`

- `AccountingSanityChecker` — validates:
  - Revenue > 0, assets > 0
  - Cash ≤ assets
  - Debt ≤ liabilities + tolerance
  - Gross profit ≤ revenue
  - Operating income ≤ revenue
  - Net margin within plausible range
  - Assets ≈ liabilities + equity
  - No exact source value survived
- Configurable tolerances
- Warnings for missing optional data

### `src/fenrix_synthetic/qa/exact_number_attack.py`

- `ExactNumberAttack` — compares source to public facts:
  - Exact value match detection
  - Exact ratio match detection
  - Near-match detection with configurable tolerance
- `AttackReport` — structured pass/fail report
- `AttackConfig` — configurable thresholds

## CLI Commands

```bash
# Ingest source archive
python -m fenrix_synthetic.cli ingest-source-archive \
  --zip /path/to/archive.zip \
  --output-private private/source_archive \
  --run-tag scott_1 \
  --manifest private/source_archive/scott_1/qa/manifest.json
```

## Output Tree

### Private (archive ingestion)

```
private/source_archive/<run_tag>/
  raw/<relative-path>               — extracted files
  inventory/
    source_archive_inventory.json
    filing_coverage_by_company.json
    filing_coverage_by_year.json
  qa/archive_ingest_report.json
```

### Public (numeric transform)

```
public/anonymized/COMPANY_XXX/financials/
  transformed_metrics.csv
  ratio_summary.csv
  summary.md
```

### Private (numeric transform)

```
private/qa/numeric_transform_audit.json
qa/public/exact_number_attack_summary.json
```

## Pipeline Integration

The `NUMERIC_TRANSFORM` stage is intended for placement:
- After `PEER_ARCHETYPE`
- Before profile/public financial output
- Before release manifest
- Before release gate
- Before ZIP export

Current status: **Module implemented, pipeline integration pending Phase 5B completion.**

## Privacy Guarantees

- Raw archive never enters public output
- ZIP-slip protection prevents directory traversal
- All extracted files remain under private/
- Numeric transform breaks exact value matches via rounding + scaling
- Accounting sanity checks ensure no impossible values
- Exact-number attack verifies zero exact matches

## Tests

```bash
pytest tests/unit/test_archive_ingest.py -v
pytest tests/unit/test_filing_inventory.py -v
pytest tests/unit/test_numeric_transform.py -v
pytest tests/unit/test_accounting_sanity.py -v
pytest tests/unit/test_exact_number_attack.py -v
```

## Known Limitations

- Archive ingestion uses heuristics for filing type detection; may misclassify
- Numeric transform does not preserve real financial trajectory
- No real peer database in production mode
- News reconstruction planned for Phase 7
- Full text reconstruction planned for Phase 6
