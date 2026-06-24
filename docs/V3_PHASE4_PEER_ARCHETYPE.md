# V3 Phase 4: Peer Archetype Anonymization

**Branch:** `feature/professor-bundle-pipeline`
**Date:** 2026-06-24

## 1. Why Peer Archetypes Are Needed

The V2 masking system reduced direct deanonymization to peer/category-level
confusion but still leaked identity through:

- **Sector labels** — a company in an unusual or narrow sector stood out
- **Financial-bin fingerprints** — the exact combination of revenue, margin,
  and growth buckets created a unique signature
- **Synthetic trajectories** — preserved movement patterns that were
  recognizably source-derived
- **Suggestive fake names** — replacement names sometimes carried semantic
  similarity to the original

Peer archetypes solve this by making each anonymized company look like a
**broad peer archetype**, not a one-to-one masked replica.

## 2. How This Differs From Simple Masking

| Simple Masking | Peer Archetype |
|---|---|
| Replace text token-by-token | Assign to a broad archetype category |
| Preserve exact numeric relationships | Bucket into coarse feature ranges |
| One source → one output | Source must be consistent with ≥5 public peers |
| Privacy = "no direct matches found" | Privacy = "not uniquely implied by sector, scale, ratios, trajectory, or business model" |

## 3. Allowed Features

Only broad, coarse features may be used for public archetype matching:

```
broad_sector
archetype
revenue_bucket
asset_intensity_bucket
profitability_bucket
leverage_bucket
growth_bucket
cash_intensity_bucket (future)
cyclicality_bucket (future)
```

## 4. Forbidden Features (Never in Public Output)

```
exact ticker
exact company name
CIK
exact revenue
exact market cap
exact fiscal year-end
exact segment names
exact product names
exact geography footprint
exact executive names
exact store count
exact subscriber count
exact debt note labels
exact acquisition names
exact litigation names
exact daily price path
```

## 5. How k_peer Is Computed

1. Score all candidates against the source using feature distance
   (normalized cosine of overlapping bucket values)
2. Exclude the source from the count
3. Count candidates with similarity ≥ 0.5 × max_similarity
4. That count is `k_peer`

## 6. What source-not-top-k Means

After scoring and ranking all candidates:

- **Source in top 1**: The source uniquely stands out → **FAIL**
- **Source in top 3**: High deanonymization risk → **FAIL**
- **Source in top 5**: Moderate risk → **WARN**
- **Source outside top 5**: Acceptable ambiguity → **PASS** (if k_peer ≥ 5)

## 7. Public/Private Output Separation

### Private (`private/qa/peer_archetype_audit.json`)
- Contains source-aware details
- Includes `source_rank`, `source_in_top_1/3/5`
- Lists all peer candidates with similarity scores
- Never enters the professor ZIP

### Public (`public/anonymized/<ID>/profile/archetype_card.json`)
- Contains only: `anonymized_company_id`, `archetype_label`, `broad_sector`,
  `description`, `peer_range`, `k_peer`, `passes_peer_privacy`
- Never contains: real ticker, CIK, source rank, peer tickers

### Public (`public/anonymized/<ID>/profile/profile.md`)
- Human-readable markdown profile
- Broad archetype label and description
- Peer-basket size range (e.g., "5+ plausible peers")
- High-level investment-relevant traits

## 8. How to Run Tests

```bash
# Run peer archetype tests
pytest tests/unit/test_peer_archetype.py tests/unit/test_peer_privacy_scoring.py -v

# Run with coverage
pytest tests/unit/test_peer_archetype.py tests/unit/test_peer_privacy_scoring.py --cov=fenrix_synthetic.anonymization.peer_archetype
```

## 9. Known Limitations

- **No numeric transformation yet** — financial values are bucketed but not
  morphed. Phase 5 will implement numeric transformation.
- **No trajectory morphing** — time-series patterns are not altered. Phase 6.
- **No LLM blind-guessing** — adversarial AI attacks not yet implemented. Phase 9.
- **Archetype taxonomy is initial** — 8 archetypes covering common sectors.
  Will need expansion as more companies are added.
- **Not formal differential privacy** — this is risk-reporting, not DP.

## 10. Next Phase: Numeric Transformation

Phase 5 will implement numeric transformation while preserving peer-archetype
consistency. Each anonymized company's bucketed values will be populated from
the peer distribution rather than the source's exact values.

## 11. Pipeline Integration Status

**Status: COMPLETE** — Peer archetype is now a mandatory pipeline stage.

The `PEER_ARCHETYPE` stage (stage 10) runs after `SYNTHETIC_PROFILE_BUILD`
and before `FILING_RECONSTRUCT`. It produces:

### Generated Files

**Public (included in professor ZIP):**
- `public/anonymized/<ID>/profile/archetype_card.json`
- `public/anonymized/<ID>/profile/profile.md`

**Private (excluded from ZIP):**
- `private/qa/peer_archetype_audit.json`

### Stage Behavior
- In fixture mode: loads the peer universe fixture from `tests/fixtures/peer_archetype/`
- In production mode: would load a broader peer database (not yet implemented)
- Fail mode: if the fixture is missing, the stage fails with a clear error
- Fail mode: if `k_peer < 5` or source ranks top-1/3, the stage fails

## 12. Privacy Limitations

- **k_peer is a risk heuristic, not formal anonymity** — it indicates
  whether enough plausible peers exist, but does not constitute a formal
  privacy guarantee.
- **Archetype taxonomy is initial** — 8 archetypes covering common sectors.
  Will need expansion as more companies are added.
- **No formal differential privacy** — this is risk-reporting, not DP.

## 13. Next Phase: Numeric Transformation

Phase 5 will implement numeric transformation while preserving peer-archetype
consistency. Each anonymized company's bucketed values will be populated from
the peer distribution rather than the source's exact values.

## Module Location

```
src/fenrix_synthetic/anonymization/peer_archetype.py
src/fenrix_synthetic/professor/stages.py         # PEER_ARCHETYPE stage enum
src/fenrix_synthetic/professor/orchestrator.py   # Stage implementation (stage 10)
tests/unit/test_peer_archetype.py
tests/unit/test_peer_privacy_scoring.py
tests/integration/test_professor_bundle_peer_archetype_stage.py
tests/fixtures/peer_archetype/peer_universe.yaml
```
