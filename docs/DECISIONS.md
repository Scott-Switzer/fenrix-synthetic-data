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

## Decision 019: Opaque ID Design for Sanitized Outputs

**Date**: 2026-06-19
**Status**: Accepted

**Context**: Decision 014 prohibits unsalted hashes of private values in sanitized outputs. Phase 3B adds provider-based entity discovery where candidates have private matched text that must never appear in sanitized reports, summaries, or JSON output.

**Decision**: Replace all private-text-derived hashes with opaque identifiers derived exclusively from non-private fields. Two separate schemes serve different domains:

1. **Phase 3A coverage**: `opaque:v2:{document_artifact_id}:{entity_type}:{start}:{end}` — SHA-256 truncated to 16 hex chars. Handles collision avoidance across documents and spans.
2. **Phase 3B candidate summaries**: `opaque:{candidate_id}` — SHA-256 truncated to 16 hex chars. Derived from candidate_id, not from private matched text.

These schemes use different namespaces and are not cross-referenceable.

**Rationale**: Even a salted hash of a private value confirms the value's presence if the value is guessable. Opaque IDs derived from structural metadata reveal nothing about the private content while remaining deterministic.

**Constraints**:
- Never include: private entity text, aliases, company names, tickers, domains, plain/truncated hashes of private values
- Private artifacts (ProviderCandidate, PrivateDiscoveryArtifact, MaskingAudit) may retain matched_text_hash for internal integrity
- Sanitized outputs must never contain matched_text_hash

---

## Decision 020: Disagreement Tracking in Deduplication

**Date**: 2026-06-19
**Status**: Accepted

**Context**: CandidateDisagreementResolver was a no-op placeholder. Provider-based entity discovery produces candidates that may disagree on entity type, label, boundaries, or confidence for the same text span.

**Decision**: Eliminate the no-op `CandidateDisagreementResolver`. Implement disagreement tracking within `CandidateDeduplicator.deduplicate()` via a `group_map` that records all candidates in each disagreement group. The representative is selected by highest confidence, then earliest candidate_id. All group members have `duplicate_group_id` set.

**Rationale**: A no-op resolver silently discards evidence. Group-level tracking preserves all provider responses, confidences, and labels while still producing a deduplicated candidate list. Reviewers can inspect the full disagreement evidence from the group_map.

**Preserved evidence**:
- Provider disagreement
- Label disagreement
- Boundary disagreement
- All contributing confidence values
- Selected representative
- Deterministic selection reason (confidence, then candidate_id)
- Duplicate group membership

---

## Decision 021: GitHub Actions CI Before Phase 3C

**Date**: 2026-06-19
**Status**: Accepted

**Context**: Phase 3B Core is merged to main. Future work (Phase 3C and beyond) needs quality gates that run automatically on PRs targeting main and on pushes to main.

**Decision**: Add `.github/workflows/ci.yml` with two jobs: `quality` (ruff format check, ruff lint, mypy) and `tests` (pytest --collect-only, pytest full suite). The CI requires no network access, no private company registry, no model downloads, and no API keys.

**Triggers**:
- Pull requests targeting `main`
- Pushes to `main`
- Manual `workflow_dispatch`

**Permissions**: `contents: read` only.

**Exclusions**:
- GLiNER, PyTorch, transformers not installed
- NVIDIA clients not installed
- Optional live-provider dependencies not installed
- No secrets configured

**Rationale**: Introducing CI after Phase 3B merge ensures that subsequent work (Phase 3C adapters, optional dependencies, live smoke commands) cannot silently break core quality or tests. Keeping CI fully offline-capable avoids secret management and ensures any contributor can reproduce checks locally.

---

## Decision 022: Local GLiNER Precedes NVIDIA

**Date**: 2026-06-19
**Status**: Accepted

**Context**: Phase 3C begins a model-aided discovery tier above Phase 3A/3B. Hosted NVIDIA inference requires credentials, network egress, and represents uncontrolled third-party risk. A local open-source model (GLiNER) can run offline and inside the same trust boundary as the rest of the project.

**Decision**: Phase 3C implements an optional local GLiNER adapter. NVIDIA hosted inference is not implemented in this branch and is intentionally left for a later milestone after the local integration proves out.

