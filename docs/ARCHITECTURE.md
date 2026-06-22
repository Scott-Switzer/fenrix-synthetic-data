# FENRIX Synthetic Data Architecture

## 1. Purpose

FENRIX Synthetic Data is a reproducible pipeline for transforming real company-level financial information into research packages that cannot be reliably linked back to the source company.

The system must support three goals:

1. Preserve enough financial and textual structure for meaningful analysis.
2. Remove or transform information that reveals the source company.
3. Test every generated package against automated re-identification attacks before release.

The system must not treat successful masking as a subjective visual judgment. Each masking configuration must be versioned, tested, and recorded as a reproducible experiment.

---

## 2. Core Principle

Do not rebuild infrastructure that already exists.

The new repository should contain only the components that are genuinely specific to synthetic-data masking:

1. Company identity registry
2. Deterministic masking rules
3. Document reconstruction
4. Re-identification attack system
5. Rules-based release gate

Existing systems should continue to provide ingestion, structured data, model routing, audit records, backtesting, dashboards, and operational reporting.

---

## 3. Current Vertical Slice

The first supported company is:

```text
Internal company ID: CANARY
Private source identity: CHC (fictional canary; real mappings are gitignored)
```

The initial implementation milestone is limited to:

1. Repository and Python package foundation
2. Source-code reuse inventory
3. Source provenance records
4. Typed artifact and manifest schemas
5. One canary SEC filing discovery and extraction pipeline
6. Deterministic checkpoints and resume behavior
7. Offline fixture-based tests

Masking and re-identification attacks are later milestones.

---

## 4. System Context

```text
Project Portfolio Engine + Bloomberg × Zion + SEC Sources
                              |
                              v
                 FENRIX Synthetic Data Worker
                              |
                 +------------+-------------+
                 |                          |
                 v                          v
          Masking Pipeline          Re-ID Attack Lab
                 |                          |
                 +------------+-------------+
                              |
                              v
                 Versioned Research Artifacts
                              |
                 +------------+-------------+
                 |                          |
                 v                          v
          FENRIX Review UI           PPE Backtester
          Later integration          Later integration
```

The synthetic-data worker is a standalone, batch-oriented Python package.

It is not:

* A dashboard
* A portfolio engine
* An authentication system
* An agent framework
* A permanent data server
* A replacement for PPE, Zion, Hermes, or the current FENRIX application

---

## 5. System Responsibilities

### 5.1 Control Plane — Hermes

Hermes may later:

* Launch jobs
* Route model providers
* Track usage and cost
* Report status to Slack
* Write decisions and handoffs to Obsidian

Hermes must not parse or mask filings directly.

Hermes integration is not part of the initial vertical slice.

### 5.2 Data Plane — PPE and Bloomberg × Zion

Existing systems provide:

* SEC filing ingestion
* Security master and company aliases
* Corporate actions
* Structured financial data
* Source manifests
* Checksums and audit records
* Experiment registration
* Historical market data
* Filing normalization

The new worker should adapt only the smallest reusable utilities required for its current milestone.

### 5.3 Compute Plane — Local and Colab Workers

Batch processing may run:

* Locally during development
* On a Colab T4 for GPU-assisted processing
* Through hosted NVIDIA endpoints for difficult cases

The core package must not depend on notebooks.

Notebooks may invoke package commands but must contain no business logic.

### 5.4 Review Plane — Existing FENRIX Application

The existing Render application may later display:

* Package coverage
* Unresolved entities
* Attack results
* Release decisions
* Provider cost
* Pipeline failures

The deployed application must not be modified during the initial worker milestone.

### 5.5 Storage Plane

The initial storage system is file-based:

* Local filesystem
* Canary Search Drive for private archives and checkpoints
* Parquet or JSONL for structured records
* DuckDB for local analytical queries

No network database is required for the initial milestone.

---

## 6. Repository Structure

