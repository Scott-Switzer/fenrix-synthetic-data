# V3 Architecture Audit

## 1. Repository Snapshot

| Field | Value |
|-------|-------|
| **Branch** | `feature/professor-bundle-pipeline` |
| **HEAD SHA** | `afae46d814e3cd276b4c2d87e20d64294a067162` |
| **Python** | 3.12+ |
| **Package** | `fenrix-synthetic` (MIT) |
| **Build** | `setuptools` via `pyproject.toml` |

### Git Working Tree State (at audit time)
- **5 modified files** (professor orchestrator, classroom gate, fixture regression test, leak gate test, production config)
- **8 untracked files** (review/metrics providers, adversarial review/metrics privacy policies, Phase 2b integration tests, provider contract tests)

## 2. CLI Command Inventory

### Production Commands (implemented)
| Command | Phase | Status |
|---------|-------|--------|
| `hash` | M0 | Stable — SHA-256 text hashing |
| `hash-file` | M0 | Stable — SHA-256 file hashing |
| `hash-json` | M0 | Stable — deterministic JSON hashing |
| `extract` | M1 | Stable — SEC filing discovery + extraction |
| `campaign` | M1 | Stable — campaign validation (read-only) |
| `registry-validate` | Phase 2 | Stable — identity registry validation |
| `registry-inventory` | Phase 2 | Stable — registry listing with sanitization |
| `mask` | Phase 2 | Stable — deterministic masking pipeline |
| `scan` | Phase 2 | Stable — exact residual scanning |
| `discover` | Phase 3A | Stable — pattern-based entity discovery |
| `discover3b` | Phase 3B | Stable — fake provider discovery |
| `providers list` | Phase 3C | Stable — list available providers |
| `providers health` | Phase 3C | Stable — provider health check |
| `providers prepare` | Phase 3C | Stable — explicit model download |
| `providers ingest-colab` | Phase 3C | Stable — Colab evidence ingestion |
| `discover-model` | Phase 3C | Stable — GLiNER model discovery |
| `identities-compile` | Phase 4 | Stable — atlas compilation |
| `structured-transform` | Phase 4 | Stable — S0/S1/S2 transforms |
| `attack-run` | Phase 4 | Stable — text re-ID attacks |
| `utility-evaluate` | Phase 4 | Stable — utility evaluation |
| `release-assess` | Phase 4 | Stable — gate assessment |
| `release-export` | Phase 4 | Stable — dossier export |
| `boundary-diag` | Phase 4 | Stable — boundary diagnostic |
| `reanonymize-run` | Phase 4 | Stable — re-anonymization orchestrator |
| `pipeline-run` | Phase 5A | Stable — multi-company pipeline |
| `pilot-run` | Phase 4 | Stable — 18-stage pilot |
| `build-professor-bundle` | Phase 5A | Stable — professor bundle (18+1 stages) |
| `s3-transform` | Phase 5A | Stable — S3 feature-only transform |
| `s3-attack` | Phase 5A | Stable — S3 attack runner |
| `s3-assess` | Phase 5A | Stable — S3 gate assessment |
| `evaluate-submission` | Phase 5A | Stable — submission evaluation |
| `atlas-harvest` | Phase 5A | Stable — atlas harvesting |
| `classroom-build` | Phase 5A | Stable — classroom package builder |

### Placeholder/Deprecated Commands
| Command | Status |
|---------|--------|
| `ingest` | Placeholder ("not yet implemented M2") |

## 3. Source Module Inventory

### Core Infrastructure (`src/fenrix_synthetic/`)
| Module | Lines | Purpose | V3 Status |
|--------|-------|---------|-----------|
| `cli.py` | ~1600 | All CLI commands (monolithic) | **Refactor**: split by domain |
| `__init__.py` | — | Version export | Keep |
| `cli_s3.py` | — | Phase 5A S3 CLI commands | Keep |
| `cli_errors.py` | — | CLI error handling | Keep |

### Configuration (`src/fenrix_synthetic/config/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `__init__.py` | Config exports | Keep |
| `loading.py` | YAML/JSON config loading | Keep |
| `settings.py` | Pydantic settings | Keep |

