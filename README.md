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

## Verification Commands

Run these commands to verify the M0 implementation:

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
```

## Milestone 1 - Next Steps

HBAN extraction vertical slice:
- SEC adapter interface with fixture loader
- One filing download/hash verification
- HTML extraction and boilerplate removal
- Raw and bronze manifest writing
- Checkpoint resume demonstration
- Full offline fixture-based demo

## Security

- **Never commit**: `data/`, `configs/company.yaml`, `.env`, `*.key`, `*.secret`
- **Logs redact**: KEY, TOKEN, SECRET, PASSWORD, AUTH, CREDENTIAL patterns
- **Private mappings**: Company ID → source identity kept in gitignored config

## License

MIT