```text
fenrix-synthetic-data/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── opencode.jsonc
├── configs/
│   ├── campaign.yaml
│   ├── providers.yaml
│   └── release_policy.yaml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DECISIONS.md
│   └── SOURCE_PROVENANCE.md
├── src/
│   └── fenrix_synthetic/
│       ├── campaign/
│       │   ├── spec.py
│       │   ├── runner.py
│       │   └── stages.py
│       ├── ingestion/
│       │   ├── base.py
│       │   ├── sec.py
│       │   ├── zion.py
│       │   └── market_data.py
│       ├── identity/
│       │   ├── security_master.py
│       │   ├── aliases.py
│       │   ├── entity_registry.py
│       │   └── pseudonyms.py
│       ├── masking/
│       │   ├── deterministic.py
│       │   ├── ner.py
│       │   ├── llm_review.py
│       │   └── reconstruction.py
│       ├── attacks/
│       │   ├── exact_match.py
│       │   ├── lexical.py
│       │   ├── semantic.py
│       │   ├── timeseries.py
│       │   └── llm_guess.py
│       ├── providers/
│       │   ├── base.py
│       │   ├── local.py
│       │   └── nvidia_nim.py
│       ├── registry/
│       │   ├── experiments.py
│       │   ├── manifests.py
│       │   └── artifacts.py
│       ├── reporting/
│       │   ├── coverage.py
│       │   └── release_report.py
│       ├── storage/
│       │   ├── atomic.py
│       │   ├── hashing.py
│       │   └── checkpoints.py
│       └── cli.py
├── tests/
│   ├── fixtures/
│   ├── unit/
│   └── integration/
└── notebooks/
    ├── 00_bootstrap_colab.ipynb
    ├── 01_run_campaign.ipynb
    └── 02_attack_dashboard.ipynb
```

Directories should be created only when required by the active milestone. Empty speculative modules should not be added merely to match this target tree.

---

## 7. Data Layers

```text
data/
├── raw/
├── bronze/
├── silver/
└── gold/
```

### Raw

Contains immutable source documents and original exports.

Examples:

* SEC HTML
* SEC filing metadata
* Original PDFs
* Restricted source exports

Raw data is private and must never be modified in place.

### Bronze

Contains mechanically extracted material.

Examples:

* Extracted text
* Extracted tables
* Transport metadata
* Document sections
* Parsing diagnostics

Bronze data preserves source lineage and should not contain interpretive masking decisions.

### Silver

Contains normalized documents and private identity information.

Examples:

* Normalized text
* Entity registries
* Alias maps
* Private pseudonym mappings
* Document-to-company relationships

Silver data is private and must never be released.

### Gold

Contains potential research releases.

Examples:

* Masked documents
* Masked structured datasets
* Attack reports
* Coverage reports
* Release decisions

A gold artifact is not automatically safe. It must pass the release policy.

---

## 8. Artifact Lineage

Every persisted artifact must include enough information to reproduce its origin.

Required fields include:

```text
artifact_id
artifact_type
company_id
source_artifact_ids
source_manifest_hash
configuration_hash
content_hash
created_at
pipeline_version
stage
status
```

Every source document must have a SHA-256 hash.

Every generated artifact must record:

* Its source document
* The configuration used
* The code or package version
* The stage that produced it
* Its own content hash

Writes must be atomic. An interrupted write must not leave a valid-looking partial artifact.

---

## 9. Pipeline Stages

The target campaign state machine is:

```text
inventory
    |
    v
ingest
    |
    v
extract
    |
    v
build_identity_registry
    |
    v
deterministic_mask
    |
    v
entity_scan
    |
    v
targeted_model_review
    |
    v
reconstruct
    |
    v
exact_leak_attack
    |
    v
lexical_attack
    |
    v
semantic_attack
    |
    v
llm_guess_attack
    |
    v
release_report
    |
    v
approved or rejected
```

Each stage must have explicit states:

```text
pending
running
completed
failed
blocked
skipped
```

A completed stage may be reused only when:

* Its input hashes still match
* Its configuration hash still matches
* Its output artifacts exist
* Its output artifact hashes validate

Otherwise, the stage must run again.

---

