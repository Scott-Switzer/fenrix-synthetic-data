# V3 Phase 8C: Final Validation & Live LLM Review

## Overview

Phase 8C hardens the Phase 8A/8B implementation with live validation,
utility preservation scoring, and production source-map propagation.

## .env Loading

`.env` is loaded at CLI startup using `python-dotenv` (already a dependency).
The file is listed in `.gitignore` — never commit it.

```bash
# .env (gitignored, never committed)
NVIDIA_API_KEY=nvapi-...
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=meta/llama-3.1-70b-instruct
```

### Safe key detection
```python
# At CLI startup, python-dotenv loads .env into os.environ
# The key is read from os.environ.get("NVIDIA_API_KEY")
# Values are NEVER printed, logged, or persisted to artifacts
```

## Does NVIDIA Run by Default?

**No.** The default provider is `offline_stub`. NVIDIA/OpenAI-compatible
review runs ONLY when:

1. `--llm-review-provider openai_compatible` is set
2. `NVIDIA_API_KEY` (or the configured env var) is present

## How to Enable NVIDIA/OpenAI Review

### 1. Set up .env
```bash
cp .env.example .env  # if template exists
# Edit .env with your API key
```

### 2. Run with live review
```bash
fenrix-synth build-professor-bundle \
  --config configs/professor_bundle.fixture.yaml \
  --strict \
  --llm-review-provider openai_compatible \
  --llm-review-api-key-env NVIDIA_API_KEY \
  --llm-review-strict \
  --source-mapping ~/fenrix-data/private/source_companies.yaml
```

## How LLM Confidence is Scored

| Condition | Verdict |
|-----------|---------|
| Model refuses to guess | **PASS** |
| Low confidence, no actual source match | **PASS** |
| Medium confidence, actual source NOT in candidates | **WARN** |
| Medium confidence, actual source IN candidates | **FAIL** |
| High confidence (even if wrong) | **FAIL** |
| Actual source is top-1 guess | **FAIL** |
| Actual source in top-3 | **FAIL** |
| Provider error (strict mode) | **FAIL** |

## How Utility Preservation is Scored

Nine signal dimensions are compared between private source thesis
and public packet thesis:

1. **business_model** (weight: 0.20)
2. **product_exposure** (weight: 0.10)
3. **fundamentals_signal** (weight: 0.10)
4. **valuation_signal** (weight: 0.10)
5. **profitability_signal** (weight: 0.10)
6. **balance_sheet_signal** (weight: 0.10)
7. **growth_signal** (weight: 0.10)
8. **risk_signals** (weight: 0.10)
9. **market_signal** (weight: 0.10)

Thresholds:
- **>= 0.70** → PASS (same broad thesis preserved)
- **0.55–0.70** → WARN (partially preserved)
- **< 0.55** → FAIL (thesis lost)

## What "Same Message, Sanitized" Means

The public packet should communicate the same broad investment/finance
thesis as the source, but WITHOUT revealing specific identity:

- ✅ "Banking company with strong fundamentals and regulatory exposure"  
- ❌ "JPMorgan Chase reported $50B in Q4 revenue"  
- ✅ "Diversified financial services with consumer and commercial banking"  
- ❌ "Wells Fargo's CEO commented on the earnings beat"  

Specific brands, counterparties, exact dates, exact numbers must not
survive sanitization. Broad sector, signal direction, and risk categories
should survive.

## What Blocks Release

A release is blocked if ANY of:

1. Direct identifier hits > 0
2. Metadata identifier hits > 0
3. Exact number matches > 0
4. Trajectory source in top-3
5. Filing reconstruction leakage
6. LLM blind guess: top-1 hit
7. LLM blind guess: top-3 hit
8. LLM blind guess: high confidence
9. LLM blind guess: medium confidence + actual source in candidates
10. Utility preservation score < 0.55 (thesis lost)
11. Strict release gate failures
12. ZIP contains forbidden paths/extensions

## Where Final Artifacts Are Written

