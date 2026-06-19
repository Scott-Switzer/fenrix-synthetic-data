# FENRIX Synthetic Data — Release Criteria

## 1. Purpose

This document defines the deterministic criteria for accepting or rejecting a
`SYNTH_001` release candidate. The release gate (`release assess`) applies
these criteria automatically. A human reviewer may escalate borderline cases
but may not override a FAIL into a PASS.

## 2. Release Decision Values

| Decision | Meaning |
|----------|---------|
| **PASS** | All blocking conditions clear. Dossier is safe to release. |
| **FAIL** | At least one blocking condition present. Release is blocked. |
| **REVIEW_REQUIRED** | No blocking condition, but warnings or borderline metrics require human judgment. |

## 3. Blocking Conditions (FAIL)

A release candidate MUST be rejected when any of the following is true:

### 3.1 Identity Leakage

- A known company identifier (name, former name, ticker, CIK, EIN, LEI, exchange identifier) is found in the masked text or metadata.
- A person name (executive, director, founder, spokesperson) is found.
- A subsidiary, acquired company, auditor, transfer agent, regulator, or counterparty name is found.
- A product name, brand, service name, or program name is found.
- An identifying location (headquarters, office, branch, city, state, country where identifying) is found.

### 3.2 Digital Identity Leakage

- A website, domain, email domain, social handle, or phone number is found.
- A URL pointing to a source-identifying resource is found.

### 3.3 Provenance Leakage

- A source identifier (`SRC_001` or any real equivalent) appears in a filename inside the release dossier.
- A private path appears in any release artifact.
- Raw source data or fragments are inside the repository.
- Private mappings appear in tracked output.
- The private replacement map is included in the release dossier.

### 3.4 Attack Thresholds

- The structured attack exceeds the configured privacy threshold (default: source must not rank in the top K of the candidate universe).
- A unique phrase or semantic fingerprint scan exceeds the configured threshold (default: zero blocking hits).
- The LLM guessing attack identifies the correct source with confidence above the configured threshold.

### 3.5 Evidence Integrity

- Deterministic reproduction fails (content hashes do not match).
- A required attack did not run.
- Provenance is incomplete.
- Pipeline configuration cannot be reproduced.
- Any validator encounters an unhandled error.

### 3.6 Artifact Validation

- Release artifact hashes do not validate against the expected values.
- The release dossier contains forbidden private fields (per Phase 3C privacy validator).
- An artifact is missing from the expected dossier structure.

## 4. Warning Conditions (REVIEW_REQUIRED)

A release candidate should be escalated to human review when:

- The structured attack places the source in the top 2×K but not the top K.
- The LLM guessing attack identifies the industry correctly but not the company.
- A document-type classification changes after masking (may indicate over-masking).
- Financial-number retention falls below the configured utility threshold.
- A new entity type is discovered by GLiNER that was not in the identity atlas.
- Any attack produces borderline results within 10% of the configured threshold.

## 5. Non-Blocking Conditions (PASS with Notes)

The following do NOT block release:

- Generic industry terms survive masking (e.g., "bank", "regional bank", "financial services").
- Generic geographic references survive (e.g., "Midwest", "United States").
- Common financial metrics survive (e.g., revenue, net income, EPS).
- Model inference produces false positives that do not match atlas entries.
- An attack identifies the industry but not the company.
- Performance metrics for the masking pipeline are within normal ranges.

## 6. Threshold Configuration

Thresholds are configured in `configs/policies/pilot_v1.yaml`:

```yaml
attack_thresholds:
  structured_ranking_k: 10          # Source must not rank in top 10
  llm_confidence_threshold: 0.7     # LLM confidence above this triggers REVIEW
  unique_phrase_hits: 0             # Zero tolerance for fingerprint hits
  semantic_fingerprint_hits: 0      # Zero tolerance
```

## 7. Gate Implementation

The release gate (`src/fenrix_synthetic/release/gate.py`) must:

- Accept structured evidence from all attack runners
- Apply blocking conditions deterministically
- Never convert missing evidence into a PASS
- Never default to PASS on unhandled errors
- Produce a `ReleaseDecision` with explicit pass/fail/review status and rationale
- Log all conditions evaluated and their outcomes

## 8. Version

| Field | Value |
|-------|-------|
| Release criteria version | `1.0.0` |
| Effective date | 2026-06-19 |
