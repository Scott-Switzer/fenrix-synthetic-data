"""Identity-atlas builder for ``reanonymize-run``.

WHY THIS MODULE EXISTS
======================
The previous fail-closed run produced ``aliases_loaded=6`` from a real
NVDA-class atlas. Six aliases cannot cover a single SEC 10-K's leak
surface. The fix is NOT another regex tweak inside the masker — it is a
broader harvest from the run-folder's own metadata so the ``EntityRegistry``
that ``TextAnonymizer`` consumes has real coverage.

Validated-harvesting admission pipeline
========================================
Each candidate identifier goes through ``discovery → classify → admission
validation → atlas insertion``. Rejected candidates NEVER appear in the
public alias set, NEVER participate in the scanner's leak-surface count,
and NEVER inflate the replacement-rate denominator. Counts only are emitted
to ``qa/direct_identifier_rejected_candidates_report.json``.

Person admission requires ALL six rules:

  1. Token count 2–4 (after leading-blocklist strip).
  2. No token in hard blocklist (case-insensitive, including INNER tokens).
  3. Per-token shape: title-case, initial-with-period, or hyphenated title-case.
  4. No lowercase function-word tokens (covered by rule 3 when shape rejects
     lowercase; rule 4 is a tighter belt for "or", "and", …).
  5. No trailing verb from ``_POST_NAME_VERB_TOKENS`` (REJECT, never trim).
  6. Capture must appear near a high-confidence SEC context.

Handle admission requires the handle's alpha projection to share a stem
with a known root (ticker, company token, etc.). Empty risk_stems means
NO handle is admitted — fail-closed against a misconfigured source run.

Required rejects the system must never admit (per user spec):

  - ``or director``
  - ``authorized us``
  - ``authorized officer``
  - ``authorized officer of us``
  - ``director of the company``
  - ``chief executive``

Required accepts the system must admit (per user spec):

  - ``/s/ Jane Q. Smith``
  - ``John Smith, Director``
  - ``Mary A. Johnson, Executive Vice President``
"""

from __future__ import annotations

import collections
import json
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import orjson

from ..anonymization.registry_load import normalize_private_value
from ..identity.schemas import EntityType

logger = logging.getLogger(__name__)


# ── Conservative regex catalogue ───────────────────────────────────────

# SEC accession numbers: 18-digit dashed pattern.
# The dashed form is the canonical SEC convention; the non-dashed form
# is accepted by some filing endpoints. Both MUST be harvestable so
# the masker catches both representations.
_ACCESSION_DASHED_RE = re.compile(r"\b(\d{10}-\d{2}-\d{6})\b")
_ACCESSION_BARE_RE = re.compile(r"\b(\d{18})\b")

# CIK: 1-10 digit numeric; padded to 10 zeros elsewhere in the pipeline.
_CIK_RE = re.compile(r"\b(?:CIK[:#\s]+|cik=)(\d{1,10})\b", re.IGNORECASE)

# XBRL tag names: colon-separated, e.g. ``us-gaap:Revenues``.
_XBRL_TAG_RE = re.compile(r"\b([a-z][a-z0-9_-]*:[A-Z][A-Za-z0-9]*)\b")

# URLs and domains are only added when explicitly present in the
# deterministic source (no aggressive substring matching — phishing
# protection requires this conservative treatment).
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,24}\b")

# Email and phone: very common, very low false-positive risk.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")

# Conservative ``rare phrase`` window: short, distinctive, between
# anchored tokens. False positives should be vanishingly rare because
# the surrounding anchors are deliberate.
_RARE_PHRASE_RE = re.compile(
    r"\b(?:trademark|copyright|registered|patent)\s+(?:of|for)\s+"
    r"([A-Z][A-Za-z0-9 .,'&_-]{3,40})\b",
    re.IGNORECASE,
)


# ── Coverage thresholds (user spec) ───────────────────────────────────

# For NVDA-scale filings the previous "6 aliases" outcome is treated as
# a critical coverage warning. Smaller test atlases are allowed via
# the ``configured_minimum`` override.
DEFAULT_CONFIGURED_MINIMUM = 50
CRITICAL_ALIAS_THRESHOLD = 6


# ── Admission constants (user spec, Fix 1 + Fix 2) ────────────────────

