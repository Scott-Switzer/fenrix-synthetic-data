"""Peer-archetype anonymization: k-peer privacy scoring and archetype matching.

Implements Phase 4 of V3: each anonymized company must be plausibly
consistent with at least 5 public-company peers. The real source
company must not be uniquely implied by sector, scale, ratios,
trajectory, or business model.

This module provides:
- Typed models for peer candidates and archetype profiles
- An archetype taxonomy with coarse, non-identifying feature buckets
- ``k_peer`` computation and source-not-top-k risk evaluation
- Public/private output separation
- Deterministic scoring under a configurable seed
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# ── Archetype taxonomy ────────────────────────────────────────────────

ARCHETYPE_TAXONOMY: dict[str, dict[str, Any]] = {
    "consumer_defensive_multiline": {
        "public_label": "Consumer Defensive — Multi-line",
        "description": "Diversified consumer staples / defensive company with multiple product lines.",
        "allowed_sectors": ["Consumer Defensive", "Consumer Staples"],
        "feature_expectations": {
            "revenue_bucket": ["LARGE", "MEDIUM"],
            "profitability_bucket": ["HIGH", "MEDIUM"],
            "growth_bucket": ["LOW", "MODERATE"],
        },
        "forbidden_naming_cues": ["specific brand list", "product names"],
        "default_peer_minimum": 5,
    },
    "digital_platform_services": {
        "public_label": "Digital Platform Services",
        "description": "Asset-light digital platform or marketplace company.",
        "allowed_sectors": ["Technology", "Communication Services", "Consumer Cyclical"],
        "feature_expectations": {
            "asset_intensity_bucket": ["LOW"],
            "profitability_bucket": ["HIGH", "MEDIUM"],
            "growth_bucket": ["HIGH", "VERY_HIGH"],
            "leverage_bucket": ["LOW"],
        },
        "forbidden_naming_cues": ["platform name", "app names"],
        "default_peer_minimum": 5,
    },
    "regional_financial_institution": {
        "public_label": "Regional Financial Institution",
        "description": "Regional or community-focused financial services company.",
        "allowed_sectors": ["Financial Services", "Banking"],
        "feature_expectations": {
            "asset_intensity_bucket": ["HIGH"],
            "profitability_bucket": ["MEDIUM", "LOW"],
            "growth_bucket": ["LOW", "MODERATE"],
            "leverage_bucket": ["MODERATE", "HIGH"],
        },
        "forbidden_naming_cues": ["bank name", "branch count"],
        "default_peer_minimum": 5,
    },
    "industrial_distribution_services": {
        "public_label": "Industrial Distribution & Services",
        "description": "Industrial distribution, logistics, or business services company.",
        "allowed_sectors": ["Industrials", "Basic Materials"],
        "feature_expectations": {
            "asset_intensity_bucket": ["MEDIUM", "HIGH"],
            "profitability_bucket": ["LOW", "MEDIUM"],
            "growth_bucket": ["LOW", "MODERATE"],
            "leverage_bucket": ["MEDIUM", "MODERATE"],
        },
        "forbidden_naming_cues": ["distribution center count", "fleet size"],
        "default_peer_minimum": 5,
    },
    "large_scale_technology_services": {
        "public_label": "Large-Scale Technology Services",
        "description": "Large-scale technology services or enterprise software company.",
        "allowed_sectors": ["Technology", "Communication Services"],
        "feature_expectations": {
            "asset_intensity_bucket": ["LOW", "MODERATE"],
            "revenue_bucket": ["LARGE", "MEDIUM"],
            "growth_bucket": ["HIGH", "MODERATE"],
            "leverage_bucket": ["LOW"],
        },
        "forbidden_naming_cues": ["specific product names", "patent counts"],
        "default_peer_minimum": 5,
    },
    "consumer_discretionary_retail": {
        "public_label": "Consumer Discretionary — Retail",
        "description": "Consumer discretionary retail or e-commerce company.",
        "allowed_sectors": ["Consumer Cyclical", "Consumer Discretionary"],
        "feature_expectations": {
            "revenue_bucket": ["LARGE", "MEDIUM"],
            "profitability_bucket": ["MEDIUM", "LOW"],
            "growth_bucket": ["HIGH", "MODERATE"],
            "leverage_bucket": ["LOW", "MODERATE"],
        },
        "forbidden_naming_cues": ["store count", "e-commerce platform name"],
        "default_peer_minimum": 5,
    },
    "regulated_consumer_products": {
        "public_label": "Regulated Consumer Products",
        "description": "Heavily regulated consumer products company.",
        "allowed_sectors": ["Consumer Defensive", "Healthcare"],
        "feature_expectations": {
            "revenue_bucket": ["LARGE", "MEDIUM"],
            "profitability_bucket": ["HIGH", "MEDIUM"],
            "growth_bucket": ["LOW", "MODERATE"],
            "leverage_bucket": ["LOW", "MODERATE"],
        },
        "forbidden_naming_cues": ["FDA filing references", "specific drug names"],
        "default_peer_minimum": 5,
    },
    "institutional_financial_services": {
        "public_label": "Institutional Financial Services",
        "description": "Institutional or wholesale financial services company.",
        "allowed_sectors": ["Financial Services", "Banking", "Insurance"],
        "feature_expectations": {
            "asset_intensity_bucket": ["HIGH", "MODERATE"],
            "revenue_bucket": ["LARGE"],
            "profitability_bucket": ["HIGH", "MEDIUM"],
            "growth_bucket": ["LOW", "MODERATE"],
        },
        "forbidden_naming_cues": ["AUM", "fund names", "specific deal sizes"],
        "default_peer_minimum": 5,
    },
}

# Feature buckets allowed in public archetype matching.
# These are broad, coarse features that do NOT directly identify a company.
ALLOWED_PUBLIC_FEATURES = {
    "broad_sector",
    "archetype",
    "revenue_bucket",
    "asset_intensity_bucket",
    "profitability_bucket",
    "leverage_bucket",
    "growth_bucket",
    "cash_intensity_bucket",
    "cyclicality_bucket",
}

# Forbidden features — must never appear in public archetype output.
FORBIDDEN_PUBLIC_FEATURES = {
    "exact_ticker",
    "exact_company_name",
    "CIK",
    "exact_revenue",
    "exact_market_cap",
    "exact_fiscal_year_end",
    "exact_segment_names",
    "exact_product_names",
    "exact_geography_footprint",
    "exact_executive_names",
    "exact_store_count",
    "exact_subscriber_count",
    "exact_debt_note_labels",
    "exact_acquisition_names",
    "exact_litigation_names",
    "exact_daily_price_path",
}

# Default privacy thresholds.
DEFAULT_MIN_K_PEER = 5
DEFAULT_WARN_K_PEER = 8
DEFAULT_FAIL_IF_SOURCE_TOP_1 = True
DEFAULT_FAIL_IF_SOURCE_TOP_3 = True
DEFAULT_WARN_IF_SOURCE_TOP_5 = True
DEFAULT_MAX_SIMILARITY_SCORE = 1.0
DEFAULT_PEER_SIMILARITY_THRESHOLD = 0.5


# ── Models ─────────────────────────────────────────────────────────────


class PeerCandidate(BaseModel):
    """A candidate peer company for archetype matching."""

    candidate_id: str
    broad_sector: str
    archetype: str
    revenue_bucket: str = ""
    asset_intensity_bucket: str = ""
    profitability_bucket: str = ""
    leverage_bucket: str = ""
    growth_bucket: str = ""
    similarity_score: float = 0.0
    risk_notes: list[str] = Field(default_factory=list)


class PeerArchetypeProfile(BaseModel):
    """The archetype profile for an anonymized company."""

    anonymized_company_id: str
    broad_sector: str
    archetype: str
    k_peer: int
    peer_candidates: list[PeerCandidate]
    selected_peer_mix: list[str]
    source_rank: int | None = None
    source_in_top_1: bool = False
    source_in_top_3: bool = False
    source_in_top_5: bool = False
    passes_peer_privacy: bool = False
    warnings: list[str] = Field(default_factory=list)


# ── Core scoring functions ─────────────────────────────────────────────


def assign_company_archetype(
    broad_sector: str,
    feature_buckets: dict[str, str] | None = None,
) -> str:
    """Assign a company to the best-matching archetype from the taxonomy.

    Matches on ``broad_sector`` first, then checks feature expectations.
    If no archetype is a clear match, returns ``"unclassified"``.
    """
    if feature_buckets is None:
        feature_buckets = {}

    candidates: list[tuple[str, int]] = []
    for archetype_key, archetype_def in ARCHETYPE_TAXONOMY.items():
        score = 0
        allowed = archetype_def.get("allowed_sectors", [])
        if broad_sector in allowed:
            score += 3

        expectations = archetype_def.get("feature_expectations", {})
        for feat, allowed_values in expectations.items():
            actual = feature_buckets.get(feat, "")
            if actual in allowed_values:
                score += 1

        if score > 0:
            candidates.append((archetype_key, score))

    if not candidates:
        return "unclassified"

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _feature_distance(
    source: dict[str, str],
    candidate: dict[str, str],
) -> float:
    """Compute normalized feature distance between source and candidate.

    Returns 1.0 for exact match, 0.0 for no overlap.
    Only uses allowed public features.
    """
    matches = 0
    total = 0
    for feat in ALLOWED_PUBLIC_FEATURES:
        sv = source.get(feat, "")
        cv = candidate.get(feat, "")
        if sv == "" and cv == "":
            continue
        total += 1
        if sv == cv:
            matches += 1
    if total == 0:
        return 0.0
    return matches / total


def score_peer_candidates(
    source_features: dict[str, str],
    peer_pool: list[dict[str, Any]],
    *,
    seed: int = 42,
) -> list[PeerCandidate]:
    """Score and rank peer candidates against the source company.

    Uses the ``similarity_score`` from the raw data when available
    (e.g. from a fixture or pre-computed analysis). If absent,
    falls back to feature-distance-based scoring.

    Returns candidates sorted by similarity (highest first).
    """
    scored: list[PeerCandidate] = []
    for raw in peer_pool:
        # Use pre-computed similarity_score if provided in raw data
        raw_score = raw.get("similarity_score")
        if raw_score is not None and isinstance(raw_score, (int, float)):
            # Add deterministic jitter for tiebreaking
            rng = random.Random(f"{seed}:{raw['candidate_id']}")
            jitter = rng.uniform(0.0, 0.0001)
            similarity = float(raw_score) + jitter
        else:
            candidate_feats = {k: str(raw.get(k, "")) for k in ALLOWED_PUBLIC_FEATURES}
            dist = _feature_distance(source_features, candidate_feats)
            rng = random.Random(f"{seed}:{raw['candidate_id']}")
            jitter = rng.uniform(0.0, 0.001)
            similarity = dist + jitter

        scored.append(
            PeerCandidate(
                candidate_id=str(raw["candidate_id"]),
                broad_sector=str(raw.get("broad_sector", "")),
                archetype=str(raw.get("archetype", "")),
                revenue_bucket=str(raw.get("revenue_bucket", "")),
                asset_intensity_bucket=str(raw.get("asset_intensity_bucket", "")),
                profitability_bucket=str(raw.get("profitability_bucket", "")),
                leverage_bucket=str(raw.get("leverage_bucket", "")),
                growth_bucket=str(raw.get("growth_bucket", "")),
                similarity_score=round(similarity, 6),
            )
        )

    scored.sort(key=lambda c: c.similarity_score, reverse=True)
    return scored


def compute_k_peer(
    candidates: list[PeerCandidate],
    source_id: str,
    *,
    similarity_threshold: float = DEFAULT_PEER_SIMILARITY_THRESHOLD,
) -> tuple[int, int | None, bool, bool, bool]:
    """Compute k-peer and source rank among candidates.

    Returns ``(k_peer, source_rank, source_in_top_1, source_in_top_3, source_in_top_5)``.
    A peer counts toward k_peer if its ``similarity_score >= similarity_threshold``.
    The source is excluded from the k_peer count.
    """
    non_source = [c for c in candidates if c.candidate_id != source_id]
    k_peer = len([c for c in non_source if c.similarity_score >= similarity_threshold])

    # Find source rank among all candidates (including source if present)
    source_idx = None
    for rank, c in enumerate(candidates, start=1):
        if c.candidate_id == source_id:
            source_idx = rank
            break

    source_in_top_1 = source_idx is not None and source_idx <= 1
    source_in_top_3 = source_idx is not None and source_idx <= 3
    source_in_top_5 = source_idx is not None and source_idx <= 5

    return k_peer, source_idx, source_in_top_1, source_in_top_3, source_in_top_5


def evaluate_peer_privacy(
    candidates: list[PeerCandidate],
    source_id: str,
    *,
    min_k_peer: int = DEFAULT_MIN_K_PEER,
    warn_k_peer: int = DEFAULT_WARN_K_PEER,
    fail_if_source_top_1: bool = DEFAULT_FAIL_IF_SOURCE_TOP_1,
    fail_if_source_top_3: bool = DEFAULT_FAIL_IF_SOURCE_TOP_3,
    warn_if_source_top_5: bool = DEFAULT_WARN_IF_SOURCE_TOP_5,
    similarity_threshold: float = DEFAULT_PEER_SIMILARITY_THRESHOLD,
) -> tuple[bool, list[str], list[str]]:
    """Evaluate whether the anonymized company passes peer privacy thresholds.

    Returns ``(passes, failures, warnings)``.
    Failures are blocking conditions. Warnings are advisory.
    """
    k_peer, source_rank, in_top_1, in_top_3, in_top_5 = compute_k_peer(
        candidates,
        source_id,
        similarity_threshold=similarity_threshold,
    )

    failures: list[str] = []
    warnings: list[str] = []

    if k_peer < min_k_peer:
        failures.append(f"k_peer={k_peer} < required minimum {min_k_peer}")
    elif k_peer < warn_k_peer:
        warnings.append(f"k_peer={k_peer} < warning threshold {warn_k_peer}")

    if fail_if_source_top_1 and in_top_1:
        failures.append("source ranks in top 1 — unique deanonymization risk")
    if fail_if_source_top_3 and in_top_3:
        failures.append("source ranks in top 3 — high deanonymization risk")
    if warn_if_source_top_5 and in_top_5 and not in_top_3:
        warnings.append(f"source ranks #{source_rank} — within top-5 warning threshold")

    if not failures:
        warnings.append(f"peer privacy check passed: k_peer={k_peer}, source_rank={source_rank}")

    return len(failures) == 0, failures, warnings


def build_peer_archetype_profile(
    source_id: str,
    peer_pool: list[dict[str, Any]],
    *,
    anonymized_company_id: str = "",
    broad_sector: str = "",
    archetype: str = "",
    feature_buckets: dict[str, str] | None = None,
    seed: int = 42,
    select_peer_count: int = 5,
) -> PeerArchetypeProfile:
    """Build a complete peer archetype profile for an anonymized company.

    This is the main entry point. It:
    1. Assigns an archetype if not provided.
    2. Scores and ranks peer candidates.
    3. Computes k_peer and source rank.
    4. Evaluates privacy thresholds.
    5. Selects a deterministic peer mix.
    """
    if feature_buckets is None:
        feature_buckets = {}

    if not broad_sector:
        broad_sector = feature_buckets.get("broad_sector", "")
    if not archetype:
        archetype = assign_company_archetype(broad_sector, feature_buckets)
    if not anonymized_company_id:
        anonymized_company_id = source_id

    source_features = dict(feature_buckets)
    source_features["broad_sector"] = broad_sector
    source_features["archetype"] = archetype

    candidates = score_peer_candidates(source_features, peer_pool, seed=seed)

    k_peer, src_rank, in_top_1, in_top_3, in_top_5 = compute_k_peer(candidates, source_id)

    passes, failures, warn_msgs = evaluate_peer_privacy(candidates, source_id)

    # Select deterministic peer mix
    non_source = [c for c in candidates if c.candidate_id != source_id]
    rng = random.Random(f"{seed}:mix:{source_id}")
    rng.shuffle(non_source)
    selected = non_source[:select_peer_count]
    selected_ids = [c.candidate_id for c in selected]

    return PeerArchetypeProfile(
        anonymized_company_id=anonymized_company_id,
        broad_sector=broad_sector,
        archetype=archetype,
        k_peer=k_peer,
        peer_candidates=candidates,
        selected_peer_mix=selected_ids,
        source_rank=src_rank,
        source_in_top_1=in_top_1,
        source_in_top_3=in_top_3,
        source_in_top_5=in_top_5,
        passes_peer_privacy=passes,
        warnings=warn_msgs,
    )


# ── Private audit output ───────────────────────────────────────────────


def write_private_peer_archetype_audit(
    profile: PeerArchetypeProfile,
    output_dir: Path,
) -> Path:
    """Write the private peer archetype audit report.

    Contains source-aware details. Must NEVER enter public output.
    Written to ``private/qa/peer_archetype_audit.json``.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    audit: dict[str, Any] = {
        "schema_version": "1.0",
        "anonymized_company_id": profile.anonymized_company_id,
        "broad_sector": profile.broad_sector,
        "archetype": profile.archetype,
        "archetype_public_label": ARCHETYPE_TAXONOMY.get(profile.archetype, {}).get(
            "public_label", profile.archetype
        ),
        "k_peer": profile.k_peer,
        "source_rank": profile.source_rank,
        "source_in_top_1": profile.source_in_top_1,
        "source_in_top_3": profile.source_in_top_3,
        "source_in_top_5": profile.source_in_top_5,
        "passes_peer_privacy": profile.passes_peer_privacy,
        "warnings": profile.warnings,
        "selected_peer_mix": profile.selected_peer_mix,
        "peer_candidates": [
            {
                "candidate_id": c.candidate_id,
                "broad_sector": c.broad_sector,
                "archetype": c.archetype,
                "similarity_score": c.similarity_score,
                "risk_notes": c.risk_notes,
            }
            for c in profile.peer_candidates
        ],
        "thresholds_applied": {
            "min_k_peer": DEFAULT_MIN_K_PEER,
            "warn_k_peer": DEFAULT_WARN_K_PEER,
            "fail_if_source_top_1": DEFAULT_FAIL_IF_SOURCE_TOP_1,
            "fail_if_source_top_3": DEFAULT_FAIL_IF_SOURCE_TOP_3,
            "warn_if_source_top_5": DEFAULT_WARN_IF_SOURCE_TOP_5,
        },
    }

    path = output_dir / "peer_archetype_audit.json"
    path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# ── Public archetype card output ───────────────────────────────────────


