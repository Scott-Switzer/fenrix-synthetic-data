# FENRIX Synthetic Data Worker

Reproducible company-level financial-data masking and re-identification testing system.

Current vertical slice: **fictional canary company (CHC)** — real source mappings are private and gitignored.

## Milestone 0 - Repository Foundation (Complete)

- Python package with `src/` layout
- CLI entry point: `fenrix-synth`
- Typed schemas for artifacts, manifests, checkpoints, provenance
- Deterministic canonical JSON serialization (orjson + sorted keys)
- SHA-256 hashing (files, strings, objects)
- Atomic writes (JSON, JSONL, Parquet, binary)
- Checkpoint validation and resume behavior
- Structured JSON logging with secret-key redaction
- Configuration via YAML + environment variables
- Comprehensive unit tests (offline, deterministic)

## Quick Start

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install package in development mode with dev dependencies
pip install -e ".[dev]"

# Verify installation
fenrix-synth --help

# Run tests
pytest

# Run formatting and linting
ruff format --check
ruff check

# Run type checking
mypy src/fenrix_synthetic
```

## Configuration

### Company Configuration (Private)

Copy the template and fill in values:

```bash
cp configs/company.yaml.template configs/company.yaml
```

Edit `configs/company.yaml`:
```yaml
companies:
  CANARY:
    source_identity: "CHC"  # Private source identity (canary placeholder in template)
    data_root: "data"
    raw_dir: "data/raw"
    bronze_dir: "data/bronze"
```

**Never commit `configs/company.yaml`** - it's in `.gitignore`. Real company mappings belong in gitignored private config only.

### Campaign Configuration

Edit `configs/campaign.yaml` to control pipeline execution:
```yaml
company_id: "CANARY"
stages:
  - "ingest"
  - "extract"
  - "manifest"
resume: true
stop_on_failure: true
```

### Environment Variables

Copy `.env.example` to `.env` and configure:
```bash
cp .env.example .env
```

## CLI Usage

```bash
# Show help
fenrix-synth --help

# Hash utilities (working in M0)
fenrix-synth hash "test string"
fenrix-synth hash-file path/to/file.txt
fenrix-synth hash-json --json-input '{"key": "value"}'

# Pipeline commands (placeholders for M1)
fenrix-synth ingest --company CANARY
fenrix-synth extract --company CANARY
fenrix-synth campaign --company CANARY --resume
```

## Project Structure

```
fenrix-synthetic-data/
├── configs/
│   ├── company.yaml.template   # Template for private company mappings
│   └── campaign.yaml           # Campaign configuration
├── docs/
│   ├── ARCHITECTURE.md         # System architecture
│   ├── DECISIONS.md            # Architecture decisions log
│   └── SOURCE_PROVENANCE.md    # Source code reuse tracking
├── src/
│   └── fenrix_synthetic/
│       ├── cli.py              # CLI entry point
│       ├── config/             # Configuration loading
│       ├── schemas/            # Pydantic schemas
│       │   ├── artifacts.py    # Artifact enums
│       │   ├── company.py      # Company config
│       │   ├── manifests.py    # Source/Raw/Bronze manifests
│       │   ├── provenance.py   # Source provenance records
│       │   └── checkpoints.py  # Checkpoint schemas
│       └── storage/            # Storage utilities
│           ├── hashing.py      # SHA-256 hashing
│           ├── atomic.py       # Atomic writes
│           ├── checkpoints.py  # Checkpoint management
│           └── logging.py      # Structured logging with redaction
├── tests/
│   ├── conftest.py             # Pytest fixtures
│   ├── unit/                   # Unit tests
│   └── integration/            # Integration tests (M1+)
├── data/                       # .gitignored - generated data
│   ├── raw/
│   └── bronze/
├── pyproject.toml
├── .env.example
├── .gitignore
└── README.md
```

## Phase 2 - Identity Registry and Deterministic Masking (Complete)

Private identity registry with typed canonical entities and aliases.
Deterministic regex-based matching with offset tracking, overlap resolution,
and stable pseudonym replacement.

### Key Modules

- `src/fenrix_synthetic/identity/` - `EntityRegistry`, `PseudonymGenerator`, typed schemas
- `src/fenrix_synthetic/masking/` - `DeterministicMasker`, `OverlapResolver`, `DocumentReconstructor`
- `src/fenrix_synthetic/attacks/` - `ExactResidualScanner` for independent leak detection

### CLI Commands

```bash
# Validate an identity registry YAML file
fenrix-synth registry-validate --registry configs/examples/company_identity.example.yaml --company CANARY

# List entities and aliases
fenrix-synth registry-inventory --registry configs/examples/company_identity.example.yaml

# Run deterministic masking on a bronze document
fenrix-synth mask --company CANARY --data-root /tmp/canary-demo --bronze-artifact bronze-CANARY-000123456724000001 --masked-output /tmp/output.md --audit-output /tmp/audit.json --summary-output /tmp/summary.json

