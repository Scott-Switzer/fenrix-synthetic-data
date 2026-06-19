# FENRIX Synthetic Data — Threat Model

## 1. Scope

This threat model applies to the Phase 4 anonymity pilot. It describes
adversarial capabilities, attack surfaces, and defense-in-depth measures
for transforming real financial data into a pseudonymous release candidate
that cannot be reliably linked back to `SRC_001`.

## 2. Adversary Model

### 2.1 Assumed Capabilities

The adversary is assumed to have:

| Capability | Description |
|------------|-------------|
| **Full release access** | Access to the complete `SYNTH_001` release dossier |
| **Financial corpus** | Access to public SEC filings, market data, news archives |
| **Search infrastructure** | Ability to run textual search, embedding similarity, and time-series correlation |
| **LLM access** | Access to frontier and open-source language models |
| **Domain knowledge** | Familiarity with the financial industry, corporate structures, and typical filing patterns |
| **Knowledge of pipeline** | Awareness that a deterministic masking pipeline was applied (but not the private atlas) |

### 2.2 Out-of-Scope Capabilities

| Capability | Reason out of scope |
|------------|---------------------|
| Access to `FENRIX_PRIVATE_ROOT` | Physical/OS security boundary |
| Access to the private identity atlas | Stored outside repository |
| Access to model weights or provider logs | Operational security boundary |
| Real-time market correlation | Release is point-in-time |
| Side-channel attacks on the build machine | Infrastructure security |

## 3. Attack Surfaces

### 3.1 Textual Identity Leakage

**Threat**: Known identifiers (names, tickers, CIK, EIN, LEI, URLs, domains, phone numbers, addresses, email domains, social handles) survive masking.

**Defense**:
- Deterministic identity atlas with exact, normalized, case-insensitive, and fuzzy alias matching
- Metadata removal (PDF/HTML metadata, filename sanitization)
- Unicode normalization (NFKC)
- Possessive form handling
- Punctuation and whitespace variant handling
- Post-masking exact residual scan
- Filename and path privacy validation

### 3.2 Semantic Fingerprint Leakage

**Threat**: Unique phrases, distinctive events, acquisitions, legal proceedings, segment names, slogans, or operational facts survive masking and enable company identification.

**Defense**:
- Semantic fingerprint entries in the identity atlas
- GLiNER discovery as supplementary detection (review-only, not auto-masking)
- Reviewer must explicitly approve fingerprint entries
- Unique phrase scan in the attack suite

### 3.3 Structured Data Re-identification

**Threat**: Price series, volume patterns, volatility signatures, or drawdown events can be correlated against public market data to identify the source.

**Defense**:
- Three transformation variants with increasing privacy protection
- S1_BASIC: rebased prices, normalized volume, removed absolute shares
- S2_PRIVACY: log returns → residual removal → winsorization → pseudo-price reconstruction
- Volume transformed through rolling distributions
- Corporate actions generalized
- Explicit disclosure that transformed prices are not tradable historical prices

### 3.4 Document Structure Fingerprinting

**Threat**: Section ordering, table structures, financial-number patterns, or document-type signatures survive masking and enable matching.

**Defense**:
- Document structure preserved where safe
- Financial numbers retained (utility requirement)
- Tables preserved where possible
- Section retention tracked as utility metric

### 3.5 LLM-Based Guessing

**Threat**: An adversary uses a frontier LLM to guess the source company from the masked text and structured data.

**Defense**:
- Provider-neutral LLM attack interface in the attack suite
- LLM receives only masked release candidates (never raw documents)
- Results redacted in public evidence
- Guesses, confidence, and clue types logged privately
- LLM attack is part of the release gate threshold

### 3.6 Provenance and Path Leakage

**Threat**: Source identifiers appear in artifact names, directory paths, or metadata fields.

**Defense**:
- Path sanitization (replace source identifiers in artifact names)
- Private path detection in release manifests
- No raw source fragments in tracked output
- Release dossier paths use `SYNTH_001` only

## 4. Defense-in-Depth Layers

| Layer | Mechanism |
|-------|-----------|
| **Identity atlas** | Versioned private mapping of all known identifiers, aliases, and fingerprints |
| **Deterministic masking** | Regex-based replacement with typed pseudonyms; no model dependency |
| **Metadata removal** | Strip embedded metadata from HTML, PDF, filenames |
| **GLiNER discovery** | Supplementary entity detection; output goes to review queue only |
| **Exact residual scan** | Independent post-masking scan for surviving identifiers |
| **Semantic fingerprint scan** | Detection of unique phrases and distinctive events |
| **Structured attacks** | Correlation, DTW, volatility similarity, drawdown matching, candidate ranking |
| **LLM guessing attack** | Adversarial model guessing with redacted evidence |
| **Release gate** | Deterministic PASS/FAIL/REVIEW_REQUIRED decision |
| **Release dossier validation** | Final bundle scan for forbidden content before export |

## 5. Residual Risks (Acknowledged)

| Risk | Mitigation | Limitation |
|------|------------|------------|
| Novel semantic fingerprints not in atlas | GLiNER discovery + reviewer review | Requires human review; false negatives possible |
| Financial ratio distinctiveness | S2_PRIVACY residual removal | Does not guarantee indistinguishability from all peers |
| Frontier model identification | LLM attack in gate | Frontier models may improve; gate thresholds are configurable |
| Deterministic replacement predictability | Typed pseudonyms only | Does not resist an adversary who knows the pseudonym scheme |
| Cross-document coreference | Shared pseudonym assignment | Weakens if pseudonym scheme is reverse-engineered |

## 6. Non-Claims

Phase 4 does NOT claim:

- Cryptographic anonymity
- Differential privacy guarantees
- k-anonymity or l-diversity
- Resistance to an adversary with access to the private atlas
- Real-time or continuous release safety
- Safety for companies with highly distinctive financial profiles
- That transformed prices reproduce actual investment performance

## 7. Version

| Field | Value |
|-------|-------|
| Threat model version | `1.0.0` |
| Effective date | 2026-06-19 |