### Schemas (`src/fenrix_synthetic/schemas/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `artifacts.py` | Artifact enums/types | Keep |
| `company.py` | Company config schema | Keep |
| `manifests.py` | Manifest schemas | Keep |
| `sec.py` | SEC filing schemas | Keep |
| `checkpoints.py` | Checkpoint schemas | Keep |
| `provenance.py` | Provenance records | Keep |

### Storage/I/O (`src/fenrix_synthetic/storage/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `hashing.py` | SHA-256 file/object hashing | Keep |
| `atomic.py` | Atomic writes | Keep |
| `checkpoints.py` | Checkpoint save/load/validate | Keep |
| `logging.py` | Structured JSON logging w/ redaction | Keep |
| `checksums.py` | Checksum computation | Keep |

### Pipeline (`src/fenrix_synthetic/pipeline/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `runner.py` | Pipeline runner | Keep |
| `config.py` | Pipeline config | Keep |
| `manifests.py` | Pipeline manifests | Keep |
| `coverage.py` | Pipeline coverage | Keep |

### Data Sources (`src/fenrix_synthetic/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `collectors/base.py` | Collector ABC | Keep |
| `collectors/sec_collector.py` | SEC collector | Keep |
| `collectors/sec_archive.py` | SEC archive reader | Keep |
| `collectors/yfinance_collector.py` | yfinance collector | Keep |
| `collectors/news_collector.py` | News collector | Keep |
| `sec/client.py` | SEC EDGAR client | Keep |
| `sec/transport.py` | SEC transport (fixture/live) | Keep |
| `sec/reliability.py` | SEC reliability | Keep |
| `sec/rate_limiter.py` | SEC fair-access rate limiting | Keep |
| `sec/retry.py` | SEC retry logic | Keep |
| `ingestion/base.py` | Ingestion base | Keep |

### Identity & Masking (`src/fenrix_synthetic/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `identity/entity_registry.py` | Entity registry | Keep |
| `identity/schemas.py` | Identity schemas | Keep |
| `identity/pseudonyms.py` | Pseudonym generation | Keep |
| `identity/pseudonym_allowlist.py` | Pseudonym allowlist | Keep |
| `masking/deterministic.py` | Deterministic masker | Keep |
| `masking/sanitizer.py` | Metadata sanitizer | Keep |
| `masking/reconstruction.py` | Document reconstruction | Keep |
| `masking/overlap.py` | Overlap resolution | Keep |
| `masking/discovery.py` | Residual discovery | Keep |
| `masking/harvesting.py` | Alias harvesting | Keep |
| `masking/registry_builder.py` | Registry builder | Keep |
| `masking/pipeline.py` | Masking pipeline | Keep |
| `masking/schemas.py` | Masking schemas | Keep |

### Discovery (`src/fenrix_synthetic/discovery/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `protocol.py` | Provider protocol | Keep |
| `fake.py` | Fake provider (testing) | Keep |
| `chunking.py` | Text chunker | Keep |
| `candidates.py` | Candidate deduplication | Keep |
| `review.py` | Review queue | Keep |
| `reports.py` | Sanitized reports | Keep |
| `promotion.py` | Proposal promotion | Keep |
| `schemas.py` | Discovery schemas | Keep |
| `providers/gliner/provider.py` | GLiNER adapter | Keep (optional) |
| `providers/gliner/config.py` | GLiNER config | Keep |
| `providers/gliner/benchmark.py` | Synthetic benchmark | Keep |
| `providers/gliner/evaluation.py` | Evaluation metrics | Keep |
| `providers/gliner/mapping.py` | Label mapping | Keep |
| `providers/gliner/loader.py` | Model loader | Keep |
| `providers/gliner/validation.py` | Validation | Keep |

### Attacks (`src/fenrix_synthetic/attacks/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `text_attacks.py` | Exact/normalized/digital scans | Keep |
| `semantic_attacks.py` | Rare phrase, lexical retrieval | Keep |
| `structured_attacks.py` | Numeric fingerprinting | Keep |
| `categorical_attacks.py` | Categorical attacks | Keep |
| `exact_match.py` | Exact match scanner | Keep |
| `canonical_evidence.py` | Canonical evidence | Keep |
| `llm_attack.py` | LLM guessing attack | **Harden** — stub only |

