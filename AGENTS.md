# FENRIX Synthetic Data Worker

## Objective

Build a reproducible company-level financial-data masking and
re-identification testing system.

The current vertical slice is HBAN.

## Current Milestone

Completed:

1. Repository and Python package scaffold
2. Source-reuse inventory
3. Source provenance records
4. Shared artifact and manifest schemas
5. HBAN SEC filing discovery, download, extraction, and checkpointing
6. Tests and an offline fixture-based demonstration

Phase 2:

7. Private identity registry and alias management
8. Deterministic matching and pseudonym replacement
9. Overlap resolution and conflict handling
10. Metadata sanitization and report separation
11. Independent exact residual scanning
12. Canary and mutation testing fixtures

Phase 3A:

13. Residual entity discovery and coverage reporting

Phase 3B Core (in progress):

14. Reviewed provider-neutral entity discovery
15. Fake provider for deterministic offline testing
16. Review queue with accept/reject/defer/duplicate
17. Proposal generation, validation, and promotion
18. Deterministic deduplication with disagreement tracking
19. Sanitized candidate summaries with opaque IDs
20. Privacy regression: no plain hashes or private values in sanitized outputs
21. Promotion → remasking → rescanning workflow

Phase 3C (in progress):

22. Optional local GLiNER adapter behind `local-ner` extra
23. Provider CLI: `providers list|health|prepare`, `discover-model`
24. Synthetic-only benchmark with hashed versioning
25. Bounded threshold sweep (0.30–0.70)
26. Decision records 022–029 covering explicit download, synthetic smoke, review gating, reproducibility, provisional threshold, CI exclusion of model execution

Deferred to Phase 3C:

- GLiNER adapter
- NVIDIA adapter
- Optional model dependency groups
- Explicit live smoke commands
- Provider-specific live tests

## Non-Negotiable Scope

Do not:

- Modify the deployed FENRIX Render application
- Modify PPE, Bloomberg × Zion, Hermes, or Finfluencer Alpha
- Build another backtester
- Build another dashboard
- Introduce LangGraph, CrewAI, Kubernetes, a vector database, or cloud infrastructure
- Add live API calls to unit tests
- Commit raw filings, secrets, Bloomberg content, generated artifacts, or private entity maps
- Claim an integration is complete without executable evidence
- Push, merge, publish, deploy, or delete files without explicit approval

## Reuse Policy

Existing repositories are reference sources and must be treated as read-only.

Before adapting code:

1. Record source repository
2. Record source path
3. Record source commit
4. Explain why reuse is preferable
5. Record modifications
6. Preserve applicable license and attribution
7. Add focused regression tests

Do not copy an entire subsystem when a small utility is sufficient.

## Implementation Standards

- Python 3.12+
- `src/` package layout
- `pyproject.toml`
- Type annotations on public interfaces
- Pydantic models for persisted schemas
- Pathlib rather than raw path strings
- Atomic artifact writes
- SHA-256 hashes for source and generated artifacts
- Structured JSON logs
- No secret values in logs
- Deterministic offline tests
- Network integrations behind interfaces
- Dependency injection for providers and storage
- Small, reviewable modules

## Data Boundaries

- `raw/`: source files; private and immutable
- `bronze/`: extracted content and metadata
- `silver/`: normalized content and private identity data
- `gold/`: masked release candidates and attack reports

Generated data directories must be ignored by Git.

## CI Preservation

GitHub Actions CI (`.github/workflows/ci.yml`) runs on PRs targeting `main`.

Future agents must:

- Preserve CI in working order
- Run the same checks locally before pushing: `ruff format --check`, `ruff check`, `mypy src/fenrix_synthetic`, `pytest`
- Not bypass or weaken required checks
- Report CI status before merging

## Required Verification

Before declaring work complete:

1. Run formatting and linting (`ruff format --check`, `ruff check`)
2. Run type checking (`mypy src/fenrix_synthetic`)
3. Run the complete test suite (`pytest`)
4. Run the offline HBAN fixture demonstration
5. Inspect `git diff`
6. Report exact commands and outcomes
7. List anything mocked, deferred, or unverified

Passing tests are required but are not sufficient evidence of correctness.