**Rationale**: Local execution has no credentials, no network, and reproducible identity. Hosted inference would require secret management, an explicit threat model for the third party, and additional privacy controls that are outside this milestone.

**Date**: 2026-06-18
**Status**: Accepted

**Context**: CANARY maps to a fictional canary company in the private company config. No reviewed real-company identity registry exists.

**Decision**: The CANARY implementation is demonstrated with synthetic canary values only. Actual real-company deterministic masking requires a reviewed private registry populated with real entities and aliases. Do not claim real-company masking is complete.

**Rationale**: Claiming masking without a reviewed registry would be misleading. The synthetic demonstration proves the pipeline works; the actual masking depends on registry population.

---

## Decision 022: Local GLiNER Precedes NVIDIA

**Date**: 2026-06-19
**Status**: Accepted

**Context**: Phase 3C begins a model-aided discovery tier above Phase 3A/3B. Hosted NVIDIA inference requires credentials, network egress, and represents uncontrolled third-party risk. A local open-source model (GLiNER) can run offline and inside the same trust boundary as the rest of the project.

**Decision**: Phase 3C implements an optional local GLiNER adapter. NVIDIA hosted inference is not implemented in this branch and is intentionally left for a later milestone after the local integration proves out.

**Rationale**: Local execution has no credentials, no network, and reproducible identity. Hosted inference would require secret management, an explicit threat model for the third party, and additional privacy controls that are outside this milestone.

---

## Decision 023: GLiNER Is Optional

**Date**: 2026-06-19
**Status**: Accepted

**Context**: A machine-learning dependency in the default install would slow tests, force torch and transformers on every contributor, and risk leaking network calls.

**Decision**: GLiNER is an `[project.optional-dependencies].local-ner` extra. The package is `gliner==0.2.27`. It is not installed by `pip install -e ".[dev]"` and not installed by CI. Importing the discovery subpackage must never trigger `import gliner`.

**Rationale**: An optional dependency group keeps the default install deterministic, fast, and offline-only, while making the feature discoverable and reproducible for opt-in users.

---

## Decision 024: Downloads Are Explicit

**Date**: 2026-06-19
**Status**: Accepted

**Context**: GLiNER requires model weights. An implicit download during pipeline initialization would breach the offline-only contract and risk silent network egress on constrained environments.

**Decision**: `GLiNERConfig.allow_download` defaults to `False`. The CLI exposes `fenrix providers prepare --provider gliner_local --allow-download` as the only acquisition path. The loader uses `local_files_only=True` when `allow_download=False`. No call to `from_pretrained` ever falls through to network without explicit opt-in.

**Rationale**: Faithful offline-by-default requires that even the most "natural" workflow (import, configure asset, run) never accidentally fetches model weights.

---

## Decision 025: Synthetic Smoke Only

**Date**: 2026-06-19
**Status**: Accepted

**Context**: Using real canary-company text in a live GLiNER test would leak real entities outside the privacy boundary.

**Decision**: The committed synthetic benchmark (in `gliner/benchmark.py`) contains only synthetic entities that resemble but do not match real canary facts. Live smoke executes against the synthetic benchmark only. No canary document is sent to the local model in any automated path.

**Rationale**: Synthetic-only execution keeps the milestone out of the privacy-hardened `data/` directories and makes the smoke reproducible across contributors.

---

## Decision 026: Provider Output Requires Review

**Date**: 2026-06-19
**Status**: Accepted

**Context**: Model output is candidate evidence only. Automatic acceptance or promotion would invalidate the reviewer gate established in Phase 3B.

**Decision**: GLiNER candidates enter the existing `ReviewQueue` with `review_status="pending"`. Reviewers must invoke `accept`, `reject`, `defer`, `duplicate`, or `already_registered` with a mandatory reason. Promotions pass through the same `promote_proposal` flow that requires experimental collision analysis. The CLI surfaces no automatic acceptance regardless of confidence score.

**Rationale**: Confidence is not calibrated leakage probability; review is the only structural gating we trust.

---

## Decision 027: Model Revision Is Recorded

**Date**: 2026-06-19
**Status**: Accepted

**Context**: Reproducibility requires more than a model name. Different commits inside a Hugging Face model id can change weights without changing the identifier.