### Release (`src/fenrix_synthetic/release/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `gate.py` | Core release gate (Phase 4R2) | Keep |
| `classroom_gate.py` | Professor bundle gate | **Harden** |
| `evidence.py` | Evidence manifest | Keep |
| `dossier.py` | Dossier generation | Keep |
| `eligibility.py` | Variant eligibility | Keep |
| `namespace_scanner.py` | Path/filename scanning | Keep |
| `pseudonym_paths.py` | Pseudonym path validation | Keep |
| `classroom_build.py` | Classroom builder | Keep |
| `s3_gate.py` | S3 release gate | Keep |

### Transforms (`src/fenrix_synthetic/transforms/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `structured.py` | S0/S1/S2 transforms | Keep |
| `feature_only.py` | S3 feature-only transform | Keep |
| `schemas.py` | Transform schemas | Keep |

### Professor Bundle (`src/fenrix_synthetic/professor/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `orchestrator.py` | 19-stage orchestrator | **Refactor** — too monolithic |
| `stages.py` | Stage registry, enums | Keep |
| `evidence.py` | Evidence objects | Keep |
| `sec_providers.py` | SEC provider ABC/fixture | Keep |
| `providers.py` | Legacy mock providers | **Deprecate** — replaced by dedicated modules |
| `entity_providers.py` | Entity providers | Keep |
| `review_providers.py` | Review providers (new) | Keep |
| `metrics_providers.py` | Metrics providers (new) | Keep |

### Pilot (`src/fenrix_synthetic/pilot/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `orchestrator.py` | Pilot orchestrator | Keep |
| `manifest.py` | Run manifest | Keep |
| `schemas.py` | Pilot schemas | Keep |

### Reanonymize (`src/fenrix_synthetic/reanonymize/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `orchestrator.py` | Re-anonymization orchestrator | Keep |
| `limits.py` | Form/item limits | Keep |
| `atlas_builder.py` | Identity atlas builder | Keep |

### Anonymization (`src/fenrix_synthetic/anonymization/`)
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `structured_anonymizer.py` | Structured anonymization | Keep |
| `text_anonymizer.py` | Text anonymization | Keep |
| `registry_load.py` | Registry loading | Keep |
| `atlas_builder.py` | Atlas builder | Keep |
| `residual_scanner.py` | Residual scanner | Keep |
| `news_surrogate_generator.py` | News surrogates | Keep |
| `classroom_numeric_writer.py` | Numeric writer | Keep |

### Other
| Module | Purpose | V3 Status |
|--------|---------|-----------|
| `providers/nvidia_risk.py` | NVIDIA risk provider | Keep (optional) |
| `providers/nvidia_scrub.py` | NVIDIA scrub provider | Keep (optional) |
| `providers/nvidia_review.py` | NVIDIA review adapter | Keep (optional) |
| `providers/nvidia_client.py` | NVIDIA client | Keep (optional) |
| `submission_package.py` | ZIP packaging | Keep |
| `submission_fast.py` | Fast submission | Keep |
| `submission_nvidia.py` | NVIDIA submission | Keep |
| `submission_quality.py` | Quality scrubbing | Keep |
| `submission_sources.py` | Source material | Keep |
| `atlas/compiler.py` | Atlas compiler | Keep |
| `atlas/schemas.py` | Atlas schemas | Keep |
| `atlas/validation.py` | Atlas validation | Keep |
| `boundary/private_root.py` | Private root boundary | Keep |
| `extraction/converter.py` | HTML→text converter | Keep |
| `extraction/segmenter.py` | Filing segmenter | Keep |
| `evaluation/backtest.py` | Backtesting | Keep |
| `utility/structured.py` | Structured utility | Keep |
| `utility/unstructured.py` | Unstructured utility | Keep |
| `utility/feature_only.py` | Feature-only utility | Keep |
| `reporting/coverage.py` | Coverage reporting | Keep |

## 4. Test Coverage

### Test Count
- **Unit tests**: ~55 files in `tests/unit/`
- **Integration tests**: ~10 files in `tests/integration/`
- **Total tests**: ~1100 (pytest reports ~1084+ passed, ~5 skipped)
- **Coverage**: Comprehensive for pipeline, masking, identity, attacks. Gaps in numeric transformation and trajectory morphing.

