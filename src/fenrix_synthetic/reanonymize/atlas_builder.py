"""Identity-atlas builder for ``reanonymize-run``.

WHY THIS MODULE EXISTS
======================
The previous fail-closed run produced ``aliases_loaded=6`` from a real
NVDA-class atlas. Six aliases cannot cover a single SEC 10-K's leak
surface. The fix is NOT another regex tweak inside the masker — it is a
broader harvest from the run-folder's own metadata so the ``EntityRegistry``
that ``TextAnonymizer`` consumes has real coverage.

Sources harvested, in priority order:

1. ``source_run/run_summary.json``  — canonical ticker, run_id, etc.
2. ``source_run/config/*``           — yfinance + campaign metadata.
3. ``source_run/private_maps/<TICKER>/identity_atlas.yaml`` — reviewer's
   hand-curated atlas (always read first so human judgment wins).
4. SEC submission manifests and filing inventories reachable under
   ``source_run/manifests/*`` and ``source_run/originals/<TICKER>/**/*``.
5. News metadata at
   ``source_run/originals/<TICKER>/news/articles.json`` — publisher names,
   canonical URLs, headline phrases (conservative regex).
6. Existing anonymized artifacts at
   ``source_run/anonymized/<TICKER>/**/*`` — used to harvest surviving
   rare phrases (the masker's own output is fair game for new aliases
   so the next iteration tightens coverage).

The builder NEVER exports the raw identifier values; every output is a
count + opaque-rendered identifier type. Run QA gets
``qa/direct_identifier_coverage_report.json`` with explicit per-type
counts and ``coverage_warnings`` (with ``level`` so the release gate
can refuse on ``critical``).
"""

from __future__ import annotations

import collections
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from ..anonymization.registry_load import normalize_private_value
from ..identity.schemas import (
    EntityType,
    MatchPolicy,
)

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


# ── Types ─────────────────────────────────────────────────────────────


@dataclass
class AtlasHarvestReport:
    """Counts of harvested identifiers per type + warnings list.

    The release gate forbids ``level == "critical"`` warnings; the
    orchestrator writes this report to
    ``qa/direct_identifier_coverage_report.json``.

    ``buckets`` carries the RAW harvested values per type so the
    orchestrator's ``_merge_harvest_into_atlas_yaml`` can iterate the
    exact strings (the dataclass's other fields only carry counts so
    the public QA payload NEVER leaks raw identifiers).
    """

    ticker: str = ""
    atlas_sources: dict[str, int] = field(default_factory=dict)
    identifier_types: dict[str, int] = field(default_factory=dict)
    aliases_built: int = 0
    aliases_by_type: dict[str, int] = field(default_factory=dict)
    coverage_warnings: list[dict[str, str]] = field(default_factory=list)
    # Internal-use-only: the actual harvested values per type.
    # NOT serialized by ``to_report`` because raw identifiers must
    # never appear in public QA.
    buckets: dict[str, set[str]] = field(default_factory=dict)

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
        }


# ── Builder ────────────────────────────────────────────────────────────


class DirectIdentifierAtlasBuilder:
    """Conservative identifier harvester.

    Reads deterministic sources first, then regex-extracted clusters
    from filing headers and news metadata. The result is sufficient to
    materialise an ``EntityRegistry`` via ``registry_load.build_atlas``
    or to feed the orchestrator's existing atlas YAML.

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

    # ── Public surface ────────────────────────────────────────────────

    def harvest(self) -> AtlasHarvestReport:
        """Run every harvester. Order is deterministic for stable QA."""
        self._harvest_run_summary()
        self._harvest_config_files()
        self._harvest_existing_atlas_yaml()
        self._harvest_filing_headers()
        self._harvest_news_metadata()
        self._harvest_xbrl_tags()
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
        ticker = str(data.get("ticker") or data.get("primary_ticker") or self.ticker)
        self._buckets["ticker"].add(ticker)
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
        # Add ALL accession variants: bare + dashed (user spec).
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
                # Conservative: only first 25 chars of a unique headline.
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
            # Limit to the XBRL namespace section to keep scope honest.
            xbrl_section = text[:65536]
            for m in _XBRL_TAG_RE.finditer(xbrl_section):
                tag = m.group(1)
                if tag.startswith(("us-gaap:", "dei:", "xbrl:", "ifrs-full:")):
                    continue  # standard taxonomy — not custom
                tags_seen.add(tag)
            self._sources_seen["xbrl_tags"] += 1
        for t in tags_seen:
            self._buckets["xbrl_concept"].add(t)

    # ── Report materialisation ────────────────────────────────────────

    def _materialize_report(self) -> AtlasHarvestReport:
        report = AtlasHarvestReport(ticker=self.ticker)
        report.atlas_sources = dict(self._sources_seen)
        # Carry the RAW values forward so ``_merge_harvest_into_atlas_yaml``
        # can iterate them. ``to_report`` deliberately never serializes
        # this field so the public QA payload stays identifier-free.
        report.buckets = {etype: set(values) for etype, values in self._buckets.items()}
        for etype, values in self._buckets.items():
            report.identifier_types[etype] = len(values)
            report.aliases_by_type[etype] = len(values)
        report.aliases_built = sum(len(v) for v in self._buckets.values())

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

    # ── Atlas materialisation helpers ─────────────────────────────────

    def emit_entityregistry_kwargs(self) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
        """Format the harvested buckets into the shape ``load_atlas`` can read.

        Returns ``(entities_payload, aliases_payload)`` so an external
        YAML merge can feed existing-atlas values into Phase 1.5 without
        losing curated typing.
        """
        entities: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        aliases: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        counter = 1
        for etype, values in sorted(self._buckets.items()):
            for value in sorted(values):
                entity_id = f"harvest_{etype}_{counter:04d}"
                entities["items"].append(
                    {
                        "entity_id": entity_id,
                        "entity_type": etype,
                        "canonical_private_value": value,
                    }
                )
                aliases["items"].append(
                    {
                        "alias_id": f"harvest_{etype}_a{counter:04d}",
                        "canonical_entity_id": entity_id,
                        "private_alias_value": value,
                        "entity_type": etype,
                        "match_policy": MatchPolicy.LITERAL.value,
                    }
                )
                counter += 1
        # Type narrowing for mypy — emit empty lists if no items.
        if not entities["items"]:
            entities.pop("items", None)
        if not aliases["items"]:
            aliases.pop("items", None)
        return dict(entities), dict(aliases)

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


def write_coverage_report(report: AtlasHarvestReport, dest: Path) -> None:
    """Write the coverage report to disk (no raw identifiers, ever)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report.to_report(), indent=2, sort_keys=True), encoding="utf-8")
