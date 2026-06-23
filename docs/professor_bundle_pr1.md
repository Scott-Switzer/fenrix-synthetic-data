# PR1: Professor-Bundle Pipeline — Phase 0 + Phase 1

## Branch
`feature/professor-bundle-pipeline` (off `feature/sec-faithful-professor-alpha-v2`)

## Summary

This PR implements the first integrated scaffold for a classroom-grade FenriX
professor bundle builder. It establishes a mandatory, reproducible 18-stage
pipeline that cannot be marked ready unless every required privacy, provenance,
SEC, metrics, cross-link, pedagogy, QA, and release-gate stage actually ran.

### Phase 0: Identity Leak Guardrail Repair

- **Removed all real source identifiers** (HBAN, NVDA, CL, PEP, TJX, PM, AMZN,
  BLK, GOOGL, SNDK, Huntington, Colgate, PepsiCo, etc.) from all tracked files:
  README, configs, docs, notebooks, source, tests.
- **Moved `COMPANY_DATA`** (real CIKs, aliases, domains, executives) from
  `submission_quality.py` to a gitignored `private/company_data.py` module.
  Tracked code uses canary-only data + runtime loader.
- **Gitignored `professor_alpha_v2.py`** (2,822-line monolith with 19 real-
  ticker references) — kept as local reference, never tracked.
- **Added `test_public_identity_leak_gate.py`**: scans all tracked files for
  real tickers, company names, CIKs, accession numbers, SEC URLs, local paths,
  API keys. Fails on any hit outside an allowlist.

### Phase 1: Minimal End-to-End Strict Fixture Bundle

New package: `src/fenrix_synthetic/professor/`

| Module | Responsibility |
|--------|---------------|
| `stages.py` | 19-value `ProfessorStage` enum, `StageStatusRecord`, `StageRegistry` with `professor_ready` property that is `True` only when all stages PASS with evidence |
| `evidence.py` | 14 evidence objects (`SourceFiling`, `SourceSection`, `DetectedEntity`, etc.), provenance key builders, public serialization guard (`PublicSerializationError`) |
| `sec_providers.py` | `SecProvider` ABC + `FixtureSecProvider` with semantic section validation (10-K Item 7/8, 10-Q Item 2, Q4 10-Q rejection, future-dated 8-K rejection) |
| `providers.py` | `MockGLiNERProvider`, `MockNVIDIAReviewer`, `MockMetricsSynthesizer` for deterministic offline testing |
| `orchestrator.py` | `ProfessorBundleOrchestrator` runs all 18+1 stages end-to-end, produces full output tree with real artifacts, provenance keys, QA reports, and ZIP |

New gate: `src/fenrix_synthetic/release/classroom_gate.py`
- `evaluate_classroom_gate()` with blocking conditions: missing stages, identity
  leaks, missing QA reports, missing classroom docs, empty-evidence QA pass,
  ZIP containing private paths, missing checksums, PROVIDER_NOT_RUN in strict
- CLI: `python -m fenrix_synthetic.release.classroom_gate --bundle-root ... --output ...`

New CLI: `python -m fenrix_synthetic.cli build-professor-bundle --config ... --output-root ... --strict`

### Stage Registry (19 stages)

| # | Stage | Evidence Required |
|---|-------|------------------|
| 1 | SOURCE_INGESTION | Yes |
| 2 | SEC_PARSE | Yes |
| 3 | SECTION_EXTRACT | Yes |
| 4 | ENTITY_DETECT_GLINER | Yes |
| 5 | ENTITY_DETECT_RULES | Yes |
| 6 | ENTITY_RESOLVE | No |
| 7 | DEIDENTIFY | Yes |
| 8 | PRIVATE_EVIDENCE_BUILD | Yes |
| 9 | SYNTHETIC_PROFILE_BUILD | No |
| 10 | FILING_RECONSTRUCT | Yes |
| 11 | METRIC_SYNTHESIS | Yes |
| 12 | METRIC_EVALUATION | Yes |
| 13 | NEWS_RECONSTRUCT | Yes |
| 14 | CROSSLINK_BUILD | Yes |
| 15 | PEDAGOGY_BUILD | Yes |
| 16 | RAG_INDEX_BUILD | Yes |
| 17 | ADVERSARIAL_QA | Yes |
| 18 | RELEASE_GATE | No |
| 19 | ZIP_EXPORT | Yes |

