# V3.1 Professor Bundle — Final Production & Hardening Report

**Date:** 2026-06-26  
**Branch:** `feature/professor-bundle-pipeline`  
**Latest Commit:** `e9e5730`  
**Build Location:** Lightning AI (`s_01kvxp4wpmbdenat5ne183xqph@ssh.lightning.ai`)

---

## Final Verdict: **NOT_PROFESSOR_READY_PENDING_LIGHTNING_BUILD**

The decoy-aware LLM review implementation is **code-complete** with 30 unit tests passing, ruff clean, and mypy clean. All local gates pass. However, the Lightning SSH key has expired — the production rebuild with decoy-aware live review cannot run until Lightning connectivity is restored.

The code is functional: when built (with a valid Lightning connection and live NVIDIA API key), the decoy-aware pipeline should run, score, and produce a `decoy_aware_llm_summary.json`. If that gate passes, the verdict will upgrade to `PROFESSOR_READY_V3_1`.

---

## Gate Results

| Gate | Status | Detail |
|:-----|:-------|:-------|
| Privacy Gate (blind) | **PASS** | 0 source top-1, 0 source top-3, 0 high-confidence |
| Utility Gate | **PASS** | Score 1.00 (8/8 companies) |
| Artifact Quality Gate | **PASS** | 8 checks, 0 failures |
| Strict Release Gate | **PASS** | No direct identifiers, no forbidden paths |
| **Decoy-Aware LLM Review** | **CODE-COMPLETE, PENDING LIVE BUILD** | 30 unit tests pass locally; live Lightning build blocked |

---

## V3.1 Decoy-Aware Implementation (Code-Complete)

### Files Modified

1. **`src/fenrix_synthetic/qa/llm_provider.py`** (+140)
   - Added `_DECOY_SYSTEM_PROMPT` for decoy-aware adversarial review
   - Added `_build_decoy_aware_review_prompt(public_content, company_id, candidate_labels)` with opaque Candidate A-E labels
   - Extended `StubConfig` with `decoy_response` field and 5 decoy factory methods:
     - `decoy_pass_low_confidence()` — wrong guess, low confidence
     - `decoy_pass_wrong_guess_medium()` — wrong guess, medium confidence
     - `decoy_fail_top1_high_confidence()` — correct top-1, high confidence
     - `decoy_fail_direct_leak()` — evidence includes direct identifiers
     - `decoy_warn_business_model()` — true source top-3, low confidence, business model only
   - Updated `OfflineStubProvider.complete_json()` to return `decoy_response` when set
   - Updated `OpenAICompatibleProvider.complete_json()` to auto-detect decoy prompts and send `_DECOY_SYSTEM_PROMPT`

2. **`src/fenrix_synthetic/qa/confidence_scoring.py`** (+230)
   - Added evidence basis constants: `_DIRECT_LEAK_BASES` (direct_identifier, exact_number, metadata_leak, product_event_fingerprint), `_ACCEPTABLE_BASES` (business_model, sector_only), `_VALID_BASES`
   - Added `PrivateDecoyScoreDetail`, `PublicDecoyScoreSummary`, `DecoyScoreResult` dataclasses
   - Added `score_decoy_aware_guess()` with 5 scoring rules:
     - FAIL: direct leak evidence (any basis in `_DIRECT_LEAK_BASES`)
     - FAIL: true source top-1 with medium/high confidence
     - FAIL: true source top-3 with high confidence
     - WARN: true source top-3 with low confidence, broad evidence only
     - PASS: true source not top-1/top-3, no direct leaks
   - Added `_count_bases()` helper for evidence basis histograms

3. **`src/fenrix_synthetic/professor/multi_orchestrator.py`** (+400)
   - Added `_DECOY_PEER_POOLS`: per-archetype lists of 8-10 real public peer companies (8 archetypes covered)
   - Added `_run_per_company_decoy_aware_review()`: builds 5-candidate set (true source + 4 sector peers), shuffles deterministically, sends opaque-label prompt to LLM, scores, writes redacted public summary only. Private candidate mapping written under `_inner_work_root/private/qa/` — NEVER enters the student ZIP
   - Added `_aggregate_decoy_aware()`: produces `qa/decoy_aware_llm_summary.json` with aggregate PASS/WARN/FAIL counts, direct leak counts, top-1/top-3 hits — zero real company names
   - Wired decoy review into `_run_impl()` per-company loop (between blind guess and utility)
   - Updated verdict cascade: `DECOY_AWARE_GATE_FAILED` blocks `PROFESSOR_READY_V3_1`
   - Added decoy-aware assertions to final validation
   - Added `_update_docs_with_decoy_results()`: appends decoy section to `RUN_SUMMARY.md` and `RELEASE_MANIFEST.json`
   - Fixed pre-existing `_random` → `random` NameError in `_resolve_archetype_for_company()`
   - Expanded `international_nicotine_products` peer pool from 8 to 10 entries

