# Architecture Decisions Log

This document records significant architectural decisions made during implementation.

---

## Decision 001: Package Layout and Build System

**Date**: 2026-01-18
**Status**: Accepted

**Context**: Need to establish Python package structure per AGENTS.md §53-68.

**Decision**: Use `src/` layout with `pyproject.toml` and `setuptools`.

**Rationale**:
- `src/` layout prevents import issues during development
- `pyproject.toml` is modern standard (PEP 621)
- `setuptools` is stable and well-supported

**Alternatives Considered**:
- Flat layout (rejected: import issues)
- `flit` or `poetry` (rejected: extra dependencies, `setuptools` sufficient)

---

## Decision 002: Configuration Management

**Date**: 2026-01-18
**Status**: Accepted

**Context**: Need to load company and campaign configuration.

**Decision**: Use `pydantic-settings` for environment-based settings, YAML files for company/campaign config.

**Rationale**:
- `pydantic-settings` provides type-safe env var loading
- YAML is human-readable for complex nested config
- Company mapping kept in separate private file (`configs/company.yaml`)

**Security**: Private mappings and secrets never committed (`.gitignore`).

---

## Decision 003: Deterministic JSON Serialization

**Date**: 2026-01-18
**Status**: Accepted

**Context**: ARCHITECTURE.md §23.4 requires canonical JSON serialization for hashing.

**Decision**: Use `orjson` with `OPT_SORT_KEYS` for all JSON serialization.

**Rationale**:
- `orjson` is faster than stdlib `json`
- `OPT_SORT_KEYS` guarantees deterministic key ordering
- Used for artifact hashes, checkpoint serialization, manifests

**Alternatives Considered**:
- `json.dumps(..., sort_keys=True)` (rejected: slower, not truly canonical for all types)
- `msgpack` (rejected: not human-readable, extra dependency)

---

## Decision 004: SHA-256 for All Hashing

**Date**: 2026-01-18
**Status**: Accepted

**Context**: ARCHITECTURE.md §62 requires SHA-256 for source and generated artifacts.

**Decision**: Use Python's `hashlib.sha256` for all hashing (files, strings, objects).

**Rationale**:
- FIPS-compliant, widely available
- `hash_object()` uses canonical JSON serialization for determinism
- Streaming for large files (8KB chunks)

---

## Decision 005: Atomic Writes via Temp File + Rename

**Date**: 2026-01-18
**Status**: Accepted

**Context**: ARCHITECTURE.md §61 requires atomic artifact writes.

**Decision**: Write to temp file in same directory, `fsync`, then `os.replace()` (atomic on POSIX/Windows).

**Rationale**:
- `os.replace()` is atomic on POSIX and Windows (since 3.3)
- Temp file in same directory ensures same filesystem
- `fsync()` ensures data on disk before rename

**Applies to**: JSON, JSONL, Parquet, binary writes.

---

## Decision 006: Checkpoint Validation Strategy

**Date**: 2026-01-18
**Status**: Accepted

**Context**: ARCHITECTURE.md §9 requires resume behavior with hash validation.

**Decision**: Store checkpoint with input hash, config hash, output artifact hashes, version. Validate all on resume.

**Invalidation Triggers**:
1. No checkpoint exists
2. Checkpoint status != COMPLETED
3. Pipeline version changed
4. Input hash mismatch
5. Config hash mismatch
6. Output artifact missing
7. Output artifact hash mismatch

**Rationale**: Comprehensive validation prevents silent corruption.

---

## Decision 007: Structured Logging with Secret Redaction

**Date**: 2026-01-18
**Status**: Accepted

**Context**: AGENTS.md §63-64 requires structured JSON logs with no secret values.

**Decision**: Use `python-json-logger` with custom `RedactingFilter`.