### Output Tree

```
runs/professor_bundle_fixture/
  public/
    README.md, CLASSROOM_GUIDE.md, PROFESSOR_AUDIT_GUIDE.md,
    EXERCISES.md, ANSWER_KEY_STUB.md, RUBRIC.md
    anonymized/COMPANY_001/
      sec/ (item_1.md, item_1a.md, item_7.md, item_8.md, *.json tables)
      news/ (news_001.md, news_002.md, news_003.md)
      metrics/ (daily_prices.json, returns.json, volume.json, fundamentals.json, ratios.json)
      LEARNING_GUIDE.md, crosslinks.json
  private/
    evidence/ (evidence_graph.json)
  qa/
    stage_registry.json, entity_audit_report.json,
    metrics_quality_report.json, metrics_privacy_report.json, metrics_schema_report.json,
    rag_index_report.json, adversarial_qa_report.json, classroom_gate_report.json
  exports/
    anonymized_bundle.zip
  checksums.sha256, run_summary.json, artifact_inventory.csv
```

### Strict vs CI Contract

| Mode | NVIDIA | CI behavior | Bundle status |
|------|--------|-------------|---------------|
| `--strict` | Real API key required | Cannot run in default CI | `professor_ready=true` only if all stages PASS with real providers |
| `--fast-fixtures` | Mock provider | Runs in CI | `strict_fixture_ready=true` (mock providers pass), `professor_ready=false` |
| `--allow-provider-skip-for-local-dev` | Absent → PROVIDER_NOT_RUN | Runs in CI | `professor_ready=false`, `beta_status=NOT_PROFESSOR_READY` |

### Tests Added

- `test_public_identity_leak_gate.py` (8 tests)
- `test_stage_registry_required_stages.py` (8 tests)
- `test_professor_ready_requires_all_mandatory_stages.py` (8 tests)
- `test_sec_semantic_gate.py` (12 tests)
- `test_gliner_entity_audit_gate.py` (8 tests)
- `test_private_public_evidence_boundary.py` (11 tests)
- `test_professor_bundle_fixture_build.py` (16 integration tests)
- `test_classroom_gate_seeded_failures.py` (12 integration tests)

### Quality Gates

- `ruff format --check`: 208 files formatted
- `ruff check`: All checks passed
- `mypy src/fenrix_synthetic`: No issues found in 144 source files
- `pytest`: 1084 passed, 5 skipped

### What is Mocked

- GLiNER: `MockGLiNERProvider` (deterministic span detection)
- NVIDIA: `MockNVIDIAReviewer` (deterministic PASS verdict with evidence)
- SDV/CTGAN: `MockMetricsSynthesizer` (deterministic issuer-specific metrics)
- SEC: `FixtureSecProvider` (synthetic 10-K with proper Item structure)

### What is NOT Mocked

- Stage registry validation (real `professor_ready` logic)
- Release gate (real blocking conditions)
- Evidence boundary (real `PublicSerializationError`)
- SEC semantic validation (real Item 7/8/Q4 checks)
- Identity leak scanning (real canary pattern matching)
- ZIP export (real ZIP creation with path exclusion)
- Checksums, run summary, artifact inventory

### Verdict

`STRICT_FIXTURE_PIPELINE_READY_NON_PRODUCTION` — the fixture build passes all 19 stages,
produces a real ZIP with real artifacts, and the gate returns PASS with
`strict_fixture_ready=true`, `professor_ready=false`, `release_safe=false`,
`beta_status=STRICT_FIXTURE_READY`.