**Decision**: `model_identity` records `model_id`, requested `revision`, `device`, `adapter_policy_version`, `config_hash`, `model_load_timestamp`, `model_load_succeeded`, and `resolved_revision` (when the underlying library exposes `id_to_name`). If the library does not expose a resolved revision, that limitation is recorded explicitly: `resolved_revision=null`.

**Rationale**: Bit-for-bit reproducibility is rare for ML weights; the integrity guarantees we can give are the configuration hash, the adapter policy version, the load timestamp, and whether the resolved revision matches expectations.

---

## Decision 028: Threshold Remains Provisional

**Date**: 2026-06-19
**Status**: Accepted

**Context**: Picking a permanent default threshold from one benchmark risks overfitting.

**Decision**: `GLiNERConfig.threshold` defaults to `0.50` and is documented as provisional. Default CI does not run the benchmark at any threshold. The CI does not select a production threshold; a sweep over `[0.30, 0.40, 0.50, 0.60, 0.70]` is available offline but does not feed CI status.

**Rationale**: A threshold's quality depends on the benchmark distribution and downstream review cost; record metrics at multiple thresholds and let reviewers choose.

---

## Decision 029: CI Excludes Model Execution

**Date**: 2026-06-19
**Status**: Accepted

**Context**: CI must remain hermetic, fast, and reproducible without model weights. The `local_model` pytest marker would not be honored by default test selection if no model is downloaded.

**Decision**: The pytest `local_model` marker is registered in `pyproject.toml`. Default CI runs the full suite via `pytest --disable-socket --allow-unix-socket`. The CI does not install `local-ner`, does not download weights, and does not run any `local_model` test. Live GLiNER smoke (if executed locally) uses synthetic text only and is documented but unrequired.

**Rationale**: Default CI must test the adapter's offline guarantees without paying for model load latency or for GPU inference.

---

## Decision 030: ``--enable-nvidia`` Deferred From Current Beta

**Date**: 2026-06-20
**Status**: Accepted

**Context**: The bounded beta of ``fenrix-synth reanonymize-run`` exposes
``--source-run``, ``--output-root``, ``--limit-forms``, and ``--limit-news``
flags but NOT ``--enable-nvidia``. Reviewers would otherwise discover the
gap cold and ask for clarification. The Phase 3C deferral in AGENTS.md
already lists NVIDIA-hosted review as deferred, but no DECISIONS entry
hitherto recorded the explicit gap in the beta CLI surface.

**Decision**: ``--enable-nvidia`` (and the NVIDIA review adapter behind it)
remains deferred from the current beta release. The release gate's
``stubs_enforced`` list explicitly tracks ``nvidia`` so a downstream
consumer of ``qa/release_gate.json`` can verify the stub status without
reading the source code.

**Rationale**: Recording the gap as an explicit decision — rather than
leaving it as silent absence — lets a future reviewer or contributor
add the flag without re-deriving intent. The deferred NVIDIA adapter is
a known scope-choice (Decision 022); this entry just makes its absence
from the beta CLI visible.

**Implications**:
- ``--enable-nvidia`` is not present in ``fenrix-synth --help``.
- ``beta_status`` stays ``INCOMPLETE`` until a real NVIDIA adapter lands.
- ``release_safe`` remains ``false`` for any release that relies on
  the NVIDIA reviewer's verdict.

---

## Decision 031: Validated-Harvesting Admission Pipeline + H3 Collision Fix

**Date**: 2026-06-20
**Status**: Accepted (H3 fix shipped + all quality gates GREEN on a single
PR; the direct-privacy ``post_mask_hits == 0`` acceptance criterion remains
an open follow-up tracked below.)

**Context**: The Phase 4 vertical slice's
``fenrix-synth reanonymize-run`` command was failing ``post_mask_hits == 0``
on the real source-company source run under ``[PRIVATE_RUN_PATH]``.
Two distinct leak patterns surfaced:

1. **Person admission was over-permissive** \u2014 boilerplate fragments like
   ``/s/ Jennifer Smith`` and ``authorized officer of us`` were landing in
   the public alias set. The previous strict-titlecase regex
   ``[A-Z][a-z]+|[A-Z]\\.?`` was *also* under-aggressive because it could
   capture 1-letter prose tokens (``I``, ``A``) as candidate name parts,
   inflating the rejection histogram to 124,193 rows.
