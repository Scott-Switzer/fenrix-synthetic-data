# V3 Phase 5C: News Reconstruction Plan

## Status

**Planning document — implementation deferred to Phase 7.**

This document records the design decisions, privacy risks, and
implementation plan for synthetic news reconstruction. No real news
headlines or article text are copied into public outputs.

## News Source Options

| Source | Status | Privacy Risk | Notes |
|--------|--------|-------------|-------|
| GDELT | Research only | Medium (real events) | Global event database; requires filtering |
| Yahoo Finance local data | If already present | Low | Must verify no real headlines in output |
| SEC 8-K event extraction | Preferred | Low | Extract broad event classes from filings |
| Company filing event sections | Preferred | Low | Risk factors, MD&A contain event signals |

## Rules

1. **Do not copy real headlines.** All public news output is synthetic.
2. **Do not expose article URLs in public release** by default.
3. **Convert real news into broad event classes.**
4. **Never include exact dates, amounts, or quotes** that could identify the source.

## Public Output Targets

```
public/anonymized/COMPANY_XXX/news/
  synthetic_news_briefs.md
  event_timeline.csv
```

### synthetic_news_briefs.md

- Broad event summaries (2-3 sentences each)
- No real headlines, no article URLs, no exact dates
- Relative time references only ("recent quarter", "prior year")

### event_timeline.csv

- event_class, relative_period, broad_sector_impact
- No exact calendar dates
- No specific dollar amounts

## Event Classes

1. **demand_shift** — consumer preference changes, market expansion
2. **margin_pressure** — cost inflation, pricing pressure
3. **regulatory_development** — new rules, compliance costs
4. **capital_allocation** — buybacks, dividends, capex
5. **leadership/governance** — management changes, board actions
6. **litigation/legal_risk** — broad legal category only
7. **supply_chain** — input cost, availability
8. **product/category_expansion** — new markets, segments
9. **macro_sensitivity** — interest rates, FX, inflation exposure
10. **financing/liquidity** — debt issuance, refinancing

## Privacy Risks to Mitigate

| Risk | Mitigation |
|------|-----------|
| Unique acquisition name | Replace with "strategic acquisition" |
| Product launch name | Replace with "product category expansion" |
| Litigation party names | Replace with "legal proceeding" |
| Executive quote | Never include direct quotes |
| Exact date | Use relative periods |
| Exact amount | Use magnitude buckets ("material", "significant") |
| Original headline text | Always synthesize; never copy |

## Skeleton Module

```python
# src/fenrix_synthetic/anonymization/news_reconstructor.py
# Deterministic, fixture-only. No network calls.
```

The skeleton module will:
1. Accept a list of broad event classes + relative periods
2. Generate synthetic news briefs deterministically from a seed
3. Output markdown + CSV without real identifiers
4. Include a private audit of event class mappings

## Phase 7 Implementation Details

1. Ingest real news data into private storage only
2. Run NER/classification to extract event classes
3. Map events to relative periods
4. Generate synthetic briefs from event classes
5. Run exact-text attack to verify no real headlines survived
6. Include in professor bundle pipeline as NEWS_RECONSTRUCT stage

## Acceptance Criteria for Phase 7

- [ ] No real headlines in public output
- [ ] No exact article text matches
- [ ] Event classes are broad enough to apply to multiple companies
- [ ] Relative time references only
- [ ] Private audit records real→synthetic mapping
- [ ] Exact-text attack passes
- [ ] Strict release gate passes
