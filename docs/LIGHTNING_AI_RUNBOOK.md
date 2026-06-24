# Lightning.ai Runbook

## Environment Setup

```bash
# Clone / navigate to repo
cd /teamspace/studios/this_studio/fenrix-synthetic-data

# Install dependencies
pip install -e ".[dev]"

# Verify
python -m fenrix_synthetic.cli --version
```

## Data Storage Layout

```
~/fenrix-data/
  raw_uploads/
    scott_1.zip
  private/
    source_archive/
    qa/
  runs/
    professor_alpha_v3/
  cache/
```

**Never** put `~/fenrix-data/private` into git.
**Never** copy raw archive into public bundle.
**Never** upload professor ZIP until strict gate passes.

## Place the Archive

Upload `scott_1.zip` (or `Scott.zip`) to:

```
~/fenrix-data/raw_uploads/scott_1.zip
```

Verify:

```bash
ls -lh ~/fenrix-data/raw_uploads/scott_1.zip
```

## Run Archive Inventory

```bash
python -m fenrix_synthetic.cli ingest-source-archive \
  --zip ~/fenrix-data/raw_uploads/scott_1.zip \
  --output-private ~/fenrix-data/private/source_archive \
  --run-tag scott_1 \
  --manifest ~/fenrix-data/private/source_archive/scott_1/qa/manifest.json
```

Expected output:
- `~/fenrix-data/private/source_archive/scott_1/raw/` — extracted filings
- `~/fenrix-data/private/source_archive/scott_1/inventory/` — inventory JSONs
- `~/fenrix_data/private/source_archive/scott_1/qa/` — ingestion report

## Run Numeric Transform Tests

```bash
pytest tests/unit/test_numeric_transform.py -v
pytest tests/unit/test_accounting_sanity.py -v
pytest tests/unit/test_exact_number_attack.py -v
```

## Run Professor Bundle Fixture Build

```bash
python -m fenrix_synthetic.cli build-professor-bundle \
  --config configs/professor_bundle.fixture.yaml \
  --output-root /tmp/fenrix_bundle_fixture \
  --fast-fixtures
```

## Inspect ZIP

```bash
unzip -l /tmp/fenrix_bundle_fixture/exports/anonymized_bundle.zip | head -30
```

Verify:
- No `private/`, `raw/`, `source/`, `identity/` paths
- No `.html`, `.xml`, `.env`, `.key` files
- Contains `profile/archetype_card.json`
- Contains `profile/profile.md`
- No `peer_archetype_audit.json`

## Resume Ingestion

The ingestion module is idempotent — re-running with the same archive
produces the same hashes. To resume a partial run:

```bash
# Check existing inventory
ls ~/fenrix-data/private/source_archive/scott_1/inventory/

# Re-run if needed (safe — will overwrite with identical output)
python -m fenrix_synthetic.cli ingest-source-archive ...
```

## Output Locations

| Output | Path |
|--------|------|
| Raw archive | `~/fenrix-data/raw_uploads/` |
| Extracted filings | `~/fenrix-data/private/source_archive/<tag>/raw/` |
| Inventories | `~/fenrix-data/private/source_archive/<tag>/inventory/` |
| QA reports | `~/fenrix-data/private/source_archive/<tag>/qa/` |
| Professor bundles | `~/fenrix-data/runs/professor_alpha_v3/` |

## Avoid Committing Private Data

```bash
# Check git status
git status

# Ensure private/ is ignored
git check-ignore -v private/

# If accidentally staged
git reset HEAD private/
```

## Recommended Workflow

1. Upload archive to `raw_uploads/`
2. Run `ingest-source-archive`
3. Review ingestion report
4. Run numeric transform tests
5. Build professor bundle with `--fast-fixtures`
6. Inspect ZIP
7. Run strict release gate
8. Only then consider production build

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `zipfile.BadZipFile` | Re-upload archive; verify完整性 |
| `FileNotFoundError` | Check `--zip` path is absolute |
| Rejected entries > 0 | Review `rejected_detail` in report; usually traversal attempts |
| Out of disk | Clean `cache/`; archive is ~737MB, extracted ~2-3x |
| Tests fail | Run `pip install -e ".[dev]"` first |
