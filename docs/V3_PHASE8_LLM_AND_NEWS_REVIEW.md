# V3 Phase 8: LLM Blind Review & Synthetic News Reconstruction

## Overview

Phase 8 adds two major capabilities to the professor bundle pipeline:

1. **Phase 8A — LLM Blind-Guess Review** (`LLM_BLIND_GUESS` stage):
   An adversarial LLM reviews ONLY public bundle content and attempts to
   identify the real source company. The release fails if the model can
   confidently identify the source.

2. **Phase 8B — Synthetic News Reconstruction** (`NEWS_RECONSTRUCT` stage):
   Synthetic news briefs are generated from private source events (8-K
   filings, news archives) with all real identifiers removed.

## Stage Order

```
PEER_ARCHETYPE
FILING_RECONSTRUCT
METRIC_SYNTHESIS
METRIC_EVALUATION
NEWS_RECONSTRUCT
CROSSLINK_BUILD
PEDAGOGY_BUILD
RAG_INDEX_BUILD
ADVERSARIAL_QA
RELEASE_GATE
LLM_BLIND_GUESS    ← NEW (runs after release gate, before ZIP)
ZIP_EXPORT
```

LLM_BLIND_GUESS runs **after** the release gate and **immediately before**
ZIP export. This ensures the LLM reviews the exact public content that
would be sent to the professor.

## NVIDIA / Live Provider Behavior

### Is NVIDIA API called by default?

**No.** The default provider is `offline_stub`, which is deterministic and
requires no network access. This is the CI-safe default.

### How to run offline stub (CI default)

```bash
fenrix-synth build-professor-bundle \
  --config configs/professor_bundle.fixture.yaml \
  --fast-fixtures \
  --llm-review-provider offline_stub
```

### How to run live OpenAI-compatible review

```bash
fenrix-synth build-professor-bundle \
  --config configs/professor_bundle.production.example.yaml \
  --strict \
  --llm-review-provider openai_compatible \
  --llm-review-api-key-env NVIDIA_API_KEY \
  --llm-review-strict
```

### How to configure NVIDIA / API key safely

1. Set the environment variable (never commit to repo):
   ```bash
   export NVIDIA_API_KEY="nvapi-..."
   ```

2. Use `--llm-review-api-key-env NVIDIA_API_KEY` (default)

3. For other providers:
   ```bash
   # OpenRouter
   export OPENROUTER_API_KEY="sk-or-..."
   fenrix-synth build-professor-bundle ... \
     --llm-review-provider openai_compatible \
     --llm-review-api-key-env OPENROUTER_API_KEY \
     --llm-review-base-url https://openrouter.ai/api/v1 \
     --llm-review-model openai/gpt-4o

   # Local Ollama
   fenrix-synth build-professor-bundle ... \
     --llm-review-provider local_ollama \
     --llm-review-base-url http://localhost:11434/v1 \
     --llm-review-model llama3.2
   ```

## Confidence Scoring Rules

| Condition | Verdict |
|-----------|---------|
| Model refuses to guess (no justified guess) | **PASS** |
| Low confidence, no actual source in candidates | **PASS** |
| Medium confidence, actual source NOT in candidates | **WARN** |
| Medium confidence, actual source IN candidates | **FAIL** |
| High confidence (even if wrong company) | **FAIL** |
| Actual source is top-1 guess | **FAIL** |
| Actual source is in top-3 candidates | **FAIL** |
| Provider error (strict mode) | **FAIL** |
| Provider error (non-strict mode) | **WARN** |
| Malformed model output (strict mode) | **FAIL** |

### Target Behavior

```
direct_identifier_hits = 0
metadata_identifier_hits = 0
exact_number_matches = 0
trajectory_source_top_3 = false
filing_reconstruction_leakage = false
llm_actual_source_top_1 = false
llm_actual_source_top_3 = false
llm_confidence_high = false
llm_confidence_medium_with_actual_candidate = false
target_outcome = low confidence or no justified guess
```

## Public / Private LLM Reports

### Private report
- Location: `private/qa/llm_blind_guess_private.json`
- Contains: full scoring details, actual source company/ticker mapping,
  raw model response, top-1/top-3 hit status
- **Never included in ZIP export**

### Public report
- Location: `qa/llm_blind_guess_summary.json`
- Contains: pass/fail verdict, confidence level, model's guesses,
  candidate count, redacted reasoning
- **Actual source mapping is excluded**
- **Included in ZIP export**

## Synthetic News Rules

### Public news must NOT contain:
- Original headlines
- Exact article URLs
- Publication names (Reuters, Bloomberg, etc.)
- Exact article dates (YYYY-MM-DD, Month DD YYYY)
- Exact deal values ($5B, etc.)
- Named counterparties (if identifying)
- Executive quotes ("said CEO Name,")
- Unique acquisition or litigation names
- Source ticker/company names
- Raw 8-K item text

### Public news SHOULD contain:
- Relative period (e.g., "Year -2, Q3")
- Controlled event class (e.g., "demand_shift", "capital_allocation")
- Synthetic event title
- Broad description
- Market/financial relevance
- Uncertainty note
- Anonymized company ID only

### Event Classes
```
demand_shift
margin_pressure
regulatory_development
capital_allocation
leadership_governance
litigation_legal_risk
supply_chain
product_category_expansion
macro_sensitivity
financing_liquidity
strategic_investment
competitive_pressure
```

## News Attack Rules

The `NewsReconstructionAttack` checks public news output for:

1. Copied headline substrings (≥4 words overlap)
2. URL patterns (https://...)
3. Exact source company names/tickers
4. Exact dates (ISO and written formats)
5. Named counterparties
6. Executive quote patterns
7. **Verbatim text overlap** (≥40 consecutive characters matching source body snippets)

### Output
- `qa/news_reconstruction_attack_summary.json` — public, pass/fail with findings
- `private/qa/news_reconstruction_private.json` — private provenance (hashes only)

## Final Release Criteria

A release passes only when ALL of these are true:

1. `direct_identifier_hits = 0`
2. `metadata_identifier_hits = 0`
3. `exact_number_matches = 0`
4. No trajectory source in top-3
5. No filing reconstruction leakage
6. **LLM blind guess: no top-1, no top-3, no high confidence** (Phase 8A)
7. **LLM blind guess: no medium confidence with actual candidate**
8. **News attack: zero blocking findings** (Phase 8B)
9. Strict release gate passes

## CLI Reference

```bash
# Offline stub (CI default)
fenrix-synth build-professor-bundle \
  --config config.yaml --fast-fixtures \
  --llm-review-provider offline_stub

# Live NVIDIA review
fenrix-synth build-professor-bundle \
  --config config.yaml --strict \
  --llm-review-provider openai_compatible \
  --llm-review-api-key-env NVIDIA_API_KEY \
  --llm-review-strict

# Skip LLM review entirely
fenrix-synth build-professor-bundle \
  --config config.yaml --skip-live-llm-review
```

## Test Suite

```bash
# Unit tests
pytest tests/unit/test_llm_blind_guess.py
pytest tests/unit/test_llm_confidence_scoring.py
pytest tests/unit/test_news_reconstructor.py
pytest tests/unit/test_news_reconstruction_attack.py

# Integration tests
pytest tests/integration/test_professor_bundle_llm_blind_guess_stage.py
pytest tests/integration/test_professor_bundle_news_reconstruction_stage.py
```
