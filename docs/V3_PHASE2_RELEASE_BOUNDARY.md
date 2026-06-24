# V3 Phase 2/3: Hard Release Boundary

## Branch
`feature/professor-bundle-pipeline`

## Summary

Implements a strict V3 release boundary that makes it structurally impossible
to accidentally ship raw SEC/iXBRL, source identity, private mappings, or
direct identifiers in a professor-facing artifact.

### What Was Implemented

1. **Direct Identifier Scanner** (`src/fenrix_synthetic/qa/direct_identifier_scan.py`)
   - Detects 25+ static pattern classes: CIK, commission file numbers, EIN,
     CUSIP, ISIN, LEI, SEC URLs, XBRL namespaces, Workiva/Wdesk metadata
   - Supports dynamic patterns from config: company names, tickers, executive
     names, CIK numbers
   - Scans markdown, JSON, CSV, TXT, YAML, HTML, XML files
   - Scans ZIP entry names
   - Returns structured `ScanResult` with `ScanHit` dataclasses

2. **Metadata Scanner** (`src/fenrix_synthetic/qa/metadata_scan.py`)
   - Groups 55+ patterns into 6 categories: html_xml, xmlns, xbrl_tag,
     xbrl_attr, sec_metadata, additional
   - By default, notes `.html`/`.xml` files as hits without content scanning
     (they should never be in a public release)
   - Detects: IXBRL tags (ix:hidden, ix:header, ix:nonNumeric),
     XBRL attributes (contextRef, unitRef, schemaRef),
     SEC metadata (DocumentFiscalYearFocus, TradingSymbol, EntityRegistrantName),
     Accession patterns, EDGAR URLs

3. **Strict Release Gate** (`src/fenrix_synthetic/qa/release_gate.py`)
   - Aggregates: direct identifier scan + metadata scan + forbidden path
     validation + forbidden file pattern validation + ZIP content validation
   - Fail-closed: any finding → FAIL
   - Writes `qa/direct_identifier_scan.json` and `qa/metadata_scan.json`
   - Produces `qa/public_release_gate.json` with pass/fail, findings,
     fail_reasons

4. **Student Bundle Packager** (`src/fenrix_synthetic/package/student_bundle.py`)
   - Allowlist-based ZIP creation (replaces exclusion-based approach)
   - Pre-validation catches forbidden files before ZIP creation
   - Post-validation verifies ZIP contents
   - Forbidden patterns: private/, raw/, source/, identity/, mappings/,
     *.html, *.xml, *.env, *.key, *.pem, *_identity*, *_mapping*, etc.

5. **Release Manifest** (`src/fenrix_synthetic/package/release_manifest.py`)
   - Pydantic schema with forced false privacy flags
   - `identity_map_included`, `raw_source_included`, `raw_sec_html_included`,
     `raw_xbrl_included` must all be `False`
   - Serializes to JSON and Markdown
   - Includes repo SHA, branch, config hash, seed, artifact counts, QA reports

### Allowlisted Paths

```
README.md, QUICKSTART.md, RUN_SUMMARY.md, DATA_DICTIONARY.md,
RELEASE_MANIFEST.md, RELEASE_MANIFEST.json,
public/anonymized/**, qa/**, checksums.sha256, run_summary.json,
artifact_inventory.csv
```

### Forbidden Paths

```
private/**, raw/**, source/**, sources/**, identity/**,
checkpoints/**, cache/**, edgar_raw/**, sec_raw/**, original/**,
*.html, *.xml, *.env, *.key, *.pem, *.sqlite, *.db,
*_identity*, *_mapping*, *identity_map*, *source_map*
```

### Tests

| Test File | Test Count | Coverage |
|-----------|-----------|----------|
| `test_direct_identifier_scan.py` | 18 | Pattern detection, clean content, dynamic patterns, ZIP scan, result properties |
| `test_metadata_scan.py` | 15 | XBRL tags, DEI/US-GAAP, HTML/XML detection, SEC metadata, clean content |
| `test_release_gate_strict.py` | 14 | Private map, raw HTML, raw XBRL, ZIP entries, direct identifiers, metadata, clean bundle, gate output structure |
| `test_student_bundle_packager.py` | 14 | Allowlist/blocklist, ZIP packaging, private/raw/env/html/xml rejection, post-validation |
| `test_release_manifest.py` | 12 | Forced false flags, serialization (JSON/Markdown), repo SHA, artifact counts |

### How to Run

```bash
# Run the pip install if needed
pip install -e ".[dev]"

# Run the new test suites
pytest tests/unit/test_direct_identifier_scan.py -v
pytest tests/unit/test_metadata_scan.py -v
pytest tests/unit/test_release_gate_strict.py -v
pytest tests/unit/test_student_bundle_packager.py -v
pytest tests/unit/test_release_manifest.py -v

# Run professor review fixture tests (must still pass)
pytest tests/unit/test_professor_review_fixtures.py -v

# Run full suite
pytest
```

### Current Limitations

- **CLI flag available but gate always runs**: `--no-strict-release-gate` is accepted by the CLI but the orchestrator always evaluates the strict gate. The production path is always safe.
- **Artifact counts use file extensions**: The release manifest counts artifacts by suffix (`.md`, `.json`) rather than by semantic type. Minor follow-up improvement.
- **No network calls**: All scanners work offline with pattern matching.
- **Dynamic company data**: Company names/tickers must be passed explicitly to the scanner. They are not auto-loaded from private config.
- **No fuzzy matching**: Exact and regex patterns only. No embedding-based similarity scanning.
- **Allowlist maintenance**: New public file types must be added to the allowlist manually.

### Next Phase Recommendation

Proceed to Phase 4: peer-archetype anonymization, numeric transformation, trajectory morphing. The release boundary is now structurally enforced — private/raw/SEC/iXBRL/identity artifacts cannot accidentally enter the professor-facing ZIP.