### Key Test Files
| Test | What it Covers |
|------|---------------|
| `test_public_identity_leak_gate.py` | No real identifiers in tracked files |
| `test_stage_registry_required_stages.py` | All 19 stages required |
| `test_professor_ready_requires_all_mandatory_stages.py` | Readiness semantics |
| `test_private_public_evidence_boundary.py` | Public/private separation |
| `test_classroom_gate_seeded_failures.py` | Gate failure modes |
| `test_professor_bundle_fixture_build.py` | End-to-end fixture build |
| `test_deterministic_masking.py` | Deterministic masking |
| `test_exact_residual.py` | Residual scanning |
| `test_release_gate_privacy.py` | Release gate privacy |
| `test_gliner_provider.py` | GLiNER provider |
| `test_nvidia_bounded.py` | NVIDIA bounded review |
| `test_phase2b_gate_adversarial_review.py` | Adversarial review gate (new) |
| `test_phase2b_gate_metrics_privacy.py` | Metrics privacy gate (new) |

## 5. Current Pipeline Flow

```
config YAML → ProfessorBundleOrchestrator
  ├─ Stage 1:  SOURCE_INGESTION (SEC provider)
  ├─ Stage 2:  SEC_PARSE (section/tables extraction)
  ├─ Stage 3:  SECTION_EXTRACT (extract text)
  ├─ Stage 4:  ENTITY_DETECT_GLINER (GLiNER entity detection)
  ├─ Stage 5:  ENTITY_DETECT_RULES (regex rules)
  ├─ Stage 6:  ENTITY_RESOLVE (deduplication)
  ├─ Stage 7:  DEIDENTIFY (replacement mapping → sanitized text)
  ├─ Stage 8:  PRIVATE_EVIDENCE_BUILD (private evidence graph)
  ├─ Stage 9:  SYNTHETIC_PROFILE_BUILD (synthetic company profile)
  ├─ Stage 10: FILING_RECONSTRUCT (sanitized markdown sections)
  ├─ Stage 11: METRIC_SYNTHESIS (synthetic financial metrics)
  ├─ Stage 12: METRIC_EVALUATION (quality/privacy/schema reports)
  ├─ Stage 13: NEWS_RECONSTRUCT (synthetic news surrogates)
  ├─ Stage 14: CROSSLINK_BUILD (filing↔metric cross-links)
  ├─ Stage 15: PEDAGOGY_BUILD (exercises, guides, rubric)
  ├─ Stage 16: RAG_INDEX_BUILD (retrieval index)
  ├─ Stage 17: ADVERSARIAL_QA (exact scan + review provider)
  ├─ Stage 18: RELEASE_GATE (classroom gate evaluation)
  └─ Stage 19: ZIP_EXPORT (public-only ZIP bundle)
```

### Output Tree
```
bundle_root/
  public/
    README.md, CLASSROOM_GUIDE.md, EXERCISES.md, ...
    anonymized/COMPANY_001/
      sec/ (item_1.md, item_1a.md, item_7.md, item_8.md, ...)
      news/ (news_001.md, ...)
      metrics/ (daily_prices.json, returns.json, ...)
      crosslinks.json, LEARNING_GUIDE.md
  private/evidence/evidence_graph.json
  qa/
    stage_registry.json, entity_audit_report.json,
    metrics_quality_report.json, metrics_privacy_report.json,
    adversarial_qa_report.json, adversarial_review_report.json,
    classroom_gate_report.json, ...
  exports/anonymized_bundle.zip
  checksums.sha256, run_summary.json, artifact_inventory.csv
```

## 6. Direct Leak Risks

### Current Blockers
1. **Identity leakage in public artifacts**: Scanned by `_scan_for_identity_leaks()` in classroom_gate.py — catches canary entities only (CHC, 0000999999, Eleanor Testperson, canary-test.invalid). Does not scan for real source-company identifiers because real source data is not in the public repo.
2. **Raw SEC HTML/iXBRL in public ZIP**: Prevented by `zip_exclude_prefixes` (private/, originals/, maps/, .env, smoke_excerpts). HTML and XML files are NOT explicitly excluded.
3. **XBRL metadata survival**: The HTML extraction pipeline strips boilerplate but XBRL embedded metadata (namespace declarations, CIK references) could survive in extracted text if present in source.
4. **Fake name leakage**: Synthetic company profiles use generic names ("Company 001", "TKR_001") — safe for fixture mode. Production builds need peer-archetype-based name generation.
5. **NVIDIA review stubs**: In fixture mode, adversarial review always returns PASS with no findings. In production mode, provider failures block release.