# Hard-blocklist tokens that MUST NEVER appear in an admitted person
# alias regardless of context. The admission predicate lowercases
# tokens before lookup so "Director" / "DIRECTOR" / "director" all
# match. Exactly the 16 entries the user spec requires.
_BLOCKLIST_PERSON_TOKENS: frozenset[str] = frozenset(
    {
        "or",
        "and",
        "of",
        "us",
        "the",
        "authorized",
        "director",
        "officer",
        "executive",
        "chief",
        "president",
        "vice",
        "board",
        "committee",
        "registrant",
        "company",
    }
)

# High-confidence SEC contexts that a person capture MUST appear near.
# 10 entries per user spec — context validates a nearby name without
# itself becoming part of the alias.
_HIGH_CONFIDENCE_CONTEXTS: tuple[str, ...] = (
    "SIGNATURES",
    "/s/",
    "Director",
    "Chief Executive Officer",
    "Chief Financial Officer",
    "President and Chief Executive Officer",
    "Executive Vice President",
    "Senior Vice President",
    "Named Executive Officer",
    "Board of Directors",
)

# Corporate suffixes stripped once before handle stem matching so
# ``@NVIDIACorp`` matches the curated ``nvidia`` stem (and produces
# the four required handle surface variants).
_CORP_SUFFIXES: tuple[str, ...] = (
    "corp",
    "inc",
    "co",
    "holdings",
    "ltd",
    "llc",
)

# Continuation verbs that frequently follow person names in SEC prose.
# Captures like ``Jane Doe Signed`` are REJECTED here rather than
# trimmed — trim was the failure mode in the prior WIP.
_POST_NAME_VERB_TOKENS: frozenset[str] = frozenset(
    {
        "Signed",
        "Led",
        "Filed",
        "Approved",
        "Certifies",
        "Certified",
        "Directs",
        "Manages",
        "Oversees",
        "Served",
        "Joined",
        "Left",
        "Dated",
        "Has",
        "Was",
        "Is",
        "Are",
        "Had",
        "Acknowledged",
        "Attests",
    }
)


# ── RejectionReason enum (driver of qa histogram) ─────────────────────


class RejectionReason(StrEnum):
    """Canonical rejection reasons emitted to the public QA histogram.

    Rejected candidates are NEVER serialized with their raw value —
    only the histogram by reason is emitted to
    ``qa/direct_identifier_rejected_candidates_report.json``.
    """

    HANDLE_NOT_TIED_TO_ROOT = "handle_not_tied_to_root"
    PERSON_TOKEN_COUNT_OUT_OF_RANGE = "person_token_count_out_of_range"
    PERSON_HARD_BLOCKLIST = "person_hard_blocklist"
    PERSON_LOWERCASE_FUNCTION_WORD = "person_lowercase_function_word"
    PERSON_INVALID_TOKEN_SHAPE = "person_invalid_token_shape"
    PERSON_NO_HIGH_CONFIDENCE_CONTEXT = "person_no_high_confidence_context"
    PERSON_TRAILING_VERB = "person_trailing_verb"


# ── Candidate dataclass ───────────────────────────────────────────────


@dataclass
class Candidate:
    """A single identifier candidate from a regex match.

    Internal-use-only — never serialized. ``value`` carries the raw
    match (private), ``rejection_reason`` carries the enum when
    rejected, ``accepted`` flips True on admission, ``context`` is
    the string window around the match (used for context predicates).
    ``kind`` is a string discriminator ("person" / "handle" / …) so
    Candidate can classify without coupling to the EntityType enum
    (HANDLE / PERSON are not first-class schema entity types).
    """

    value: str
    kind: str
    source: str
    context: str = ""
    accepted: bool = False
    rejection_reason: RejectionReason | None = None


# ── AtlasHarvestReport ────────────────────────────────────────────────