2. **Merge collisions silently dropped harvested entries** \u2014 the curate-upgrade
   branch's merge loop used ``if entity_id in existing_entity_ids: counter += 1;
   continue``, which silently forfeited the harvested value whenever the
   counter offset collided with a curated ``harvest_*`` slot from a previous
   run. The bounded-beta CLI log ``Merged 124 harvested entities`` while
   483 had been sourced evidenced that ~359 sorted values were dropped.

**Decision**:

- **Fix 1 (Person admission firewall)**: Tighten the regex from
  ``[A-Z][a-z]+|[A-Z]\\.?`` to ``[A-Z][a-z]+|[A-Z]\\.`` (require period for
  initials). Implement a 6-rule admission predicate in
  ``src/fenrix_synthetic/reanonymize/atlas_builder.py``:
  (a) strip leading blocklist token from
  ``_BLOCKLIST_PERSON_TOKENS`` (16 entries: ``director``, ``officer``,
  ``authorized``, ``us``, ``company``, ``the``, ``by``, ``executive``,
  ``chief``, ``vp``, ``of``, ``and``, ``or``, ``has``, ``will``, ``shall``);
  (b) require 2-4 surviving tokens; (c) reject inner-blocklist token;
  (d) require titlecase token shape; (e) reject trailing verb from
  ``_POST_NAME_VERB_TOKENS`` (20 entries); (f) require a high-confidence
  context window (``_HIGH_CONFIDENCE_CONTEXTS`` 10 entries: ``signatures``,
  ``by:``, ``/s/``, ``director``, ``officer``, ``executive``, ``vp``,
  ``chief executive``, ``chief financial``, ``president``).
- **Fix 2 (Handle admission firewall)**: Stem-match against
  ``_risk_stems`` (ticker + curated company tokens). Empty ``_risk_stems``
  fails closed: every handle rejected with ``handle_not_tied_to_root``.
- **Fix 3 (Rejection-report privacy contract)**: Re-categorize every
  admission rejection via a ``RejectionReason`` enum, emit
  ``qa/direct_identifier_rejected_candidates_report.json`` containing
  ONLY counts + enum strings (never raw rejected values), and ensure
  rejected candidates NEVER inflate ``aliases_built`` nor participate in
  post-mask scans. The schema is always-on: the file is written even
  with zero rejections so downstream tooling can always read it.
- **Fix 4 (Curated-upgrade loop + case-insensitive policies)**: After
  parsing existing aliases in ``_merge_harvest_into_atlas_yaml``, walk
  the curated aliases and upgrade ``match_policy`` to ``case_insensitive``
  + append ``[punctuation_variant, possessive, whitespace_normalize]`` to
  ``enabled_mutation_policies`` for ``entity_type`` in
  ``{COMPANY, TICKER, BRAND}``. Other entity types (accession, cik,
  rare_phrase, xbrl_concept, url) stay literal so we don't over-expand
  the leak surface for rare-phrase captures. The same policy applies to
  both *curated* (in-place upgrade) AND *harvested* (fresh emission)
  aliases. The ``case_insensitive_types`` constant is hoisted to
  function-local scope BEFORE both loops so the curated upgrade reads
  the same value the harvested insert writes.
- **Fix 5 (H3 collision bugfix)**: In ``_merge_harvest_into_atlas_yaml``,
  replace the buggy ``if entity_id in existing_entity_ids: counter += 1;
  continue`` with a single ``while`` loop that increments ``counter`` until
  BOTH ``entity_id`` AND ``alias_id`` land on free slots. Bounded by 1
  million attempts as a defensive guard with a logged-warning skip-and-continue
  path so pathological curators cannot hang the run.

**Rationale**:

- All five fixes ship together because they were diagnosed together
  against the SAME bounded-beta observation. Shipping them serially
  re-runs the diagnostic work each time.
- The defensive 1M bound on Fix 5 is intentionally redundant with the
  deterministic-counter contract: a curated atlas carrying 1M harvest
  slots is implausible in practice; the bound's only purpose is to fail
  closed cleanly rather than spin.
