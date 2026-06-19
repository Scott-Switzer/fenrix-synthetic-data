# FENRIX Synthetic Data Worker

Reproducible company-level financial-data masking and re-identification testing system.

Current vertical slice: **HBAN (C001)**

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
  C001:
    source_identity: "HBAN"  # Private source identity
    data_root: "data"
    raw_dir: "data/raw"
    bronze_dir: "data/bronze"
```

**Never commit `configs/company.yaml`** - it's in `.gitignore`.

### Campaign Configuration

Edit `configs/campaign.yaml` to control pipeline execution:
```yaml
company_id: "C001"
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
fenrix-synth ingest --company C001
fenrix-synth extract --company C001
fenrix-synth campaign --company C001 --resume
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

## Phase 2 - Identity Registry and Deterministic Masking (In Progress)

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
fenrix-synth registry-validate --registry configs/examples/company_identity.example.yaml --company C001

# List entities and aliases
fenrix-synth registry-inventory --registry configs/examples/company_identity.example.yaml

# Run deterministic masking on a bronze document
fenrix-synth mask --company C001 --data-root /tmp/c001-demo --bronze-artifact bronze-C001-000123456724000001 --masked-output /tmp/output.md --audit-output /tmp/audit.json --summary-output /tmp/summary.json

# Run exact residual scan
fenrix-synth scan --document /tmp/output.md --values scan-values.yaml
```

### Synthetic Demonstration

The test suite demonstrates the full pipeline with synthetic canary values:

- `tests/fixtures/canary_document.md` - Deliberately leaky document for scanner validation
- `tests/fixtures/clean_document.md` - Clean document proving zero false positives
- `tests/integration/test_masking_pipeline.py` - End-to-end synthetic pipeline verification

### C001 Status

⚠️ **Synthetic only.** The C001 implementation is demonstrated with canary values.
Actual HBAN masking requires a reviewed private registry populated with real
HBAN entities and aliases. Do not claim HBAN masking is complete.

## Verification Commands

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
```

## Milestone 1 - HBAN Extraction (Complete)

- SEC adapter interface with fixture loader
- One filing download/hash verification
- HTML extraction and boilerplate removal
- Raw and bronze manifest writing
- Checkpoint resume demonstration
- Full offline fixture-based demo

## Milestone 2 - Identity Registry (In Progress)

- Phased identity registry with typed schemas
- Deterministic regex matching with offset tracking
- Overlap resolution and stable pseudonym replacement
- Independent exact residual scanning
- Canary and mutation testing fixtures
- Synthetic end-to-end demonstration

## Security

- **Never commit**: `data/`, `configs/company.yaml`, `.env`, `*.key`, `*.secret`
- **Logs redact**: KEY, TOKEN, SECRET, PASSWORD, AUTH, CREDENTIAL patterns
- **Private mappings**: Company ID → source identity kept in gitignored config

## License

MIT