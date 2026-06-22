"""Identity-atlas loader + canonicalization helpers.

Shared by both the masker (``TextAnonymizer``) and the privacy scanner
(``attacks.text_attacks.exact_identity_scan``, the orchestrator's
``_phase_direct_privacy``). The single source of truth for normalizing
private values lives here.

Failure contract:

    ``registry_load.load_atlas`` returns a ``RegistryLoadSummary``
    whose ``status`` is ``'passed'`` or ``'failed'`` and whose
    ``blocking`` flag is True if the run cannot proceed. Callers MUST
    consult ``blocking`` BEFORE writing any public surrogate.

No silent ``ValueError`` drops: per-bad-alias errors are counted in
``load_errors``. Per-empty values are counted in ``skipped_empty``.
Per-duplicate alias IDs are counted in ``duplicates``.

All integer-shaped IDs in YAML (CIKs / accessions / accession-as-int)
are coerced to ``str(..).strip()`` at the loader boundary so the
masker's string-key lookup NEVER misses them. This eliminates the
``add_alias`` silent-drop that previously caused zero aliases to load
on real source-class atlases.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..identity import EntityRegistry
from ..identity.schemas import EntityType, MatchPolicy

logger = logging.getLogger(__name__)

# ── Canonicalization ──────────────────────────────────────────────────

# High-risk entity types whose stored value can be very short
# (single-char ticker like "F" / "T", 1-10 digit CIK, 18-digit
# accession). These BYPASS the default ``min_length`` gate.
_HIGH_RISK_TYPES: frozenset[EntityType] = frozenset(
    {
        EntityType.TICKER,
        EntityType.CIK,
        EntityType.SEC_ACCESSION_NUMBER,
        EntityType.SEC_PRIMARY_DOCUMENT,
    }
)

_DEFAULT_MIN_LENGTH = 3
_COLLAPSE_WHITESPACE_RE = re.compile(r"\s+")

# Prose fragments that the harvester's admission pipeline rejects as
# boilerplate (cf. ``atlas_builder._BLOCKLIST_PERSON_TOKENS``). Older
# source atlases may have them written as curated ``rare_phrase``
# entries by mistake; loading them would feed the post-mask scanner
# exactly the phrases that the masker does NOT replace, producing
# aggressive-but-meaningless post-mask hits. Treat them as if they
# were empty values so the loader's existing fail-closed accounting
# (``skipped_empty``) and the public QA report (``registry_load_report``
# ``skipped_empty`` count) surface the leak without ever propagating it.
#
# Lowercase form is the comparison key: ``or director`` /
# ``Authorized Us`` / ``AUTHORIZED OFFICER`` all land here. Adding
# to this set is intentionally a code change (not a config change)
# so the safety contract lives next to the loader it's protecting.
_LOAD_BLOCKLIST: frozenset[str] = frozenset(
    {
        "or director",
        "authorized us",
        "authorized officer",
        "authorized officer of us",
        "director of the company",
        "chief executive officer",
        "chief executive",
        "vice president",
        "by:",
        "by :",
        "/s/",
    }
)


def _is_load_blocklisted(value: str) -> bool:
    if not value:
        return False
    return value.strip().lower() in _LOAD_BLOCKLIST


def normalize_private_value(
    value: Any,
    *,
    entity_type: EntityType | None = None,
    min_length: int = _DEFAULT_MIN_LENGTH,
    allow_short: bool = False,
) -> str:
    """Canonical form of a private value used by mask AND scan AND QA.

    Behaviour:

    - ``None`` / falsy values return ``""``.
    - ``int`` and ``float`` values are coerced to ``str``.
    - Strings are ``.strip()``-ed and have internal whitespace runs
      collapsed to a single space.
    - Case is preserved (the masker compares exact spelling).
    - ``entity_type in _HIGH_RISK_TYPES`` bypasses the ``min_length``
      gate so a ticker ``"F"`` or a 4-digit CIK is NOT dropped.
    - ``allow_short=True`` ALSO bypasses ``min_length`` — used by the
      atlas loader for ID-shaped fields (``entity_id``,
      ``alias_id``, ``canonical_entity_id``) where short sequential
      integers like ``"1"`` are legitimate. Without this, the loader
      silent-dropped short IDs (the regression that produced 4735
      exact-identity hits on real source runs).
    - All other entity types default to ``min_length=3``; values
      shorter than that return ``""``.

    Returns ``""`` on invalid / empty / too-short values; callers MUST
    treat that as "skip this entry" and not as a zero-byte match.
    """
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        s = str(value)
    elif isinstance(value, str):
        s = value
    else:
        s = str(value)
    s = s.strip()
    if not s:
        return ""
    s = _COLLAPSE_WHITESPACE_RE.sub(" ", s)
    if entity_type not in _HIGH_RISK_TYPES and not allow_short:
        if len(s) < min_length:
            return ""
    return s


def private_value_collision_key(value: str) -> str:
    """Lowercased collision key for scanner equality checks.

    The masker must match the EXACT stored spelling, but the scanner's
    quote-tolerant equality benefits from a case-folded key. Returning
    a separate helper makes the two roles explicit so we don't drift
    the case-handling in one site without the other.

    Returns ``""`` for falsy / whitespace-only inputs.
    """
    if not value:
        return ""
    return value.strip().lower()


# ── Enum resolution (tolerant fallbacks) ─────────────────────────────

_KNOWN_MATCH_POLICIES: dict[str, MatchPolicy] = {
    "literal": MatchPolicy.LITERAL,
    "case_insensitive": MatchPolicy.CASE_INSENSITIVE,
    "ticker_exact": MatchPolicy.TICKER_EXACT,
    "ticker_with_exchange": MatchPolicy.TICKER_WITH_EXCHANGE,
    "ticker_parenthesized": MatchPolicy.TICKER_PARENTHESIZED,
    "cik_padded": MatchPolicy.CIK_PADDED,
    "cik_contextual": MatchPolicy.CIK_CONTEXTUAL,
    "accession_dashed": MatchPolicy.ACCESSION_DASHED,
    "accession_url_form": MatchPolicy.ACCESSION_URL_FORM,
    "domain_full": MatchPolicy.DOMAIN_FULL,
    "domain_email": MatchPolicy.DOMAIN_EMAIL,
    "url_full": MatchPolicy.URL_FULL,
    "possessive": MatchPolicy.POSSESSIVE,
    "punctuation_variant": MatchPolicy.PUNCTUATION_VARIANT,
    "whitespace_variant": MatchPolicy.WHITESPACE_VARIANT,
    "canary": MatchPolicy.CANARY,
}


def _lookup_match_policy(raw: Any) -> MatchPolicy:
    if raw is None:
        return MatchPolicy.LITERAL
    if isinstance(raw, MatchPolicy):
        return raw
    s = str(raw).strip().lower()
    return _KNOWN_MATCH_POLICIES.get(s, MatchPolicy.LITERAL)


def _lookup_entity_type(raw: Any) -> EntityType:
    if raw is None:
        return EntityType.COMPANY
    if isinstance(raw, EntityType):
        return raw
    s = str(raw).strip().lower()
    try:
        return EntityType(s)
    except ValueError:
        return EntityType.COMPANY


# ── Load summary ──────────────────────────────────────────────────────


@dataclass
class RegistryLoadSummary:
    """Per-atlas-load accounting + status flag for fail-closed control.

    Used by ``ReanonymizeOrchestrator`` to write
    ``qa/registry_load_report.json`` and to gate the ``_phase_direct_privacy``
    replacement-rate blocker.
    """

    entities_loaded: int = 0
    aliases_loaded: int = 0
    skipped_empty: int = 0
    duplicates: int = 0
    load_errors: int = 0
    entity_type_breakdown: dict[str, int] = field(default_factory=dict)
    atlas_path: Path | None = None
    redacted_ticker: str = ""

    @property
    def total_attempted(self) -> int:
        # Total entities + aliases that produced a non-skipped,
        # non-errored outcome. Skipped-empty, duplicates, and load
        # errors are tracked separately so operators can see the
        # retry surface; ``total_attempted`` is the "completion" metric.
        return self.entities_loaded + self.aliases_loaded

    @property
    def status(self) -> str:
        if self.aliases_loaded == 0:
            return "failed"
        if self.load_errors > 0:
            return "failed"
        return "passed"

    @property
    def blocking(self) -> bool:
        # Fail-closed on zero aliases OR any load error.
        return self.aliases_loaded == 0 or self.load_errors > 0

    def to_report(self) -> dict[str, Any]:
        """Public QA payload.

        The atlas absolute path is fully REDACTED — only an opaque
        suffix is preserved — so the report cannot leak the per-ticker
        ``private_maps`` directory layout or the YAML filename. The
        absolute path IS logged at WARNING level by the loader so
        operators with access to the run logs can locate the file;
        consumers of this report cannot.
        """
        return {
            "schema_version": "1.0.0",
            "status": self.status,
            "ticker": self.redacted_ticker or "<unknown>",
            "entities_loaded": self.entities_loaded,
            "aliases_loaded": self.aliases_loaded,
            "skipped_empty": self.skipped_empty,
            "duplicates": self.duplicates,
            "load_errors": self.load_errors,
            "total_attempted": self.total_attempted,
            "entity_type_breakdown": dict(self.entity_type_breakdown),
            "atlas_filename": "<redacted-atlas>",
            "blocking": self.blocking,
        }


# ── Atlas loader ──────────────────────────────────────────────────────


def load_atlas(
    atlas_path: Path,
    *,
    ticker: str,
) -> tuple[EntityRegistry | None, RegistryLoadSummary]:
    """Load ``identity_atlas.yaml`` into an ``EntityRegistry``.

    Returns ``(None, summary)`` when the atlas is unreadable or when
    no aliases survive normalization. ``summary.blocking`` is True in
    those cases so the caller can fail-closed BEFORE writing public
    surrogates.

    Coercion rules:

    - Every ``entity_id`` / ``alias_id`` / ``canonical_entity_id``
      and ``*_value`` field is passed through ``normalize_private_value``
      before being added, so YAML's native integer CIKs / accessions
      do NOT trigger ``add_alias`` ValueError drops.
    - Empty / whitespace-only values are counted in ``skipped_empty``.
    - Duplicate alias_ids are counted in ``duplicates`` (the second
      occurrence is ignored).
    - Aliases referencing a missing entity_id are LOGGED at WARNING
      level and counted in ``load_errors``. This is the explicit
      replacement for the silent ``try/except ValueError: pass`` loop
      in the previous ``TextAnonymizer._load_registry``.
    """
    summary = RegistryLoadSummary(atlas_path=atlas_path, redacted_ticker=ticker)

    if not atlas_path.is_file():
        logger.warning("Identity atlas missing for %s at %s", ticker, atlas_path)
        return None, summary

    raw = atlas_path.read_text(encoding="utf-8")
    try:
        atlas_data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.warning("Atlas YAML parse error for %s at %s: %s", ticker, atlas_path, exc)
        return None, summary

    if not isinstance(atlas_data, dict):
        logger.warning("Atlas top-level is not a mapping for %s", ticker)
        return None, summary

    reg_meta = atlas_data.get("metadata", {}) or {}
    registry_id = str(reg_meta.get("registry_id") or f"reg-{ticker}")
    company_id = str(reg_meta.get("company_id") or ticker)
    reg = EntityRegistry.create(
        company_id=company_id, registry_id=registry_id
    )  # Phase 1 — entities.
    seen_entity_ids: set[str] = set()
    # Tracks entity_ids that Phase 1 deliberately dropped via the prose
    # blocklist (Fix 1). Phase 2 uses this set to route alias-side
    # orphans of those dropped entities into ``skipped_empty`` instead
    # of ``load_errors``, so the prose-blocklist guard does not trip
    # the fail-closed ``summary.blocking`` flag through spurious
    # orphan load errors.
    blocklisted_eids: set[str] = set()
    raw_entities = atlas_data.get("entities", []) or []
    for ent in raw_entities:
        if not isinstance(ent, dict):
            summary.skipped_empty += 1
            continue
        # ID-shaped fields bypass ``min_length`` because small sequential
        # ints (e.g. ``"1"``) are legitimate aliases / entity_ids.
        eid = normalize_private_value(ent.get("entity_id"), allow_short=True)
        etype = _lookup_entity_type(ent.get("entity_type"))
        value = normalize_private_value(ent.get("canonical_private_value"), entity_type=etype)
        if not eid or not value:
            summary.skipped_empty += 1
            continue
        # Fix 1 (prose-fragment drop): if the curator wrote a known
        # boilerplate phrase as a curated ``rare_phrase`` entity, treat
        # it as empty so the existing fail-closed accounting surfaces
        # it. Loading the phrase would feed the post-mask scanner
        # exactly the strings the masker does NOT replace, producing
        # aggressive-but-meaningless blocking hits (``or director`` /
        # ``authorized us`` survived into the public body because they
        # were ALSO loaded as private values to scan against).
        if _is_load_blocklisted(value):
            logger.warning(
                "Atlas entity value for %s matches prose blocklist (entity_id=%s); "
                "treating as skipped_empty",
                ticker,
                eid,
            )
            blocklisted_eids.add(eid)
            summary.skipped_empty += 1
            continue
        if eid in seen_entity_ids:
            summary.duplicates += 1
            continue
        seen_entity_ids.add(eid)
        try:
            reg.add_entity(eid, etype, value)
        except Exception:
            logger.exception("add_entity failed for %s entity_id=%s", ticker, eid)
            summary.load_errors += 1
            continue
        summary.entities_loaded += 1
        summary.entity_type_breakdown[etype.value] = (
            summary.entity_type_breakdown.get(etype.value, 0) + 1
        )

    # Phase 2 — aliases.
    seen_alias_ids: set[str] = set()
    raw_aliases = atlas_data.get("aliases", []) or []
    for ali in raw_aliases:
        if not isinstance(ali, dict):
            summary.skipped_empty += 1
            continue
        aid = normalize_private_value(ali.get("alias_id"), allow_short=True)
        eid = normalize_private_value(ali.get("canonical_entity_id"), allow_short=True)
        etype = _lookup_entity_type(ali.get("entity_type"))
        value = normalize_private_value(ali.get("private_alias_value"), entity_type=etype)
        mpolicy = _lookup_match_policy(ali.get("match_policy"))

        if not aid:
            summary.skipped_empty += 1
            continue
        if aid in seen_alias_ids:
            summary.duplicates += 1
            continue
        seen_alias_ids.add(aid)
        if not eid or eid not in reg.entities:
            # Orphan-of-Fix-1 routing: an alias that legitimately
            # points to a prose-blocklisted entity that Phase 1
            # already dropped is silently skipped into
            # ``skipped_empty`` rather than tripped as a structural
            # ``load_error`` (which would fail-closed the run via
            # ``summary.blocking``). A real missing-eid orphan (e.g.
            # a typo in curated YAML that references an entity_id
            # that was never declared) still counts as ``load_errors``
            # so the loader's fail-closed contract is preserved for
            # genuine atlas bugs.
            if eid and eid in blocklisted_eids:
                summary.skipped_empty += 1
                continue
            logger.warning(
                "alias %s for %s references unknown entity_id=%s; skipping",
                aid,
                ticker,
                eid or "<empty>",
            )
            summary.load_errors += 1
            continue
        if not value:
            summary.skipped_empty += 1
            continue
        # Fix 1 (prose-fragment drop): mirror the entity-side guard so
        # a curator who wrote ``or director`` / ``authorized us`` as a
        # ``rare_phrase`` ALIAS also cannot inject boilerplate into
        # the post-mask scanner target list.
        if _is_load_blocklisted(value):
            logger.warning(
                "Atlas alias value for %s matches prose blocklist (alias_id=%s); "
                "treating as skipped_empty",
                ticker,
                aid,
            )
            summary.skipped_empty += 1
            continue
        try:
            reg.add_alias(aid, eid, value, etype, mpolicy)
        except Exception:
            logger.exception("add_alias failed for %s alias_id=%s", ticker, aid)
            summary.load_errors += 1
            continue
        summary.aliases_loaded += 1

    return reg, summary


# ── Scanner private_values payload ────────────────────────────────────


def build_private_values_dict(
    reg: EntityRegistry | None,
    *,
    fallback_ticker: str = "",
) -> dict[str, list[str]]:
    """Build the ``private_values`` dict consumed by ``exact_identity_scan``.

    v2: split entries into per-type buckets so the direct-privacy
    report can produce ``hits_by_type`` taxonomy. Each bucket is
    de-duplicated within itself; cross-bucket dedup happens via a
    single ``seen`` set so the scanner only sees each private value
    once. High-risk entity types (ticker / cik /
    sec_accession_number / sec_primary_document) always bypass the
    min_length gate via ``normalize_private_value`` so ``"F"``-style
    tickers and short CIKs survive.
    """
    # Per-type buckets — values that don't fit a known category fall
    # back to ``"names"`` so existing scanner consumers keep working.
    out: dict[str, list[str]] = {
        "names": [],
        "ticker": [],
        "company_name": [],
        "cik": [],
        "accession": [],
        "person": [],
        "product": [],
        "platform": [],
        "location": [],
        "domain": [],
        "url": [],
        "xbrl_concept": [],
        "rare_phrase": [],
    }
    seen: set[str] = set()

    if fallback_ticker:
        seen.add(fallback_ticker)
        out["ticker"].append(fallback_ticker)
        out["names"].append(fallback_ticker)

    if reg is None:
        return out

    # Map EntityType.values onto the per-type bucket keys used by
    # ``DirectIdentifierAtlasBuilder`` so a downstream scanner report
    # speaks the same taxonomy as the coverage report.
    _ENTITY_TYPE_TO_BUCKET: dict[str, str] = {
        "ticker": "ticker",
        "cik": "cik",
        "sec_accession_number": "accession",
        "sec_primary_document": "accession",
        "company": "company_name",
        "former_company_name": "company_name",
        "executive": "person",
        "board_member": "person",
        "product": "product",
        "brand": "product",
        "proprietary_platform": "platform",
        "facility": "location",
        "headquarters": "location",
        "subsidiary": "company_name",
        "company_domain": "domain",
        "company_email_domain": "domain",
    }

    for ent in reg.all_entities():
        v = normalize_private_value(ent.canonical_private_value, entity_type=ent.entity_type)
        if v and v not in seen:
            seen.add(v)
            bucket = _ENTITY_TYPE_TO_BUCKET.get(ent.entity_type.value, "names")
            out[bucket].append(v)
            out["names"].append(v)
    for ali in reg.all_aliases():
        v = normalize_private_value(ali.private_alias_value, entity_type=ali.entity_type)
        if v and v not in seen:
            seen.add(v)
            bucket = _ENTITY_TYPE_TO_BUCKET.get(ali.entity_type.value, "names")
            out[bucket].append(v)
            out["names"].append(v)
    return out
