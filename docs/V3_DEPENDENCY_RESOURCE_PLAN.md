# V3 Dependency / Resource Plan

**Branch:** `feature/professor-bundle-pipeline`
**Date:** 2026-06-24

## Already in Repo (from `pyproject.toml`)

| Package | Where Used | Notes |
|---------|-----------|-------|
| `pydantic` | All manifest/schema modules | Core; used extensively |
| `pandas` | transforms/, utility/, pipeline/ | Structured data + CSV handling |
| `numpy` | transforms/, metrics/ | Numeric operations |
| `requests` | sec/ client, collectors/ | SEC API + HTTP |
| `beautifulsoup4` | sec/ extraction | SEC filing HTML parsing |
| `lxml` | sec/ extraction | XBRL/iXBRL parsing |
| `click` | cli.py | CLI framework |
| `pyyaml` | config/, providers/ | Config files |
| `orjson` | All JSON serialization | Fast JSON |
| `scikit-learn` | Not yet used in V3 | Available if needed for similarity |
| `python-dotenv` | cli.py | Env loading (safe, optional) |
| `gliner` | discovery/providers/gliner/ | Optional `local-ner` extra |

## Safe Core Dependencies (already present, no new installs needed)

- **pydantic**: Schema validation for all artifacts
- **pandas/numpy**: Numeric transformation (Phase 5)
- **requests**: SEC API calls (already used)
- **click**: CLI (already used)
- **orjson**: JSON (already used)
- **pyyaml**: Config (already used)

## Optional Extras for Phase 4+

These are NOT installed and do NOT need to be installed now. Evaluate only when the feature requires them:

### Numeric / Statistical
- **sdv / sdmetrics**: Synthetic data quality/privacy evaluation. Heavy dependency. Only add if simple statistical checks are insufficient.
- **opendp**: Formal differential privacy. Only if DP experiments are funded and scoped.
- **diffprivlib**: Python DP library. Lighter than OpenDP but still optional.

### Text / NLP
- **sentence-transformers**: Text similarity for attack harness. Heavy. Only if regex-based similarity is insufficient.
- **rapidfuzz**: Lightweight fuzzy matching. Install if fuzzy identifier scanning is needed.
- **spacy**: Heavy NLP. Unlikely to be needed given existing GLiNER approach.

### XBRL / SEC Parsing
- **arelle-release** (Arelle): Full XBRL/iXBRL validation. Heavy. Only if the simple SEC client + lxml approach fails to parse necessary filings.

### Attack / LLM
- **litellm** or **openai**: For AI-based blind guessing attacks. Optional; gated behind `--enable-llm-attacks` flag. Never required for CI.
- **nvidia-nim** or equivalent: For NVIDIA review adapter. Gated behind `enable_nvidia` config flag.

## Free / Public Data Sources

All are already used or planned. No new API keys or paid services required.

| Source | Status | Use |
|--------|--------|-----|
| SEC EDGAR (data.sec.gov) | Already used | Submissions API, filing downloads |
| SEC companyfacts | Already accessible | XBRL facts |
| SEC bulk ZIPs | Already accessible | Archive-based ingestion |
| yfinance | Already used | Price data (cached, unofficial) |
| GDELT | Planned | Synthetic news/event themes |
| FRED | Planned | Macro context |

## Do Not Add Yet

These would bloat the repo without proven need:

- **Notebooks in production path**: Keep in `notebooks/` for demos only
- **Paid API wrappers**: No paid services required for core pipeline
- **Vector databases** (Chroma, Pinecone, etc.): Not needed
- **LangChain / CrewAI**: Heavy orchestration frameworks; 19-stage pipeline already covers orchestration
- **Browser automation**: Not needed for data pipeline
- **Large local LLMs**: Tokenizer-only if needed; full models not required
- **Full EDGAR database frameworks**: Simple SEC API client is sufficient
- **Kubernetes / cloud infra**: Not in scope
- **Dashboard frameworks**: Not in scope
- **Graph databases**: Not needed

## Phase-Specific Dependency Needs

### Phase 4 (Peer Archetype Anonymization)
- **No new dependencies**: Uses existing pydantic + pandas + numpy

### Phase 5 (Numeric Transformation)
- **No new dependencies**: Uses existing pandas + numpy
- May use **scipy.stats** for distribution sampling (already a transitive dep)

### Phase 6 (Trajectory Morphing)
- **No new dependencies**: Uses existing numpy/pandas

### Phase 7 (Text Generalization)
- **No new dependencies**: Deterministic rules + existing GLiNER
- If needed: **rapidfuzz** for fuzzy matching (~500KB, very lightweight)

### Phase 8 (Synthetic News)
- **No new dependencies**: Template-based generation

### Phase 9 (LLM Blind Guess)
- **Optional**: `openai` or `litellm` for API-based attacks
- Must be gated behind CLI flag and never required for CI

### Phase 10 (Release Packaging)
- **Already complete** (Phase 2/3)

## Install Command for Current State

```bash
pip install -e ".[dev]"
```

No additional extras needed for Phase 4-8.