@dataclass
class AtlasHarvestReport:
    """Counts of harvested identifiers per type + rejection histogram.

    ``_buckets`` carries the RAW harvested (admitted) values per type
    so the orchestrator's ``_merge_harvest_into_atlas_yaml`` can
    iterate the exact strings. The field is marked ``repr=False`` so
    the default dataclass ``__repr__`` also elides the raw values
    for any debug output.

    ``rejected_count_by_reason`` carries the histogram of rejected
    candidates (counts ONLY — never raw rejection values). Surfaced
    in ``to_report()`` for the release gate to inspect.
    """

    ticker: str = ""
    atlas_sources: dict[str, int] = field(default_factory=dict)
    identifier_types: dict[str, int] = field(default_factory=dict)
    aliases_built: int = 0
    aliases_by_type: dict[str, int] = field(default_factory=dict)
    coverage_warnings: list[dict[str, str]] = field(default_factory=list)
    rejected_count_by_reason: dict[str, int] = field(default_factory=dict)
    # Internal-use-only: the actual (admitted) harvested values per type.
    _buckets: dict[str, set[str]] = field(default_factory=dict, repr=False)

    def to_report(self) -> dict[str, Any]:
        """Public QA payload — NEVER emits raw identifiers."""
        return {
            "schema_version": "1.0.0",
            "ticker": self.ticker or "<unknown>",
            "atlas_sources": dict(self.atlas_sources),
            "identifier_types": dict(self.identifier_types),
            "aliases_built": self.aliases_built,
            "aliases_by_type": dict(self.aliases_by_type),
            "coverage_warnings": list(self.coverage_warnings),
            "critical_warnings_count": sum(
                1 for w in self.coverage_warnings if w.get("level") == "critical"
            ),
            "warning_warnings_count": sum(
                1 for w in self.coverage_warnings if w.get("level") == "warning"
            ),
            "rejected_count_by_reason": dict(self.rejected_count_by_reason),
        }


# ── Builder ────────────────────────────────────────────────────────────