- The curated-upgrade loop runs BEFORE the harvest-insert loop. This is
  intentional: an in-place mutation preserves ``alias_id`` (no collisions,
  so the post-upgrade state is collision-irrelevant for Fix 5's contract).

**Verification (all gates GREEN at HEAD)**:

- ``ruff format --check .`` \u2014 177 files already formatted.
- ``ruff check .`` \u2014 All checks passed.
- ``mypy src/fenrix_synthetic --show-error-codes`` \u2014 No issues found in 127 source files.
- ``pytest --disable-socket -q`` \u2014 **896 passed**, 4 skipped, 9 warnings.
  New tests cover 4 test classes (+24 individual tests) atop d78d154 baseline:
  - ``TestPersonAdmissionPipeline`` (10): director stripping, lowercase anchors,
    trailing-verb rejection, no-context rejection, validation that rejected
    candidates don't inflate ``aliases_built``.
  - ``TestHandleAdmissionPipeline`` (5): handle variant emission, no-risk-stem
    fail-closed, random-user rejection, blocklist-word handle rejection.
  - ``TestOrchestratorWritesRejectedCandidatesReport`` (3): schema-always-on,
    no-raw-values, zero-rejections path.
  - ``TestAtlasMergeFix4CaseInsensitive`` (4): harvested company/ticker/brand
    use case_insensitive, accession stays literal, curated entries survive
    append, curated literals upgrade to case_insensitive.
  - ``TestMergeCollisionBugfix`` (1): dense-atlas (400 slots) + 10 fresh values
    all land at slots \u2265 401, no duplicates, no drops.

**Acceptance Criterion Status**:

- ``post_mask_hits == 0`` in real bounded beta on the user's source-company
  run \u2014 **NOT YET achieved**. Three iterations of regression show a static
  40-hit residual: ``{\"or director\": 20, \"authorized us\": 18, \"SOURCE_TICKER\": 2}``.
  Aliases-built went 452 \u2192 483 \u2192 486 across iterations, indicating Fix 5
  did fire (more entries land). The persistent residual is a SECOND-LAYER
  issue independent of the collision bug:
  - The 38 fragments appear INSIDE the masked body text co-occurring with
    masked entities (e.g., ``, Company 682 or director``), suggesting the
    curated source atlas contains these as ``rare_phrase`` aliases with
    match_policy=literal that the masker cannot substitute due to word
    boundary / surrounding-token handling.
  - The 2 \u00d7 source-ticker literal hits are NOT in ``\\bSOURCE_TICKER\\b`` form across
    the masked ``public/surrogates/sec/`` files; they appear to score on
    filename metadata carryover (``source_run:`` path segment containing
    the source ticker directory name) rather than body text.
  - Hypothesised second-layer fix (deferred): normalise-out prose fragments
    in ``build_private_values_dict`` (``registry_load.py``) + sanitise
    metadata carryover in ``TextAnonymizer`` filename emission. Approximately
    50 lines of change across two modules; not shipped with this PR.

**Open Follow-ups** (tracked in code as TODO; not blocking this commit):

- TODO \[registry_load.__init__\] \u2014 Drop pre-curated prose fragments
  (``or director``, ``authorized us``, ``authorized officer``, etc.) from
  the ``private_values_dict`` so neither the masker nor the post-mask
  scanner treats them as candidate identifiers.
- TODO \[text_anonymizer.build_filename\] \u2014 Apply case-folding + ticker
  substitution to the ``source_run`` metadata carrier that surfaces in
  ``filename_and_metadata_scan`` so the source ticker doesn't survive via the
  path-segment carryover.

**Implications**:

- ``beta_status`` stays ``INCOMPLETE`` (the 40-hit residual keeps the
  post-mask-identity-hits gate condition at blocking=true). The release
  gate already records this correctly.
- ``release_safe`` stays ``false`` in all iterations; semantic + NVIDIA
  stubs continue to add the REQUIRED semantic_privacy_attack_implemented
  and nvidia_review_implemented blocking conditions.
- This PR is staged progress per user direction \u2014 do not re-run the
  bounded beta in this state without first applying the second-layer fix
  tracked above.