**Redaction Pattern**: Case-insensitive regex matching `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `AUTH`, `CREDENTIAL` in key names.

**Applies to**: Log message, args, and extra fields.

**Rationale**: Defense-in-depth - redacts at logging layer regardless of caller.

---

## Decision 008: CLI Framework - Click

**Date**: 2026-01-18
**Status**: Accepted

**Context**: Need CLI entry point `fenrix-synth`.

**Decision**: Use `click` for CLI.

**Rationale**:
- Mature, stable, minimal dependencies
- Good integration with type hints
- Supports subcommands naturally (`ingest`, `extract`, `campaign`)

**Alternatives Considered**:
- `typer` (rejected: requires `click` anyway, adds abstraction)
- `argparse` (rejected: verbose, no subcommand help)

---

## Decision 009: No Live Network in M0

**Date**: 2026-01-18
**Status**: Accepted

**Context**: AGENTS.md §32 and user requirements prohibit live network calls in tests.

**Decision**: All M0 code works offline. SEC ingestion adapter interface defined but implementation deferred to M1 with fixture support.

**Rationale**: Enables deterministic CI/CD, no external dependencies.

---

## Decision 010: Test Fixtures Committed

**Date**: 2026-01-18
**Status**: Accepted

**Context**: Need offline test fixtures for HTML extraction.

**Decision**: Commit sanitized/synthetic test fixtures to `tests/fixtures/`.

**Rationale**:
- Enables fully offline test suite
- Fixtures are synthetic, no real PII
- Hashes recorded for validation

---

## Decision 011: Defer Source Reuse Investigation

**Date**: 2026-01-18
**Status**: Accepted

**Context**: AGENTS.md §39-49 requires source provenance tracking before reuse.

**Decision**: Create `SOURCE_PROVENANCE.md` template but defer actual investigation until M1 when specific utilities are needed.

**Rationale**: Avoid premature optimization; reuse when concrete need identified.

---

## Decision 012: Deterministic Masking as Tier 0

**Date**: 2026-06-18
**Status**: Accepted

**Context**: Phase 2 requires private identity registry, alias matching, and deterministic replacement.

**Decision**: Implement deterministic masking as pure regex-based Tier 0 with no model dependencies. All matching operates on original-text offsets, not semantic meaning.

**Rationale**: Deterministic matching avoids false positives from heuristic inference, produces identical results across runs, and requires no model infrastructure. Model-based entity discovery (Tier 1) is deferred to Phase 3.

---

## Decision 013: Pseudonym Format

**Date**: 2026-06-18
**Status**: Accepted

**Context**: Need stable, non-revealing pseudonyms that do not encode real names, geography, scale, or industry.

**Decision**: Use `{EntityType} {counter:03d}` format (e.g., "Company 001", "Executive 001"). Counters are per-type, stable across a campaign.

**Rationale**: Format reveals nothing about the source value. No hash of the private value is exposed. Counter-based assignment is deterministic and reproducible. Different entity types have independent counters.

---

## Decision 014: Plain Hash Prohibition

**Date**: 2026-06-18
**Status**: Accepted

**Context**: An unsalted hash of a private value could be reversed if the value appears in a known set.

**Decision**: Never expose unsalted hashes of private values outside the private boundary. The `matched_text_hash` in `MatchResult` is a SHA-256 of the *matched text from the document* (the span content), not of the private alias or canonical value. Private values remain inside the private audit only.

**Rationale**: Hash reversal attacks on short private values (tickers, names) are feasible with a rainbow table. Even salted, the hash leaks that the value was present.

---

## Decision 015: Report Separation

**Date**: 2026-06-18
**Status**: Accepted

**Context**: ARCHITECTURE.md §7 requires private and sanitized reports to be strictly separated.

**Decision**: Produce two outputs per masking operation: (1) a private `MaskingAudit` containing original values, spans, and conflict decisions; (2) a `MaskingSummary` containing only hashes, counts, and status. Never include private values, aliases, source URLs, or absolute paths in the sanitized summary.

**Rationale**: Private data stays in silver-layer gitignored paths. The sanitized summary can be included in release reports without exposing the source identity.

---

## Decision 016: Independent Residual Scanning

**Date**: 2026-06-18
**Status**: Accepted

**Context**: ARCHITECTURE.md §15 describes re-identification attacks. The exact-match residual scanner must be independent of the masking pipeline.

**Decision**: `ExactResidualScanner` operates on the masked output text. It does not use the masker's accepted span list. It generates its own patterns for every active canonical value and alias. A blocking match causes the deterministic stage to fail.

**Rationale**: A scanner that relies on the masking pipeline's span list would miss leaks from metadata, filenames, or overlooked spans. Independence ensures detection of masking failures.

---

## Decision 017: No External Dependencies for Phase 2

**Date**: 2026-06-18
**Status**: Accepted

**Context**: Phase 2 may benefit from fuzzy matching, NER, or ML-based entity extraction.

**Decision**: Use only the standard library and existing Pydantic/Click dependencies. No Presidio, spaCy, GLiNER, transformers, PyTorch, RapidFuzz, FAISS, or vector database.

**Rationale**: Adding model dependencies would increase complexity, slow test execution, and introduce nondeterminism. Tier 0 deterministic operations should perform the majority of pipeline work per architecture §13.

---

## Decision 018: Synthetic C001 Status

**Date**: 2026-06-18
**Status**: Accepted

**Context**: C001 maps to HBAN in the private company config. No reviewed HBAN identity registry exists.

**Decision**: The C001 implementation is demonstrated with synthetic canary values only. Actual C001 deterministic masking requires a reviewed private registry populated with real HBAN entities and aliases. Do not claim HBAN masking is complete.

**Rationale**: Claiming masking without a reviewed registry would be misleading. The synthetic demonstration proves the pipeline works; the actual masking depends on registry population.