class DirectIdentifierAtlasBuilder:
    """Conservative identifier harvester with validated-harvesting
    admission pipeline.

    Reads deterministic sources first (run_summary, config, atlas YAML,
    filing headers, news metadata, XBRL tags), then derives risk stems
    from the curated company tokens, then admits regex-harvested
    handles + person names through the strict admission predicates.

    The result is sufficient to materialise an ``EntityRegistry`` via
    ``registry_load.build_atlas`` or to feed the orchestrator's
    existing atlas YAML.

    NO raw identifier is ever written to ``coverage_report``; counts
    only, so a downstream consumer cannot reverse-engineer values.
    """

    def __init__(
        self,
        *,
        ticker: str,
        source_run: Path,
        configured_minimum: int = DEFAULT_CONFIGURED_MINIMUM,
    ) -> None:
        self.ticker = ticker.upper()
        self.source_run = source_run
        self.configured_minimum = configured_minimum
        self._buckets: dict[str, set[str]] = collections.defaultdict(set)
        self._sources_seen: collections.Counter[str] = collections.Counter()
        # Rejected-candidates histogram (counts ONLY — never raw values).
        self._rejected_counts: collections.Counter[str] = collections.Counter()
        # Risk-stem set populated by ``_build_risk_stems`` AFTER
        # deterministic harvesters run. Handle admission requires a
        # non-empty ``_risk_stems`` (fail-closed).
        self._risk_stems: set[str] = set()

    # ── Public surface ────────────────────────────────────────────────

    def harvest(self) -> AtlasHarvestReport:
        """Run every harvester. Order is deterministic for stable QA.

        Order matters: deterministic harvesters populate the ticker/
        company buckets BEFORE ``_build_risk_stems`` runs, so the
        handle admission predicate has a non-empty ``_risk_stems``
        set to match against. Person/handle admission runs LAST so
        every earlier bucket has already stabilised.
        """
        self._harvest_run_summary()
        self._harvest_config_files()
        self._harvest_existing_atlas_yaml()
        self._harvest_filing_headers()
        self._harvest_news_metadata()
        self._harvest_xbrl_tags()
        self._build_risk_stems()
        self._harvest_social_handles()
        self._harvest_person_names()
        return self._materialize_report()

    # ── Harvesters (deterministic → regex) ────────────────────────────

    def _harvest_run_summary(self) -> None:
        path = self.source_run / "run_summary.json"
        if not path.is_file():
            return
        try:
            data = orjson.loads(path.read_bytes())
        except orjson.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        # Accept the canonical scalar keys AND the list-of-tickers
        # key the orchestrator / producer code paths write today. The
        # deterministic-source contract holds: every value here comes
        # from the source-run's own run_summary.json — no network,
        # no guessing.
        ticker_raw = data.get("ticker") or data.get("primary_ticker")
        tickers_list = data.get("tickers")
        if not ticker_raw and isinstance(tickers_list, list) and tickers_list:
            first = tickers_list[0]
            if first:
                ticker_raw = first
        if isinstance(tickers_list, list) and len(tickers_list) > 1:
            self._sources_seen["tickers_dropped"] += len(tickers_list) - 1
        if ticker_raw:
            self._buckets["ticker"].add(str(ticker_raw))
        status = data.get("status")
        if isinstance(status, str) and status:
            self._sources_seen[f"run_status_{status}"] += 1
        self._sources_seen["run_summary"] += 1

    def _harvest_config_files(self) -> None:
        config_root = self.source_run / "config"
        if not config_root.is_dir():
            return
        for cfg in sorted(config_root.rglob("*.yaml")):
            try:
                data = orjson.loads((cfg.read_bytes()).replace(b": ", b":").replace(b"- ", b""))
            except orjson.JSONDecodeError:
                continue
            for k in ("ticker", "company_name", "cik", "exchange"):
                v = data.get(k) if isinstance(data, dict) else None
                if v:
                    etype = self._etype_for_key(k)
                    self._buckets[etype].add(str(v))
            self._sources_seen["config_yaml"] += 1

    def _harvest_existing_atlas_yaml(self) -> None:
        """Reviewer-curated atlas always wins; merge forward."""
        path = self.source_run / "private_maps" / self.ticker / "identity_atlas.yaml"
        if not path.is_file():
            return
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Existing atlas YAML unreadable for %s: %s", self.ticker, exc)
            return
        if not isinstance(data, dict):
            return
        for ent in data.get("entities", []) or []:
            etype = self._etype_for_key(str(ent.get("entity_type", "company")))
            value = normalize_private_value(ent.get("canonical_private_value", ""))
            if value:
                self._buckets[etype].add(value)
        for ali in data.get("aliases", []) or []:
            etype = self._etype_for_key(str(ali.get("entity_type", "company")))
            value = normalize_private_value(ali.get("private_alias_value", ""))
            if value:
                self._buckets[etype].add(value)
        self._sources_seen["atlas_yaml"] += 1

    def _harvest_filing_headers(self) -> None:
        """Conservative regex on filing HTMLs for accession + CIK + rare phrases."""
        sec_root = self.source_run / "originals" / self.ticker / "sec" / "filings"
        if not sec_root.is_dir():
            return
        for filing in sorted(sec_root.glob("*")):
            if not filing.is_file():
                continue
            try:
                text = filing.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            head = text[:8192]
            for m in _ACCESSION_DASHED_RE.finditer(head):
                self._buckets["accession"].add(m.group(1))
            for m in _CIK_RE.finditer(head):
                self._buckets["cik"].add(m.group(1))
            for m in _RARE_PHRASE_RE.finditer(head):
                self._buckets["rare_phrase"].add(m.group(1).strip())
            self._sources_seen["filing_headers"] += 1
        all_acc = set(self._buckets.get("accession", set()))
        for acc in list(all_acc):
            self._buckets["accession"].add(acc.replace("-", ""))

    def _harvest_news_metadata(self) -> None:
        """Publisher names + canonical URLs from news articles JSON."""
        news_root = self.source_run / "originals" / self.ticker / "news" / "articles.json"
        if not news_root.is_file():
            return
        try:
            data = orjson.loads(news_root.read_bytes())
        except orjson.JSONDecodeError:
            return
        if not isinstance(data, list):
            return
        for article in data:
            if not isinstance(article, dict):
                continue
            publisher = article.get("publisher")
            if isinstance(publisher, str) and publisher.strip():
                self._buckets["brand"].add(publisher.strip())
            url = article.get("canonical_url")
            if isinstance(url, str) and url.strip():
                self._buckets["url"].add(url.strip())
            headline = article.get("headline")
            if isinstance(headline, str) and len(headline.strip()) > 20:
                snippets = headline.strip().split(",")
                if snippets:
                    self._buckets["rare_phrase"].add(snippets[0][:60].strip())
            self._sources_seen["news_metadata"] += 1

    def _harvest_xbrl_tags(self) -> None:
        """Custom XBRL tags encountered in filings become aliases."""
        sec_root = self.source_run / "originals" / self.ticker / "sec" / "filings"
        if not sec_root.is_dir():
            return
        tags_seen: set[str] = set()
        for filing in sorted(sec_root.glob("*")):
            if not filing.is_file():
                continue
            try:
                text = filing.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            xbrl_section = text[:65536]
            for m in _XBRL_TAG_RE.finditer(xbrl_section):
                tag = m.group(1)
                if tag.startswith(("us-gaap:", "dei:", "xbrl:", "ifrs-full:")):
                    continue
                tags_seen.add(tag)
            self._sources_seen["xbrl_tags"] += 1
        for t in tags_seen:
            self._buckets["xbrl_concept"].add(t)

    # ── Risk-stem derivation (Fix 2) ──────────────────────────────────

    def _build_risk_stems(self) -> None:
        """Populate ``_risk_stems`` from ticker + curated company tokens.

        Runs AFTER the deterministic harvesters so ticker + company
        buckets are populated. A handle candidate is admitted ONLY if
        its alpha projection shares a stem with this set — fail-closed
        when the source run is misconfigured and risk_stems is empty.
        """
        stems: set[str] = set()
        for token in self._buckets.get("ticker", set()):
            if isinstance(token, str) and token.strip():
                stems.add(token.strip().lower())
        for company in self._buckets.get("company", set()):
            if not isinstance(company, str) or not company.strip():
                continue
            for tok in re.split(r"[^A-Za-z0-9]+", company):
                if len(tok) > 2:
                    stems.add(tok.lower())
        self._risk_stems = stems

    # ── Handle harvester (Fix 2) ──────────────────────────────────────

    _HANDLE_RE = re.compile(r"@([A-Za-z0-9_]+)")

    def _harvest_social_handles(self) -> None:
        """Strict-admission social-handle harvester.

        For every ``@<X>`` token in news articles + SEC filings
        (first 8192 chars per filing), wrap as a Candidate and run
        the strict ``_admit_handle`` predicate. Admitted handles
        land in the ``handle`` bucket with the four required surface
        variants. Rejected candidates accrue in
        ``self._rejected_counts[HANDLE_NOT_TIED_TO_ROOT]`` and
        NEVER affect the scanner's leak-surface count.
        """
        sec_root = self.source_run / "originals" / self.ticker / "sec" / "filings"
        if sec_root.is_dir():
            for filing in sorted(sec_root.glob("*")):
                if filing.is_file() and filing.suffix.lower() in (".html", ".htm"):
                    try:
                        text = filing.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    self._scan_handle_text(text[:8192], source="filing")
        news_root = self.source_run / "originals" / self.ticker / "news" / "articles.json"
        if news_root.is_file():
            try:
                data = orjson.loads(news_root.read_bytes())
            except orjson.JSONDecodeError:
                data = None
            if isinstance(data, list):
                for article in data:
                    if not isinstance(article, dict):
                        continue
                    for field in ("publisher", "canonical_url", "body"):
                        val = article.get(field)
                        if isinstance(val, str):
                            self._scan_handle_text(val, source="news")
        # Diagnostic count (after both passes) so ``atlas_sources``
        # surfaces admit vs. candidate volume for QA.
        admitted_handles = len(self._buckets.get("handle", set()))
        if admitted_handles:
            self._sources_seen["social_handles"] = admitted_handles

    def _scan_handle_text(self, text: str, source: str) -> None:
        """Wrap each ``@<X>`` as a Candidate and run admission."""
        for m in self._HANDLE_RE.finditer(text):
            handle = m.group(1)
            cand = Candidate(
                value=handle,
                kind="handle",
                source=source,
                context=text[max(0, m.start() - 40) : m.end() + 40],
            )
            if self._admit_handle(cand):
                # Surface variants per user spec: ``@<Handle>`` /
                # ``<Handle>`` / ``/@<Handle>`` / ``flipboard.com/@<Handle>``.
                self._buckets["handle"].update(
                    {
                        f"@{handle}",
                        handle,
                        handle.lower(),
                        f"/@{handle}",
                        f"flipboard.com/@{handle}",
                    }
                )
                cand.accepted = True
            else:
                if cand.rejection_reason is not None:
                    self._rejected_counts[cand.rejection_reason.value] += 1

    def _admit_handle(self, candidate: Candidate) -> bool:
        """Admit a handle ONLY if it shares a stem with a known root.

        Three projections of the handle are matched against the curated
        ``_risk_stems``:

        - ``h_low``      : full lower-cased original
        - ``h_alpha``    : alphanumeric-only (strips ``.``, ``/``, etc.)
        - ``h_stripped`` : alpha-only with one trailing corporate suffix
                           removed (``corp`` / ``inc`` / ``co`` /
                           ``holdings`` / ``ltd`` / ``llc``)

        Empty risk_stems means NO handle is admitted (fail-closed).
        """
        if not self._risk_stems:
            candidate.rejection_reason = RejectionReason.HANDLE_NOT_TIED_TO_ROOT
            return False
        handle = candidate.value
        if not handle or len(handle) <= 2:
            candidate.rejection_reason = RejectionReason.HANDLE_NOT_TIED_TO_ROOT
            return False
        h_low = handle.lower()
        h_alpha = re.sub(r"[^a-z0-9]", "", h_low)
        h_stripped = h_alpha
        for suf in _CORP_SUFFIXES:
            if h_stripped.endswith(suf) and len(h_stripped) > len(suf) + 2:
                h_stripped = h_stripped[: -len(suf)]
                break
        for stem in self._risk_stems:
            if len(stem) > 2 and (stem in h_low or stem in h_alpha or stem in h_stripped):
                return True
        candidate.rejection_reason = RejectionReason.HANDLE_NOT_TIED_TO_ROOT
        return False

    # ── Person-name harvester (Fix 1) ─────────────────────────────────

    # Strict title-case person-name capture. Each token must be
    # either ``[A-Z][a-z]+`` (titlecase, ≥ 2 chars) or ``[A-Z]\.``
    # (initial WITH a period, e.g. ``Q.`` / ``J.``). The optional
    # period was relaxed from ``\.\?`` to mandatory ``\.`` to
    # eliminate the pathological capture of standalone prose
    # capitals like ``I`` / ``A`` (these were producing 100k+ bogus
    # ``person_invalid_token_shape`` rejections in real NVDA beta).
    # Real SEC initials always carry the period so the constraint is
    # realistic; full titlecase words still match via the first
    # alternation. Boilerplate like "or director" / "authorized
    # officer" has no titlecase tokens so it remains uncaptured.
    #
    # Token separator is ``[ \t]+`` (NOT ``\s+``) so the capture does
    # not bridge across newlines and consume the next prose paragraph.
    _PERSON_NAME_RE = re.compile(
        r"(?P<name>(?:[A-Z][a-z]+|[A-Z]\.)(?:[ \t]+(?:[A-Z][a-z]+|[A-Z]\.)){1,3})"
    )

    # SEC signature-page convention ``/s/<Name>``. Same positive capture
    # shape. The left-boundary ``(?<!\w)/s/`` prevents ``/s/`` from
    # matching inside URLs.
    _SIGNATURE_SLASH_S_RE = re.compile(
        r"(?<!\w)/s/[ \t]*(?P<name>(?:[A-Z][a-z]+|[A-Z]\.)(?:[ \t]+(?:[A-Z][a-z]+|[A-Z]\.)){1,3})"
    )

    def _harvest_person_names(self) -> None:
        """Strict-admission person-name harvester.

        Scans the WHOLE filing text (NOT just a 8192-byte header) so
        signature-page blocks are reachable. Every ``_PERSON_NAME_RE``
        and ``_SIGNATURE_SLASH_S_RE`` match becomes a Candidate and
        must pass the strict six-rule admission.
        """
        sec_root = self.source_run / "originals" / self.ticker / "sec" / "filings"
        if not sec_root.is_dir():
            return
        admitted_count = 0
        for filing in sorted(sec_root.glob("*")):
            if not filing.is_file() or filing.suffix.lower() not in (".html", ".htm"):
                continue
            try:
                text = filing.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            admitted_count += self._scan_person_text(text, source="filing")
        if admitted_count:
            self._sources_seen["person_names"] = admitted_count

    def _scan_person_text(self, text: str, source: str) -> int:
        """Apply both person-name regexes against ``text`` and admit per hit."""
        accepted = 0
        # Combine the two regex pass results so multiple captures are all
        # admitted-or-rejected through the same predicate.
        seen_spans: set[tuple[int, int]] = set()
        for regex in (self._SIGNATURE_SLASH_S_RE, self._PERSON_NAME_RE):
            for m in regex.finditer(text):
                span = (m.start("name"), m.end("name"))
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                name = m.group("name").strip()
                context_window = text[max(0, m.start() - 60) : m.end() + 60]
                cand = Candidate(
                    value=name,
                    kind="person",
                    source=source,
                    context=context_window,
                )
                if self._admit_person(cand, context_window):
                    # Strip leading blocklist tokens from candidate.value
                    # so the bucket never receives "Director John Doe"
                    # when only "John Doe" is the real name (Fix 3).
                    self._buckets["person"].add(cand.value)
                    cand.accepted = True
                    accepted += 1
                else:
                    if cand.rejection_reason is not None:
                        self._rejected_counts[cand.rejection_reason.value] += 1
        return accepted

    @staticmethod
    def _is_blocklist_token(token: str) -> bool:
        """Case-insensitive blocklist check (handles ``Q.`` → ``q``)."""
        return token.lower().rstrip(".") in _BLOCKLIST_PERSON_TOKENS

    @staticmethod
    def _is_valid_token_shape(token: str) -> bool:
        """Per-rule-3: title-case name | initial-with-period |
        hyphenated title-case name.

        ``NVDA`` (all-uppercase) and ``foo`` (lowercase) both fail.
        Case-insensitive blocklist (``Q.`` → ``q``) is checked
        separately by ``_is_blocklist_token``.
        """
        if not token:
            return False
        if len(token) == 2 and token[0].isupper() and token[1] == ".":
            return True
        if token[0].isupper() and any(c.islower() for c in token[1:]):
            segments = token.split("-")
            if all(
                seg
                and seg[0].isupper()
                and any(c.islower() for c in seg[1:])
                and all(c.isalpha() for c in seg)
                for seg in segments
            ):
                return True
        return False

    def _admit_person(self, candidate: Candidate, context_window: str) -> bool:
        """Apply the strict six-rule admission policy.

        1. Token count 2–4 (after leading-blocklist strip).
        2. No remaining token in the hard blocklist (rule 2 + 3
           combined — inner-blocklist check rejects mid-list title
           words too like ``Jane Q. Smith Director``).
        3. Per-token shape: title-case / initial-with-period /
           hyphenated title-case (rule 3 covers rule 4 inherently
           since shape rejects bare lowercase).
        4. No trailing verb from ``_POST_NAME_VERB_TOKENS`` (rule 5
           enforced as REJECT, never trim).
        5. Capture must appear near a high-confidence context
           (rule 6, defense belt).

        Per the user spec ("titles are context, not aliases. Director
        helps validate a nearby name; it must never become part of
        the alias") we strip ANY number of leading blocklist tokens
        before applying the remaining checks. INNER blocklist tokens
        that survive the strip are still rejected. After all rules
        pass we update ``candidate.value`` to the stripped form so
        the bucket never receives ``Director Jane Doe``.
        """
        original_tokens = candidate.value.split()
        if not original_tokens:
            candidate.rejection_reason = RejectionReason.PERSON_HARD_BLOCKLIST
            return False
        tokens = list(original_tokens)
        # Strip leading blocklist tokens (one per iteration).
        while tokens and self._is_blocklist_token(tokens[0]):
            tokens.pop(0)
        if not tokens:
            # Whole capture was blocklist boilerplate ("or director",
            # "authorized us", "authorized officer of us", …).
            candidate.rejection_reason = RejectionReason.PERSON_HARD_BLOCKLIST
            return False
        # Rule 1: token count 2-4 of REMAINING tokens (post-strip).
        if len(tokens) < 2 or len(tokens) > 4:
            candidate.rejection_reason = RejectionReason.PERSON_TOKEN_COUNT_OUT_OF_RANGE
            return False
        # Rules 2 + 3 + 4: per-token shape + INNER blocklist + lowercase.
        for token in tokens:
            if self._is_blocklist_token(token):
                candidate.rejection_reason = RejectionReason.PERSON_HARD_BLOCKLIST
                return False
            if token.islower() and token not in _BLOCKLIST_PERSON_TOKENS:
                # Lowercase FUNCTION word (anything lowercase that isn't
                # already a known blocklist token — broad belt).
                candidate.rejection_reason = RejectionReason.PERSON_LOWERCASE_FUNCTION_WORD
                return False
            if not self._is_valid_token_shape(token):
                candidate.rejection_reason = RejectionReason.PERSON_INVALID_TOKEN_SHAPE
                return False
        # Rule 5: trailing verb — REJECT, never trim.
        last_token_title = tokens[-1].capitalize().rstrip(".")
        if last_token_title in _POST_NAME_VERB_TOKENS:
            candidate.rejection_reason = RejectionReason.PERSON_TRAILING_VERB
            return False
        # Rule 6: high-confidence context (defense belt).
        ctx_low = context_window.lower()
        if not any(ctx.lower() in ctx_low for ctx in _HIGH_CONFIDENCE_CONTEXTS):
            candidate.rejection_reason = RejectionReason.PERSON_NO_HIGH_CONFIDENCE_CONTEXT
            return False
        # Update candidate's stored value to the stripped form so the
        # emitted alias NEVER contains a leading title token.
        candidate.value = " ".join(tokens)
        return True

    # ── Report materialisation ────────────────────────────────────────

    def _materialize_report(self) -> AtlasHarvestReport:
        report = AtlasHarvestReport(ticker=self.ticker)
        report.atlas_sources = dict(self._sources_seen)
        report._buckets = {etype: set(values) for etype, values in self._buckets.items()}
        for etype, values in self._buckets.items():
            report.identifier_types[etype] = len(values)
            report.aliases_by_type[etype] = len(values)
        report.aliases_built = sum(len(v) for v in self._buckets.values())
        report.rejected_count_by_reason = dict(self._rejected_counts)

        # Coverage warnings.
        if report.aliases_built == 0:
            report.coverage_warnings.append(
                {
                    "level": "critical",
                    "code": "no_aliases_built",
                    "msg": "Atlas builder produced zero aliases; masker cannot run.",
                }
            )
        elif report.aliases_built <= CRITICAL_ALIAS_THRESHOLD:
            report.coverage_warnings.append(
                {
                    "level": "critical",
                    "code": "below_critical_threshold",
                    "msg": (
                        f"Only {report.aliases_built} aliases built. That's below the "
                        f"critical threshold ({CRITICAL_ALIAS_THRESHOLD}) for NVDA-scale "
                        "filings; direct privacy is likely to fail."
                    ),
                }
            )
        elif report.aliases_built < self.configured_minimum:
            report.coverage_warnings.append(
                {
                    "level": "warning",
                    "code": "below_configured_minimum",
                    "msg": (
                        f"Built {report.aliases_built} aliases; configured minimum is "
                        f"{self.configured_minimum}. Consider harvesting more sources."
                    ),
                }
            )

        for required in ("ticker",):
            if report.identifier_types.get(required, 0) == 0:
                report.coverage_warnings.append(
                    {
                        "level": "critical",
                        "code": "missing_required_type",
                        "msg": f"Required identifier type '{required}' is missing.",
                    }
                )
        return report

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _etype_for_key(key: str) -> str:
        """Map a YAML/config key string to a known EntityType value."""
        clean = key.strip().lower()
        try:
            return EntityType(clean).value
        except ValueError:
            # Manual mapping for non-enum-value key strings.
            mapping = {
                "company_name": EntityType.COMPANY.value,
                "company": EntityType.COMPANY.value,
                "ticker": EntityType.TICKER.value,
                "exchange": EntityType.TICKER.value,
                "cik": EntityType.CIK.value,
                "accession": EntityType.SEC_ACCESSION_NUMBER.value,
                "executives": EntityType.EXECUTIVE.value,
                "directors": EntityType.BOARD_MEMBER.value,
                "products": EntityType.PRODUCT.value,
                "platforms": EntityType.PROPRIETARY_PLATFORM.value,
                "domains": EntityType.COMPANY_DOMAIN.value,
                "subsidiaries": EntityType.SUBSIDIARY.value,
            }
            return mapping.get(clean, EntityType.COMPANY.value)


# ── Public IO helpers ──────────────────────────────────────────────────


def write_coverage_report(report: AtlasHarvestReport, dest: Path) -> None:
    """Write the coverage report to disk (no raw identifiers, ever)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report.to_report(), indent=2, sort_keys=True), encoding="utf-8")


def write_rejected_candidates_report(report: AtlasHarvestReport, dest: Path) -> None:
    """Write the rejected-candidates histogram to disk (counts only).

    Reads ``report.rejected_count_by_reason`` and serialises ONLY the
    histogram. No candidate values, contexts, or sources — those are
    private by definition and may not appear in public QA. The
    orchestrator calls this immediately after ``write_coverage_report``
    so a downstream consumer can scan both in parallel for QA.

    Schema is always-on: when zero rejections occur, the file is still
    written with ``rejected_total == 0`` and ``by_reason == {}``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0.0",
        "ticker": report.ticker or "<unknown>",
        "rejected_total": sum(report.rejected_count_by_reason.values()),
        "by_reason": dict(report.rejected_count_by_reason),
    }
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