def write_public_archetype_card(
    profile: PeerArchetypeProfile,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write the public archetype card (JSON + Markdown).

    MUST NOT contain:
    - real ticker, real company name, CIK
    - professor guessed company
    - source rank (if revealing)
    - peer tickers (if too revealing)

    Written to:
    - ``public/anonymized/<ID>/profile/archetype_card.json``
    - ``public/anonymized/<ID>/profile/profile.md``
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    archetype_def = ARCHETYPE_TAXONOMY.get(profile.archetype, {})
    public_label = archetype_def.get("public_label", profile.archetype)
    description = archetype_def.get("description", "No description available.")

    peer_range = (
        f"{profile.k_peer}+ plausible peers"
        if profile.k_peer >= 5
        else f"{profile.k_peer} plausible peers (below target)"
    )

    card: dict[str, Any] = {
        "schema_version": "1.0",
        "anonymized_company_id": profile.anonymized_company_id,
        "archetype_label": public_label,
        "archetype_key": profile.archetype,
        "broad_sector": profile.broad_sector,
        "description": description,
        "peer_range": peer_range,
        "k_peer": profile.k_peer,
        "passes_peer_privacy": profile.passes_peer_privacy,
    }

    card_path = output_dir / "archetype_card.json"
    card_path.write_text(json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Markdown profile
    md_lines = [
        f"# Company Profile: {profile.anonymized_company_id}",
        "",
        f"**Archetype:** {public_label}",
        f"**Broad Sector:** {profile.broad_sector}",
        "",
        description,
        "",
        f"**Peer Group:** {peer_range}",
        "",
        "## Investment-Relevant Traits",
        "",
    ]
    if profile.broad_sector:
        md_lines.append(f"- Operates in the **{profile.broad_sector}** sector.")
    if profile.k_peer >= 5:
        md_lines.append(
            "- Business model is consistent with multiple public-company "
            "peers — not uniquely identifiable."
        )
    else:
        md_lines.append(
            "- **⚠ Limited peer group** — fewer than 5 comparable public companies. "
            "Exercise caution with deanonymization."
        )

    md_lines.append("")
    md_lines.append("---")
    md_lines.append(
        "*This profile was generated using peer-archetype anonymization. "
        "No real company identifiers are present.*"
    )

    md_path = output_dir / "profile.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return card_path, md_path


# ── Fixture loader ─────────────────────────────────────────────────────


def load_peer_universe(
    fixture_path: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Load a peer universe fixture YAML.

    Returns ``(companies_by_source, archetype_defs)``.
    Companies are grouped by their source prefix (SRC_A, SRC_B, etc.).
    """
    import yaml  # local import — yaml is a core dependency

    data = yaml.safe_load(fixture_path.read_text(encoding="utf-8")) or {}
    companies: list[dict[str, Any]] = list(data.get("companies", []))
    archetypes: dict[str, Any] = dict(data.get("archetypes", {}))

    companies_by_source: dict[str, list[dict[str, Any]]] = {}
    current_source: str | None = None
    for c in companies:
        cid = str(c["candidate_id"])
        if cid.startswith("SRC_"):
            current_source = cid
            companies_by_source[current_source] = []
        if current_source is not None:
            companies_by_source[current_source].append(c)

    return companies_by_source, archetypes