### Risk Assessment Matrix
| Risk | Current State | Severity |
|------|--------------|----------|
| Raw SEC HTML in public ZIP | Not explicitly excluded | **High** |
| XBRL namespaces in extracted text | No explicit stripping | **High** |
| Exact financial values | Not transformed (fixture metrics) | **Medium** |
| Source ticker in filenames | Blocked by classroom_gate | **Low** |
| Executive names in text | Handled by deterministic masker | **Low** |
| CIK in extraction output | Blocked by identity leak scan | **Low** |
| Original filenames | Blocked by pseudonym path validation | **Low** |

## 7. Fingerprint Leak Risks

| Risk | Current State | V2 Observation |
|------|--------------|---------------|
| Sector labels too narrow | "diversified financial services" | V2: sector labels enabled peer-category guesses |
| Segment structure fingerprint | Not addressed | V2: distinctive segment structure was a primary leak |
| Exact revenue scale | Fixture uses randomized but non-generalized data | V2: exact revenues enabled identification |
| Product/segment naming | Not addressed in current pipeline | V2: naming enabled peer-category confusion |
| Fiscal year-end | Not transformed | Potential fingerprint |
| Price trajectory | Not implemented | V2: synthetic index trajectories leaked |
| Synthetic names too literal | "Company 001" is safe but not meaningful | V2: suggestive fake names contributed to confusion |

## 8. Modules to KEEP

All existing modules in `src/fenrix_synthetic/` — the codebase is well-structured and follows the project contract. No modules need deletion.

## 9. Modules to MODIFY

| Module | Change Needed |
|--------|---------------|
| `release/classroom_gate.py` | Add explicit `.html` and `.xml` exclusion in ZIP validation; add production-mode provider checks for critical stages |
| `professor/orchestrator.py` | Split monolithic `run()` into smaller, testable stage methods; add peer-archetype selection; add numeric transformation |
| `cli.py` | Split by domain (sources, anonymize, qa, package) for maintainability |
| `anonymization/` | Add peer-archetype module, numeric transform, trajectory morph, text generalization, filing reconstruction, news reconstruction |
| `qa/` (new) | Add direct identifier scanner, metadata scanner, LLM blind guess, peer basket attack, trajectory attack |

## 10. Modules to ADD (V3)

| Module | Purpose | Phase |
|--------|---------|-------|
| `qa/direct_identifier_scan.py` | Scan all public output for forbidden patterns | Phase 2 |
| `qa/metadata_scan.py` | Scan for XBRL, HTML, CIK metadata survival | Phase 2 |
| `qa/peer_basket_attack.py` | Test peer-candidate diversity | Phase 9 |
| `qa/llm_blind_guess.py` | Provider-neutral LLM guessing with offline stub | Phase 9 |
| `qa/trajectory_attack.py` | Test trajectory uniqueness | Phase 6 |
| `anonymize/peer_archetype.py` | Peer-based archetype selection | Phase 4 |
| `anonymize/numeric_transform.py` | Financial value perturbation | Phase 5 |
| `anonymize/trajectory_morph.py` | Price/return path transformation | Phase 6 |
| `anonymize/text_generalizer.py` | SEC text generalization | Phase 7 |
| `anonymize/filing_reconstructor.py` | Markdown-only filing reconstruction | Phase 7 |
| `anonymize/news_reconstructor.py` | Synthetic news briefs | Phase 8 |
| `package/student_bundle.py` | Safe ZIP packager with allowlist | Phase 3 |
| `package/release_manifest.py` | Release manifest schema | Phase 3 |
| `tests/fixtures/professor_review/identification_cases.yaml` | V2 failure mode encoding | Phase 1 |

## 11. Proposed V3 Implementation Phases

