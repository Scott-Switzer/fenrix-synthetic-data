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

Phase 2 in progress:

7. Private identity registry and alias management
8. Deterministic matching and pseudonym replacement
9. Overlap resolution and conflict handling
10. Metadata sanitization and report separation
11. Independent exact residual scanning
12. Canary and mutation testing fixtures

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

## Required Verification

Before declaring work complete:

1. Run formatting and linting
2. Run type checking
3. Run the complete test suite
4. Run the offline HBAN fixture demonstration
5. Inspect `git diff`
6. Report exact commands and outcomes
7. List anything mocked, deferred, or unverified

Passing tests are required but are not sufficient evidence of correctness.