4. **`tests/unit/test_decoy_aware_review.py`** (NEW, 30 tests)
   - `TestDecoyPromptBuilder` (4): opaque labels only, required schema keys, company ID in prompt, system prompt safety
   - `TestDecoyScoringPass` (3): wrong guess low confidence, wrong guess medium confidence, source not in top-3
   - `TestDecoyScoringFail` (6): top-1 high conf, top-1 medium conf, direct_identifier evidence, exact_number evidence, product_event_fingerprint evidence, top-3 high confidence
   - `TestDecoyScoringWarn` (2): top-3 low conf business model only, top-3 low conf financial pattern
   - `TestDecoyPublicSafety` (3): no source names in public summary, evidence basis counts present, private detail contains source mapping
   - `TestDirectLeakBases` (4): verify all 4 direct leak bases are present
   - `TestDecoyStubProvider` (4): decoy schema returned, fail_direct_leak response, warn_business_model response, empty decoy falls back to blind format
   - `TestDecoyStubRoundTrip` (4): PASS round-trip, FAIL top-1 round-trip, FAIL direct leak round-trip, WARN round-trip

### Privacy Safeguards

- **Prompt:** Only Candidate A/B/C/D/E opaque labels — zero real company names in the LLM prompt
- **Private mapping:** Written under `_inner_work_root/private/qa/` (temp directory outside bundle root) — physically excluded from ZIP
- **Public summary:** Contains only aggregate counts (PASS/WARN/FAIL, direct leak count, top-1/top-3 hits) — zero real names, tickers, or candidate-to-company mappings
- **System prompt:** Separated from blind review; auto-detected by keyword matching in `OpenAICompatibleProvider`

---

## Local Verification Results

| Check | Tool | Result |
|:------|:-----|:-------|
| Syntax/compile | `python3 -m compileall src tests` | **PASS** |
| Lint | `ruff check` (v0.11.7) | **PASS** — 0 errors |
| Type check | `mypy` (confidence_scoring.py, llm_provider.py) | **PASS** — 0 errors |
| Artifact quality tests | `pytest tests/unit/test_artifact_quality_gate.py` | **PASS** — 26/26 |
| Decoy-aware tests | `pytest tests/unit/test_decoy_aware_review.py` | **PASS** — 30/30 |
| Combined tests | `pytest` both suites | **PASS** — 56/56 in 0.94s |

---

## Lightning Status

**SSH: BROKEN** — `Permission denied (publickey)` on all attempts using the configured `lightning_rsa` key. The SSH config at `~/.ssh/config` has:

```
Host ssh.lightning.ai
  IdentityFile ~/.ssh/lightning_rsa
  IdentitiesOnly yes
```

The key exists at `~/.ssh/lightning_rsa` and loads into the agent successfully, but Lightning rejects it. Likely cause: the Lightning AI studio instance was terminated and recreated with a new key. The Lightning SSH hostname (`s_01kvxp4wpmbdenat5ne183xqph@ssh.lightning.ai`) may also have changed.

### Required to Unblock

1. Obtain the new Lightning SSH key from the Lightning AI studio dashboard
2. Update `~/.ssh/lightning_rsa` with the new private key
3. Re-run the production build:
   ```bash
   ssh s_01kvxp4wpmbdenat5ne183xqph@ssh.lightning.ai \
     'cd /teamspace/studios/this_studio/fenrix-synthetic-data && \
      source .env && \
      fenrix-synth build-production-bundle \
        --output /teamspace/studios/this_studio/fenrix-data/runs/v3_1_production \
        --source-mapping .../source_companies.yaml \
        --source-archive-inventory .../source_archive_inventory.json \
        --llm-review-provider openai_compatible \
        --llm-review-model meta/llama-3.1-70b-instruct \
        --llm-review-base-url https://integrate.api.nvidia.com/v1 \
        --release-date 2026-06-26'
   ```
4. Run ZIP inspection
5. Verify `qa/decoy_aware_llm_summary.json` exists and `decoy_gate == "pass"`
6. Update verdict to `PROFESSOR_READY_V3_1` if all gates pass

---

## Commit History

| SHA | Description |
|:----|:------------|
| `e9e5730` | feat(v3.1): implement decoy-aware LLM review — opaque candidate labels, evidence classification, FAIL on direct leaks |
| `05867df` | test(v3.1): add 26 unit tests for artifact_quality_gate, update final report with decoy-aware gap |
| `c695af3` | feat(v3.1): professor-ready rebuild — 8 distinct archetypes, 10yr financials, artifact quality gate, utility gate fix |

---

## Send/No-Send Recommendation

**DO NOT SEND** — The decoy-aware LLM implementation is code-complete and locally verified, but the final production ZIP with live decoy-aware review has not been built. The Lightning SSH key needs to be refreshed. Once rebuilt with a passing decoy gate, the verdict will upgrade to `PROFESSOR_READY_V3_1`.