### Phase 0 — Baseline Audit (CURRENT)
- [x] Git state, branch, SHA
- [x] Command inventory
- [x] Source module inventory
- [x] Test coverage inventory
- [x] Current pipeline flow
- [x] Direct leak risks
- [x] Fingerprint leak risks
- [x] Module disposition (keep/modify/add)
- [x] This document

### Phase 1 — Professor Review Regression Fixtures (NEXT)
- [ ] Create `tests/fixtures/professor_review/identification_cases.yaml`
- [ ] Encode V2 leak classes (22 classes)
- [ ] Add fixture loading tests
- [ ] Add PEP exact-hit regression test

### Phase 2 — Direct Identifier & Metadata Release Gate
- [ ] Implement `qa/direct_identifier_scan.py`
- [ ] Implement `qa/metadata_scan.py`
- [ ] Harden `release/classroom_gate.py` with new scanners
- [ ] Block HTML/XML in public ZIP
- [ ] Block XBRL namespaces in extracted text

### Phase 3 — Public/Private Artifact Boundary
- [ ] Implement `package/student_bundle.py` with allowlist
- [ ] Implement `ReleaseManifest` schema
- [ ] Enforce no private artifacts in release ZIP

### Phase 4 — Peer-Archetype Anonymization
- [ ] Implement `anonymize/peer_archetype.py`
- [ ] Minimum 5 peer candidates
- [ ] Private peer list, public broad archetype only

### Phase 5 — Numeric Transformation
- [ ] Implement `anonymize/numeric_transform.py`
- [ ] Deterministic seeded perturbation
- [ ] No exact source values survive
- [ ] Accounting sanity validation

### Phase 6 — Trajectory Morphing
- [ ] Implement `anonymize/trajectory_morph.py`
- [ ] Implement `qa/trajectory_attack.py`
- [ ] Source not top-1 or top-3 in trajectory ranking

### Phase 7 — Text Generalization & Filing Reconstruction
- [ ] Implement `anonymize/text_generalizer.py`
- [ ] Implement `anonymize/filing_reconstructor.py`
- [ ] Markdown-only output; no raw HTML/iXBRL
- [ ] Remove/generalize all direct identifiers

### Phase 8 — Synthetic News Reconstruction
- [ ] Implement `anonymize/news_reconstructor.py`
- [ ] No original headlines, dates, deal values, counterparties
- [ ] Event categories: demand, margin, regulatory, competition, etc.

### Phase 9 — LLM/Human Adversarial QA
- [ ] Implement `qa/llm_blind_guess.py` with offline stub
- [ ] Implement `qa/peer_basket_attack.py`
- [ ] Support local stubs for CI, optional API for live tests

### Phase 10 — Release Packaging
- [ ] `build-professor-alpha-v3` CLI command
- [ ] Full release bundle with README, QUICKSTART, DATA_DICTIONARY
- [ ] Release manifest with SHA, seed, config hash, artifact count
- [ ] ZIP contains only professor-facing files

## 12. Quality Gate Baseline

### Current Gate Status
| Check | Status |
|-------|--------|
| `ruff format --check` | 208 files formatted |
| `ruff check` | All checks passed |
| `mypy src/fenrix_synthetic` | No issues in 144 source files |
| `pytest --disable-socket` | ~1084 passed, ~5 skipped |

### Non-Negotiable Quality Gates (V3)
- ruff format: clean
- ruff check: clean
- mypy: 0 errors
- pytest --disable-socket: all tests pass
- No live network calls in unit tests
- No real source company identities in tracked files
- No `.html` or `.xml` in public ZIP
- Release gate fails closed

## 13. Key Decisions for V3

1. **One release gate** (not multiple): Consolidate classroom_gate, Phase 4R2 gate, and S3 gate into one unified `evaluate_release_gate()`.
2. **No formal DP claims**: The project targets practical privacy metrics (k-peer >= 5, source not top-3, confidence low) rather than epsilon/delta guarantees.
3. **Provider neutrality**: All model-based steps use protocols with offline stubs for CI.
4. **Markdown-only public output**: No HTML, XML, iXBRL in release. Transform to normalized markdown.
5. **Peer-archetype over one-to-one masking**: Stop producing issuer-shaped replicas; use peer archetypes for generalization.