# Run exact residual scan
fenrix-synth scan --document /tmp/output.md --values scan-values.yaml
```

### Synthetic Demonstration

The test suite demonstrates the full pipeline with synthetic canary values:

- `tests/fixtures/canary_document.md` - Deliberately leaky document for scanner validation
- `tests/fixtures/clean_document.md` - Clean document proving zero false positives
- `tests/integration/test_masking_pipeline.py` - End-to-end synthetic pipeline verification

### Canary Status

The implementation is demonstrated with synthetic canary values only.
Actual masking of any real company requires a reviewed private registry
populated with real entities and aliases. Do not claim real-company
masking is complete without a reviewed private registry.

## Verification Commands

The CI workflow runs these commands automatically. Run them locally before pushing:

```bash
# Quality
ruff format --check && ruff check && mypy src/fenrix_synthetic

# Tests
pytest
```

### Full Verification

Run these commands to verify the implementation:

```bash
# 1. Package installs in clean environment
python -m venv venv && source venv/bin/activate && pip install -e ".[dev]"

# 2. CLI help works
fenrix-synth --help

# 3. Configuration validation works
fenrix-synth ingest --company INVALID  # Should fail with error

# 4. Deterministic JSON serialization
python -c "from fenrix_synthetic.schemas import BronzeManifest; print(BronzeManifest.model_json_schema())"

# 5. File hashing deterministic
fenrix-synth hash-file tests/fixtures/sec/filing.html  # Run twice, compare

# 6. Object hashing deterministic
python -c "from fenrix_synthetic.storage import hash_object; print(hash_object({'a': 1}))"

# 7. Atomic writes tested
pytest tests/unit/test_atomic.py -v

# 8. Invalid checkpoints rejected
pytest tests/unit/test_checkpoints.py::TestValidateCheckpoint::test_validate_no_checkpoint -v

# 9. Secret values redacted from logs
pytest tests/unit/test_logging.py -v

# 10. All tests run offline (no network)
pip uninstall requests -y && pytest  # Should still pass

# 11. Formatting, linting, type checking pass
ruff format --check && ruff check && mypy src/fenrix_synthetic

# 12. All tests pass
pytest

# 13. Phase 2 identity and masking tests
pytest tests/unit/test_identity_registry.py tests/unit/test_deterministic_masking.py tests/unit/test_overlap_resolution.py tests/unit/test_reconstruction.py tests/unit/test_sanitizer.py tests/unit/test_exact_residual.py

# 14. Phase 2 integration (masking pipeline)
pytest tests/integration/test_masking_pipeline.py

# 15. Phase 3A discovery and coverage tests
pytest tests/unit/test_residual_discovery.py tests/unit/test_coverage_report.py
```

## Phase 3A - Residual Discovery and Coverage Reporting

Pattern-based deterministic residual entity discovery. Identifies potential
entities that survived the deterministic masking pipeline without model dependency.
Does NOT include: GLiNER, NVIDIA provider, review queue, registry promotion,
remasking, or post-promotion rescanning.

### Key Modules

- `src/fenrix_synthetic/masking/discovery.py` - `ResidualEntityDiscoverer` for pattern-based residual scanning
- `src/fenrix_synthetic/reporting/coverage.py` - `CoverageReport` and `CoverageResult`

### CLI Commands

```bash
# Run residual discovery on a document
fenrix-synth discover --document /tmp/output.md

# Run discovery with masking audit for coverage statistics
fenrix-synth discover --document /tmp/output.md --audit /tmp/audit.json

# Write coverage report to file
fenrix-synth discover --document /tmp/output.md --audit /tmp/audit.json --output /tmp/coverage.json
```

### Privacy

Coverage reports use opaque finding IDs derived from non-private fields
(document artifact ID, entity type, start/end offsets) instead of plain-text
hashes. No private entity text, aliases, company names, tickers, domains, or
plain/truncated hashes of private values appear in sanitized outputs.

### Limitations

Phase 3A does not establish anonymity or release safety. It provides deterministic
evidence of coverage gaps. No reviewed real-company identity registry exists;
synthetic canary results do not prove real-company masking effectiveness.

## Phase 3B Core — Reviewed Provider-Neutral Entity Discovery (Current)

Provider-neutral discovery architecture with fake provider for deterministic
offline testing. Includes review queue, proposal generation, validation,
promotion, deduplication with disagreement tracking, and sanitized candidate
summaries with opaque IDs.

Does NOT include: GLiNER adapter, NVIDIA adapter, optional model dependency
groups, explicit live smoke commands, or provider-specific live tests.

### Key Modules

- `src/fenrix_synthetic/discovery/` - Provider protocol, fake provider, chunking, candidate deduplication, review queue, proposal promotion, sanitized reports
- `src/fenrix_synthetic/discovery/candidates.py` - `CandidateDeduplicator` with disagreement group tracking, `make_sanitized_summary` with opaque IDs
- `src/fenrix_synthetic/discovery/review.py` - `ReviewQueue` with accept/reject/defer/duplicate
- `src/fenrix_synthetic/discovery/promotion.py` - `create_proposals_from_reviews`, `promote_proposal`, `validate_proposal`

### CLI Commands

```bash
# Run Phase 3B model-assisted entity discovery
fenrix-synth discover3b --document /tmp/output.md