```
{output_root}/
├── public/
│   ├── anonymized/COMPANY_001/
│   │   ├── profile/
│   │   ├── sec/
│   │   ├── metrics/
│   │   ├── news/
│   │   └── market/
│   └── ...
├── private/
│   └── qa/
│       ├── llm_blind_guess_private.json
│       ├── utility_preservation_private.json
│       └── news_reconstruction_private.json
├── qa/
│   ├── public_release_gate.json
│   ├── llm_blind_guess_summary.json
│   ├── utility_preservation_summary.json
│   ├── news_reconstruction_attack_summary.json
│   └── ...
├── exports/
│   └── anonymized_bundle.zip
├── RELEASE_MANIFEST.json
└── run_summary.json
```

## CLI Reference

```bash
# Offline fixture (CI default)
fenrix-synth build-professor-bundle \
  --config configs/professor_bundle.fixture.yaml \
  --fast-fixtures \
  --llm-review-provider offline_stub

# Live NVIDIA review with source mapping
fenrix-synth build-professor-bundle \
  --config configs/professor_bundle.fixture.yaml \
  --strict \
  --llm-review-provider openai_compatible \
  --llm-review-api-key-env NVIDIA_API_KEY \
  --source-mapping ~/fenrix-data/private/source_companies.yaml
```

## Test Suite

```bash
# Unit tests
pytest tests/unit/test_utility_preservation.py

# Integration tests
pytest tests/integration/test_professor_bundle_utility_preservation_stage.py
pytest tests/integration/test_live_llm_provider_configuration.py

# Bundle smoke test (offline)
fenrix-synth build-professor-bundle \
  --config configs/professor_bundle.fixture.yaml \
  --fast-fixtures \
  --output-root /tmp/fenrix_phase8c_smoke \
  --llm-review-provider offline_stub
```

## Phase 8D: Live Validation Complete (2026-06-24)

### Live NVIDIA/OpenAI-compatible Review

- **NVIDIA key detected**: Yes (via `.env`, never committed)
- **httpx installed**: Yes (v0.28.1, declared in `pyproject.toml` as production dependency)
- **Live request attempted**: Yes
- **Live request succeeded**: Yes (HTTP 200 from `https://integrate.api.nvidia.com/v1/chat/completions`)
- **Model used**: `$NVIDIA_MODEL` (meta/llama-3.1-70b-instruct or equivalent)
- **Companies reviewed**: COMPANY_001
- **Any high confidence**: No
- **Any actual source top-1**: No
- **Any actual source top-3**: No
- **Live validation status**: `LIVE_LLM_VALIDATED`

### How to Reproduce Live Review

```bash
# Load .env
set -a && source .env && set +a

# Run with live NVIDIA review
python -m fenrix_synthetic.cli build-professor-bundle \
  --config configs/professor_bundle.fixture.yaml \
  --fast-fixtures \
  --output /tmp/fenrix_live_smoke \
  --llm-review-provider openai_compatible \
  --llm-review-base-url "$NVIDIA_BASE_URL" \
  --llm-review-model "$NVIDIA_MODEL" \
  --llm-review-api-key-env NVIDIA_API_KEY \
  --llm-review-strict
```

### Live Validation Status Values

Added to `StageRegistry` and `beta_status`:

| Status | Meaning |
|--------|--------|
| `NOT_ATTEMPTED` | No live provider configured or fixture mode |
| `NOT_LIVE_VALIDATED` | Live provider available but not run |
| `LIVE_LLM_VALIDATED` | Live review ran and confidence gate passed |
| `LIVE_LLM_FAILED` | Live review ran but model identified source |
| `PROVIDER_ERROR` | Live review attempted but API error occurred |
| `PRODUCTION_CANDIDATE_READY` | All stages pass + live validation passes |

### httpx Dependency

`httpx>=0.28` is a production dependency in `pyproject.toml`. Install with:

```bash
pip install -e .
```

On Lightning.ai with externally-managed Python:
```bash
pip install httpx --break-system-packages
```

### Test Results (Phase 8D)

- ruff: clean
- mypy: clean
- All focused tests pass
- Live NVIDIA smoke: PASS (LIVE_LLM_VALIDATED)
- Offline smoke: PASS (STRICT_FIXTURE_READY)
- ZIP validations: Both pass, all required files present, zero forbidden entries