## 10. Initial Canary Extraction Flow

The first implemented flow is intentionally smaller:

```text
CANARY company configuration
        |
        v
SEC filing manifest
        |
        v
Download or offline fixture load
        |
        v
Source hash verification
        |
        v
HTML extraction
        |
        v
Normalized text and section metadata
        |
        v
Raw and bronze manifests
        |
        v
Checkpoint validation
```

The initial flow must support offline fixtures so the test suite does not depend on SEC availability or internet access.

Live SEC behavior must sit behind an adapter interface.

---

## 11. Source Reuse Policy

The following repositories may be inspected as read-only references:

* Project Portfolio Engine
* Bloomberg × Zion
* Finfluencer Alpha
* Hermes Obsidian Orchestrator
* Existing NVIDIA aggregation code or notebooks

Before adapting code, record:

```text
Source repository
Source path
Source commit
Original responsibility
Reason for reuse
Dependencies
Modifications
Applicable license or attribution
Tests added in the new repository
```

Do not:

* Copy entire applications
* Use Git submodules
* Modify source repositories during inspection
* Claim reuse without recording the source commit
* Copy code whose license or ownership is unclear

Prefer adapting a small utility over importing a full subsystem.

---

## 12. Reuse Map

### Project Portfolio Engine

Potentially reusable:

* Checksums
* Atomic writes
* Retry behavior
* Failure classification
* Source manifests
* Experiment registry
* Audit hashes
* Validation reports

Do not import:

* Full research campaign
* Portfolio construction
* Dashboard
* Approval queue
* Backtest implementation during the masking milestone

### Bloomberg × Zion

Potentially reusable:

* SEC filing discovery
* SEC download logic
* HTML-to-text parsing
* Company facts
* Filing schemas
* Validation hooks

The existing company corpus may later become the reference corpus for re-identification attacks.

### Finfluencer Alpha

Potentially reusable:

* Entity alias extraction
* Queue state transitions
* Retry logic
* Budget controls
* Provider abstraction
* Provenance records
* Duplicate detection

### Hermes

Potentially reusable later:

* Provider routing
* Structured logs
* Task IDs
* Retry policies
* Usage tracking
* Slack reporting
* Obsidian decision records

Hermes integration is not required for the initial extraction milestone.

### NVIDIA Aggregator

Potentially reusable later:

* OpenAI-compatible client
* Structured JSON parsing
* Provider health checks
* Retry handling
* Model-call accounting

No NVIDIA call is required for the initial extraction milestone.

---

## 13. Processing Tiers

### Tier 0 — Deterministic Code

Used for:

* SEC and HTML parsing
* Metadata removal
* Exact alias replacement
* Pattern-based leak detection
* Document reconstruction
* Hashing
* Checkpointing
* Structured time-series transformations

This should perform most pipeline work.

### Tier 1 — Local Models

Used for:

* Entity discovery
* Product and brand detection
* Executive and subsidiary detection
* Sentiment and event classification
* Initial semantic attacks

Potential environments include local execution and Colab T4.

### Tier 2 — NVIDIA Hosted Models

Used only for difficult or high-risk cases:

* Ambiguous residual entities
* Complex financial sections
* Semantic retrieval
* Candidate reranking
* Adversarial company guessing

Hosted inference must not be described as local inference.

### Tier 3 — Frontier Models

Used only for independent escalation:

* Disagreement between lower tiers
* Highly identifiable packages
* Difficult legal or accounting passages
* Final adversarial review

Frontier models must not perform bulk document processing.

---

## 14. Release Policy

Release decisions must be deterministic and policy-based.

Models may provide evidence. Models may not approve their own masking output.

A release candidate must be rejected when any blocking condition is present, including:

* Known company identifier remains
* Source metadata remains
* Canary entity remains
* Private identity map is included
* True company ranks above the configured attack threshold
* An adversarial model identifies the source with sufficient evidence
* Required attacks were skipped
* Lineage is incomplete
* Artifact hashes do not validate
* Pipeline configuration cannot be reproduced

The exact thresholds belong in:

```text
configs/release_policy.yaml
```

---

## 15. Re-Identification Attacks

Later milestones will include:

### Exact attack

Search for:

* Company names
* Former names
* Tickers
* Executive names
* Subsidiaries
* Products
* Domains
* Accession numbers
* Addresses
* Unique identifiers

### Lexical attack

Use BM25 or equivalent lexical retrieval against the reference company corpus.

### Semantic attack

Embed masked documents and search against the raw reference corpus.

### Reranking attack

Rerank the most likely semantic candidates using a stronger cross-encoder or hosted reranker.

### Numerical attack

Test distinctive:

* Revenue values
* Employee counts
* Store counts
* Segment proportions
* Margins
* Transaction amounts
* Price-series patterns

### LLM guessing attack

Ask independent attacker models to identify the company and provide evidence.

A pipeline report must distinguish between:

* Correct identification
* Incorrect confident identification
* Industry-only identification
* No identification
* Insufficient evidence

---

## 16. Defensive Test Systems

### Canary entities

Test fixtures should contain artificial identifiers such as:

```text
Eleanor Testperson
CanaryShield 9000
canary-test.invalid
```

Tests fail unless all canaries are removed by the relevant masking stage.

### Mutation tests

Identifiers should be tested in multiple forms:

```text
Canary
BLACKROCK
Black Rock
Canary's
Black-Rock
NYSE: CHC6
B1ackRock
```

### Deliberately leaky fixtures

The repository must include test documents with known leaks.

A scanner that returns zero findings against deliberately leaky documents is broken.

### Distinctiveness scoring

Before masking a company, estimate:

* Lexical uniqueness
* Semantic uniqueness
* Market-series uniqueness
* Product and brand density
* Executive density
* Subsidiary density
* Rare-event density

This supports difficulty triage and cost control.

---

## 17. Security Boundaries

Never commit:

* API keys
* `.env` files
* Raw SEC archives
* Bloomberg exports
* Private entity maps
* Masked release artifacts
* Model weights
* Generated embeddings
* Restricted documents
* Canary Search Drive credentials

Logs must redact values associated with names containing patterns such as:

```text
KEY
TOKEN
SECRET
PASSWORD
AUTH
CREDENTIAL
```

Private source-company mappings must remain separate from public release artifacts.

---

## 18. Configuration

Configuration should be explicit and versionable.

### Campaign configuration

```text
configs/campaign.yaml
```

Defines:

* Company ID
* Requested stages
* Data roots
* Resume behavior
* Failure behavior
* Model routing
* Cost limits

### Provider configuration

```text
configs/providers.yaml
```

Defines provider names and model identifiers but never contains secret values.

### Release policy

```text
configs/release_policy.yaml
```

Defines deterministic release thresholds and blocking rules.

Environment variables provide secrets.

---

## 19. Command-Line Interface

Target commands:

```bash
fenrix-synth inventory --company CANARY
fenrix-synth ingest --company CANARY
fenrix-synth extract --company CANARY
fenrix-synth registry build --company CANARY
fenrix-synth mask --company CANARY
fenrix-synth attack --company CANARY
fenrix-synth report --company CANARY
```

Full campaign:

```bash
fenrix-synth campaign run \
  --company CANARY \
  --resume \
  --stop-on-release-failure
```

Only commands supported by implemented behavior should be exposed. Placeholder commands must fail explicitly rather than pretending to succeed.

---

## 20. Engineering Standards

The repository should use:

* Python 3.12 or newer
* `src/` package layout
* `pyproject.toml`
* Type annotations for public interfaces
* Pydantic models for persisted schemas
* `pathlib.Path`
* Atomic filesystem writes
* SHA-256 artifact hashes
* Structured JSON logging
* Dependency injection for providers and storage
* Deterministic offline tests
* Focused modules with narrow responsibilities

Unit tests must not call live external services.

Integration tests should use recorded or synthetic fixtures unless explicitly marked as live tests.

---

## 21. Phased Implementation

### Phase 0 — Foundation

Deliver:

* Python package
* CHC1I
* Configuration models
* Manifest models
* Artifact models
* Source provenance records
* Hashing utilities
* Atomic writes
* Structured logging
* Checkpoint and resume primitives
* Unit tests

### Phase 1 — CHC extraction

Deliver:

* CANARY configuration
* SEC adapter
* Offline SEC fixture
* Filing manifest
* Hash verification
* HTML extraction
* Normalized bronze artifact
* Lineage records
* Resume demonstration
* Integration tests

### Phase 2 — Identity registry and deterministic masking

Deliver:

* Alias registry
* Private identity map
* Pseudonym system
* Exact masking
* Pattern-based removal
* Canary and mutation tests

### Phase 3A — Residual discovery and coverage reporting

Deliver:

* Pattern-based deterministic residual entity discovery
* Coverage reporting with opaque finding IDs
* Deterministic offline tests
* No model dependencies

### Phase 3B Core — Reviewed provider-neutral entity discovery

Deliver:

* Provider abstraction with fake provider for deterministic offline testing
* Document chunking with configurable overlap
* Provider candidate aggregation, deduplication, and risk scoring
* Disagreement tracking (provider, label, boundary) with group_map
* Review queue with accept/reject/defer/duplicate
* Proposal generation, validation, and promotion
* Sanitized candidate summaries with opaque IDs (no private text)
* Promotion → remasking → rescanning workflow

Deferred to Phase 3C:

* GLiNER adapter
* NVIDIA adapter
* Optional model dependency groups
* Explicit live smoke commands
* Provider-specific live tests

### Phase 4 — Re-identification attacks

Deliver:

* Exact attack
* Lexical retrieval
* Semantic retrieval
* Candidate reranking
* Numerical fingerprint attack
* LLM guessing attack
* Company confusion matrix

### Phase 5 — Release reports and external integration

Deliver:

* Rules-based release report
* Hermes status integration
* Slack reporting
* Review UI integration
* Structured-data evaluation
* PPE backtesting integration

---

## 22. Explicit Non-Goals

Do not build during the current milestone:

* LangGraph
* CrewAI
* Another orchestration framework
* Another vector database
* Another backtest engine
* A new dashboard
* A new authentication system
* Kubernetes
* AWS infrastructure
* A permanent GPU server
* Fine-tuning
* A RAG chat interface
* Synthetic news generation
* Full-document frontier-model rewriting
* Production Render changes
* Slack or Hermes integration
* Bloomberg ingestion

---

## 23. Initial Acceptance Criteria

The repository foundation is complete only when:

1. The package installs in a clean environment.
2. `fenrix-synth --help` succeeds.
3. Configuration validation works.
4. Canonical JSON serialization is deterministic.
5. File and object hashing are deterministic.
6. Atomic writes are tested.
7. Invalid or partial checkpoints are rejected.
8. Secret values are redacted from logs.
9. All tests run offline.
10. Formatting, linting, type checking, and tests pass.

The CHC extraction vertical slice is complete only when:

1. CANARY resolves through a private company configuration.
2. One SEC filing is represented by a source manifest.
3. Its source hash is verified.
4. SEC HTML is converted into normalized text.
5. Raw and bronze artifacts retain complete lineage.
6. A completed valid stage is resumed without recomputation.
7. Changed input or configuration invalidates the checkpoint.
8. The full demonstration runs using committed offline fixtures.
9. No raw private source data or secrets are tracked by Git.
10. Exact verification commands and results are documented.

---

## 24. Architectural Decision Rule

When deciding whether to add a component, ask:

1. Does an existing repository already provide it?
2. Is it required for the current milestone?
3. Can it be implemented as a smaller interface?
4. Can it be tested offline?
5. Does it improve reproducibility, security, or evidence?
6. Does it create operational complexity before that complexity is necessary?

If the component is not required for the current milestone, defer it.

The immediate objective is not to build the final platform.

The immediate objective is to produce one real, traceable, tested CHC extraction vertical slice that establishes the foundation for masking and re-identification testing.