# Write sanitized report to file
fenrix-synth discover3b --document /tmp/output.md --output /tmp/discovery_report.json
```

### Privacy Design

- **Sanitized outputs** (Phase 3A coverage reports, Phase 3B candidate summaries, sanitized discovery reports) use opaque IDs derived from non-private fields only
- **No plain hashes of private text** appear in any sanitized output
- **Private artifacts** (`ProviderCandidate`, `PrivateDiscoveryArtifact`, `MaskingAudit`) may retain `matched_text_hash` for internal integrity
- **Two opaque ID schemes** (separate namespaces — not cross-referencable):
  - Phase 3A coverage: `opaque:v2:{doc_id}:{entity_type}:{start}:{end}`
  - Phase 3B discovery: `opaque:{candidate_id}`

### Deferred to Phase 3C

- GLiNER adapter
- NVIDIA adapter
- Optional model dependency groups
- Explicit live smoke commands
- Provider-specific live tests

### Limitations

Phase 3B Core does not establish anonymity or release safety. It provides a
reviewed pipeline for provider-neutral entity discovery with deterministic
offline testing. No real model execution exists — only the fake provider.
No reviewed real-company identity registry exists.

## Phase 3C — Optional Local GLiNER Discovery Adapter

Adds an optional adapter around the open-source `gliner==0.2.27` zero-shot
NER model. The adapter conforms to the existing `EntityDiscoveryProvider`
protocol so it slots into the Phase 3B pipeline (chunker → candidate
normalizer → deduplicator → risk scorer → review queue → sanitized report).

### Optional Dependency Boundary

GLiNER is **not** in the default install. Install explicitly with:

```bash
pip install -e ".[local-ner]"
```

Default CI and default tests do not install or require GLiNER. The
`pytest-socket` blocker and the new `local_model` marker ensure CI never
downloads model weights.

### Provider CLI

```bash
fenrix-synth providers list
fenrix-synth providers health --provider gliner_local
fenrix-synth providers prepare --provider gliner_local \
    --model urchade/gliner_small-v2.5 --allow-download
fenrix-synth discover-model --provider gliner_local \
    --document <path> --labels-config configs/entity_labels.yaml \
    --private-output-root <gitignored-path>
```

The `prepare` step is the only acquisition path. `discover model` writes a
sanitized report and, on request, a private artifact under the explicit
gitignored output root. The CLI refuses to write private data into a
directory tracked by git.

### Privacy Boundary

- Provider output goes into the existing `ReviewQueue` with `pending`
  status. No automatic acceptance.
- Sanitized reports use the same opaque IDs as Phase 3B.
- Private artifacts retain `matched_text_hash` for integrity but never
  publish into sanitized output.
- Model cache paths, weights, private text, and absolute host paths do
  not appear in sanitized output.

### Synthetic Benchmark

`src/fenrix_synthetic/discovery/providers/gliner/benchmark.py` carries a
committed, versioned, hashed benchmark of synthetic entities only. No
real company facts appear. Evaluation precision / recall / F1 (exact-span
and relaxed-overlap), per-type metrics, hard-negative hits, and
threshold sweep over `[0.30, 0.40, 0.50, 0.60, 0.70]` are recorded but
do not feed CI.

### Limitations

Phase 3C does **not** establish anonymity or release safety. Model
confidence is not calibrated leakage probability. Threshold selection
remains provisional. Local smoke is synthetic-only; no real-company
document is ever sent to a model in this milestone.

## Milestone 1 - SEC Extraction (Complete)

- SEC adapter interface with fixture loader
- One filing download/hash verification
- HTML extraction and boilerplate removal
- Raw and bronze manifest writing
- Checkpoint resume demonstration
- Full offline fixture-based demo

## Milestone 2 - Identity Registry (Complete)

- Phased identity registry with typed schemas
- Deterministic regex matching with offset tracking
- Overlap resolution and stable pseudonym replacement
- Independent exact residual scanning
- Canary and mutation testing fixtures
- Synthetic end-to-end demonstration

## CI

GitHub Actions runs on PRs targeting `main` and on pushes to `main`:

| Job | Checks |
|-----|--------|
| **Quality** | `ruff format --check`, `ruff check`, `mypy src/fenrix_synthetic` |
| **Tests** | `pytest --collect-only`, full `pytest` suite |

The CI requires no private company registry, no network access, and no model
downloads. Model adapters (GLiNER, NVIDIA) remain deferred to Phase 3C.
Optional dependencies are not installed.

To reproduce locally:

```bash
pip install -e ".[dev]"
ruff format --check && ruff check && mypy src/fenrix_synthetic
pytest
```

### Branch Protection

Recommended required checks for `main`:
- `Quality (ruff + mypy)`
- `Tests (pytest)`

## Security

- **Never commit**: `data/`, `configs/company.yaml`, `.env`, `*.key`, `*.secret`
- **Logs redact**: KEY, TOKEN, SECRET, PASSWORD, AUTH, CREDENTIAL patterns
- **Private mappings**: Company ID → source identity kept in gitignored config

## License

MIT