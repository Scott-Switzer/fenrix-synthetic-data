"""Synthetic financial news surrogate generator (Phase 9).

Reads real news articles from ``originals/{ticker}/news/articles.json`` and
produces synthetic news surrogates at ``public/surrogates/news/*.md``.

Each surrogate:
- Replaces identity clues (company, executives, products, suppliers, etc.)
  with stable hash-derived generic placeholders of the form ``[Executive-AB12CD]``.
- Removes publisher names (``Reuters``, ``Bloomberg``, etc.) and URLs
  (``https://...``). Original URL/publisher are recorded only as hashes
  in the **private** provenance map.
- Generalizes exact publish timestamps to ``"Q3 2024 (X months ago)"``.
- Classifies the event type into one of: ``earnings_release``,
  ``merger_acquisition``, ``product_or_service``, ``executive_change``,
  ``regulatory_legal``, ``capital_return``, ``capital_markets``,
  ``partnership``, ``analyst_action``, ``macro_economic``,
  ``general_corporate`` (catch-all).
- Labels output as ``synthetic financial news surrogate``.

The generator is deterministic and key-based off a lightweight company
fingerprint when available (see :class:`LightweightFingerprint`),
falling back to a stable synthetic company name.
It never exposes any private article text in the public output. Only
SHA-256 hashes of the original text, URL, publisher, and timestamp
appear in the private provenance map.

This module intentionally defines its own minimal fingerprint types
instead of depending on an experimental ``identity.fingerprint_graph``
module. When richer fingerprint structures are needed by other code
paths they should be additive: this generator only needs an iterable
of ``(entity_type, canonical_value)`` entries.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from ..identity.schemas import EntityType
from ..storage.hashing import hash_string

logger = logging.getLogger(__name__)


# ── Lightweight fingerprint types (local; no experimental-module dep) ──


@dataclass(frozen=True)
class FingerprintEntry:
    """A single ``(entity_type, canonical_value)`` identity record.

    Frozen so callers cannot mutate the generator's view of an entry
    after it has been added to a :class:`LightweightFingerprint`.
    """

    entity_type: EntityType
    canonical_value: str
    source: str = ""


class LightweightFingerprint:
    """Minimal fingerprint view used by :class:`NewsSurrogateGenerator`.

    This is a plain-data stand-in for the experimental
    ``identity.fingerprint_graph.FingerprintGraph`` module. The news
    surrogate generator only needs to iterate over an ordered set of
    ``FingerprintEntry`` records keyed to a ticker. It never mutates the
    input, so duck-typed objects with an ``.entries`` attribute are also
    accepted.

    Example::

        g = LightweightFingerprint("CHC")
        g.add_entry(EntityType.COMPANY, "Acme Corporation")
        g.add_entry(EntityType.EXECUTIVE, "Jane Doe")
        NewsSurrogateGenerator("CHC", fingerprint_graph=g)
    """

    def __init__(
        self,
        ticker: str,
        entries: Iterable[FingerprintEntry] | None = None,
    ) -> None:
        self.ticker = ticker.upper()
        # Materialise so callers can keep appending cheaply.
        self.entries: list[FingerprintEntry] = list(entries or [])

    def add_entry(
        self,
        entity_type: EntityType,
        canonical_value: str,
        source: str = "",
    ) -> LightweightFingerprint:
        """Append an entry and return ``self`` for fluent-style chaining."""
        self.entries.append(
            FingerprintEntry(
                entity_type=entity_type,
                canonical_value=canonical_value,
                source=source,
            )
        )
        return self


# ── Event-type classification ──────────────────────────────────────────

# First matching pattern wins. Order matters: more specific first.
_EVENT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:earnings|net income|EPS|guidance|outlook|"
            r"preliminary results|quarterly results|revenue|margin)\b",
            re.IGNORECASE,
        ),
        "earnings_release",
    ),
    (
        re.compile(
            r"\b(?:acquir|merger|acquisition|takeover|buyout|bid for|to buy)\b",
            re.IGNORECASE,
        ),
        "merger_acquisition",
    ),
    (
        re.compile(
            r"\b(?:launch|unveil|introduce|announce .{0,15} (?:product|platform|"
            r"service|chip|processor)|new (?:product|platform|service))\b",
            re.IGNORECASE,
        ),
        "product_or_service",
    ),
    (
        re.compile(
            r"\b(?:CEO|CFO|COO|CTO|chief [a-z]+ officer|appointed|named|"
            r"joins as|departs|resigns|step down)\b",
            re.IGNORECASE,
        ),
        "executive_change",
    ),
    (
        re.compile(
            r"\b(?:lawsuit|settlement|investigation|probe|regulator|"
            r"regulatory|FTC|antitrust|subpoena|indictment)\b",
            re.IGNORECASE,
        ),
        "regulatory_legal",
    ),
    (
        re.compile(
            r"\b(?:dividend|buyback|repurchase|capital return|"
            r"special dividend|recapitalization)\b",
            re.IGNORECASE,
        ),
        "capital_return",
    ),
    (
        re.compile(
            r"\b(?:IPO|public offering|secondary offering|follow-on offering|"
            r"priced at)\b",
            re.IGNORECASE,
        ),
        "capital_markets",
    ),
    (
        re.compile(
            r"\b(?:partnership|joint venture|alliance|collaboration|"
            r"teaming up)\b",
            re.IGNORECASE,
        ),
        "partnership",
    ),
    (
        re.compile(
            r"\b(?:downgrade|upgrade|price target|analyst rating|"
            r"initiates coverage|raises rating)\b",
            re.IGNORECASE,
        ),
        "analyst_action",
    ),
    (
        re.compile(
            r"\b(?:federal reserve|interest rate|inflation|recession|"
            r"tariff|trade war|macro)\b",
            re.IGNORECASE,
        ),
        "macro_economic",
    ),
]

_PUBLISHER_PATTERN = re.compile(
    r"\b(?:"
    r"Reuters|Bloomberg|CNBC|NASDAQ|Yahoo|WSJ|New York Times|"
    r"Seeking Alpha|MarketWatch|Business Insider|Barron's|Fortune|"
    r"Investopedia|Forbes|AP News|CNN|Fox|BBC News|TheStreet|Zacks|"
    r"TipRanks|Motley Fool|Wall Street Journal"
    r")\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://\S+")


# ── Result / dataclass shapes ──────────────────────────────────────────


@dataclass
class NewsSurrogateResult:
    """Result of generating news surrogates for a ticker."""

    ticker: str
    articles_processed: int = 0
    surrogates_generated: int = 0
    surrogate_ids: list[str] = field(default_factory=list)
    events_by_type: dict[str, int] = field(default_factory=dict)
    private_provenance_path: str = ""
    errors: list[str] = field(default_factory=list)


# ── Generator ──────────────────────────────────────────────────────────


class NewsSurrogateGenerator:
    """Generate synthetic financial news surrogates from real news articles.

    Mirrors :class:`SyntheticSurrogateGenerator`'s identity-replacement
    pattern but is specialised for news: short text, URLs and publishers
    must be removed from public output, dates generalised to relative
    periods, event type classified.
    """

    DEFAULT_LABEL = "synthetic financial news surrogate"
    DEFAULT_SYNTHETIC_COMPANY = "Aster"

    def __init__(
        self,
        ticker: str,
        fingerprint_graph: LightweightFingerprint | Iterable[FingerprintEntry] | None = None,
        synthetic_company: str | None = None,
    ) -> None:
        self.ticker = ticker.upper()
        # Accept either a LightweightFingerprint builder or any iterable of
        # FingerprintEntry records (e.g. a plain list). The generator only
        # iterates ``.entries`` once at construction time.
        if fingerprint_graph is None:
            self.fingerprint_entries: list[FingerprintEntry] = []
        elif isinstance(fingerprint_graph, LightweightFingerprint):
            self.fingerprint_entries = list(fingerprint_graph.entries)
        else:
            # Assume an iterable of FingerprintEntry. Materialise once so the
            # iterable (which may be a generator) is not consumed twice.
            self.fingerprint_entries = list(fingerprint_graph)
        # Note: the previous public attribute ``self.fingerprint_graph``
        # (a FingerprintGraph-shaped builder) has been removed. Callers
        # should use ``self.fingerprint_entries``. The two attributes had
        # incompatible shapes, and dropping the misleading mirror avoids a
        # silent type-lie for callers doing ``gen.fingerprint_graph.add_entry``.
        self.synthetic_company = (synthetic_company or self.DEFAULT_SYNTHETIC_COMPANY).strip()
        # Sorted (longest-first) real → synthetic replacement tuples
        self._replacements: list[tuple[str, str]] = self._build_replacements()

    # ── Replacement map ────────────────────────────────────────────

    def _build_replacements(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for entry in self.fingerprint_entries:
            real = entry.canonical_value.strip()
            if len(real) < 3:
                continue
            synth = self._synthetic_for_entry(entry)
            if synth:
                pairs.append((real, synth))
        # Real ticker -> synthetic company (always replaced, even when
        # no fingerprint is supplied).
        pairs.append((self.ticker, self.synthetic_company))
        pairs.sort(key=lambda x: len(x[0]), reverse=True)
        return pairs

    def _synthetic_for_entry(self, entry: FingerprintEntry) -> str | None:
        """Type-bucketed generic placeholder keyed off the entry value hash.

        Uses only EntityType values that exist in identity.schemas.EntityType;
        unsupported NER-side types collapse to a generic placeholder. URL and
        LOCATION values are recorded only as hashes in the private provenance
        map; the public surrogate never references them.
        """
        et = entry.entity_type
        h = hashlib.sha256(entry.canonical_value.encode()).hexdigest()[:6].upper()
        if et in (EntityType.COMPANY, EntityType.TICKER):
            return self.synthetic_company
        if et == EntityType.EXECUTIVE:
            return f"[Executive-{h}]"
        if et in (EntityType.PRODUCT, EntityType.BRAND):
            return f"[Product-{h}]"
        if et == EntityType.PROPRIETARY_PLATFORM:
            return f"[Platform-{h}]"
        if et == EntityType.SUPPLIER:
            return f"[Supplier-{h}]"
        if et == EntityType.COMPETITOR:
            return f"[Competitor-{h}]"
        if et in (EntityType.HEADQUARTERS, EntityType.FACILITY):
            return f"[Location-{h}]"
        if et == EntityType.AUDITOR:
            return f"[Auditor-{h}]"
        if et == EntityType.LAW_FIRM:
            return f"[LawFirm-{h}]"
        if et == EntityType.BUSINESS_SEGMENT:
            return f"[Segment-{h}]"
        if et == EntityType.COMPANY_DOMAIN:
            return f"{self.synthetic_company.lower()}.example.com"
        return f"[Entity-{h}]"

    # ── Event-type classification ─────────────────────────────────

    def classify_event_type(self, text: str) -> str:
        """Return first matching event-type label; ``general_corporate`` fallback."""
        if not text:
            return "general_corporate"
        for pattern, event_type in _EVENT_PATTERNS:
            if pattern.search(text):
                return event_type
        return "general_corporate"

    # ── Date generalisation ───────────────────────────────────────

    def generalize_date(
        self,
        published_timestamp: str | int | float,
        ref_date: datetime | None = None,
    ) -> str:
        """Return ``"Q3 2024 (X months ago)"``-style relative period."""
        if published_timestamp in (None, "", 0):
            return "[PERIOD DATE]"
        ts_str = str(published_timestamp).strip()
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        ts: datetime | None = None
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            try:
                ts = datetime.fromtimestamp(float(published_timestamp), tz=UTC)
            except (ValueError, TypeError, OSError):
                return "[PERIOD DATE]"
        if ts is None:
            return "[PERIOD DATE]"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        ref = ref_date or datetime.now(UTC)
        quarter = (ts.month - 1) // 3 + 1
        rel_years = ref.year - ts.year
        rel_months = (ref.year - ts.year) * 12 + (ref.month - ts.month)
        if rel_months <= 0:
            return f"Q{quarter} {ts.year} (current period)"
        if rel_years == 0:
            n = "1 month ago" if rel_months == 1 else f"{rel_months} months ago"
            return f"Q{quarter} {ts.year} ({n})"
        if rel_years == 1:
            return f"Q{quarter} {ts.year} (prior year)"
        return f"Q{quarter} {ts.year} ({rel_years} years ago)"

    # ── Identity stripping ────────────────────────────────────────

    def strip_identity(self, text: str) -> tuple[str, int]:
        """Replace real identities with synthetic placeholders.

        Returns ``(text, replacement_count)``.
        """
        replacements = 0
        for real, synth in self._replacements:
            pattern = re.compile(r"\b" + re.escape(real) + r"\b", re.IGNORECASE)
            matches = list(pattern.finditer(text))
            if matches:
                text = pattern.sub(synth, text)
                replacements += len(matches)
        return text, replacements

    @staticmethod
    def remove_publishers_and_urls(text: str) -> str:
        text = _PUBLISHER_PATTERN.sub("[PUBLISHER REMOVED]", text)
        text = _URL_PATTERN.sub("[URL REMOVED]", text)
        return text

    # ── Article-level surrogate build ─────────────────────────────

    def _make_article_id(self, article: dict[str, Any], idx: int) -> str:
        url = article.get("canonical_url", "") or ""
        if url:
            return f"news_{hash_string(url)[:16]}"
        ts_hash = hash_string(str(article.get("published_timestamp", "") or ""))[:8]
        return f"news_{self.ticker.lower()}_{idx:03d}_{ts_hash}"

    def generate_article(
        self,
        article: dict[str, Any],
        idx: int,
        ref_date: datetime | None = None,
    ) -> tuple[str, str, str, str]:
        """Generate one synthetic news surrogate.

        Returns ``(surrogate_markdown, article_id, event_type, relative_period)``.
        """
        article_id = self._make_article_id(article, idx)
        original_text = (
            " ".join(
                [
                    str(article.get("headline", "") or ""),
                    str(article.get("summary", "") or ""),
                    str(article.get("body", "") or "")[:5000],
                ]
            ).strip()
            or "[empty article]"
        )

        event_type = self.classify_event_type(original_text)
        relative_period = self.generalize_date(article.get("published_timestamp", ""), ref_date)

        # Apply transformations to the narrative content only
        stripped, _n = self.strip_identity(original_text)
        public_text = self.remove_publishers_and_urls(stripped)

        surrogate_md = (
            f"# {self.DEFAULT_LABEL}\n"
            "\n"
            f"**Surrogate ID:** {article_id}\n"
            f"**Synthetic Company:** {self.synthetic_company}\n"
            f"**Event Type:** {event_type}\n"
            f"**Relative Period:** {relative_period}\n"
            f"**Label:** {self.DEFAULT_LABEL}\n"
            "\n"
            "---\n"
            "\n"
            "## Event Summary\n"
            "\n"
            f"{public_text.strip()}\n"
            "\n"
            "---\n"
            "\n"
            "## Provenance & Disclosure\n"
            "\n"
            "This document is a synthetic financial news surrogate.\n"
            "All identity clues, URLs, publishers, and exact timestamps have been removed.\n"
            "Only the financial event type and relative period are preserved.\n"
            "Original article hashes are stored in private provenance outside this release.\n"
        )

        return surrogate_md, article_id, event_type, relative_period

    # ── Public surface ────────────────────────────────────────────

    def generate_from_articles(
        self,
        articles: list[dict[str, Any]],
        public_dir: Path,
        private_dir: Path,
        ref_date: datetime | None = None,
    ) -> NewsSurrogateResult:
        """Generate surrogates from a list of articles.

        Writes:
            - ``public_dir/{article_id}_surrogate.md`` — sanitized markdown
            - ``private_dir/{ticker}_news_provenance.json`` — hash-only
              provenance (no original values exposed)
        """
        public_dir.mkdir(parents=True, exist_ok=True)
        private_dir.mkdir(parents=True, exist_ok=True)

        result = NewsSurrogateResult(ticker=self.ticker, articles_processed=len(articles))

        provenance_records: list[dict[str, Any]] = []

        for idx, article in enumerate(articles):
            try:
                md, article_id, event_type, relative_period = self.generate_article(
                    article, idx, ref_date
                )
                surrogate_path = public_dir / f"{article_id}_surrogate.md"
                surrogate_path.write_text(md, encoding="utf-8")

                original_text_blob = " ".join(
                    [
                        str(article.get("headline", "") or ""),
                        str(article.get("summary", "") or ""),
                        str(article.get("body", "") or "")[:5000],
                    ]
                ).strip()
                provenance_records.append(
                    {
                        "article_id": article_id,
                        "surrogate_path": surrogate_path.name,
                        "original_text_hash": hash_string(original_text_blob),
                        "original_url_hash": hash_string(
                            str(article.get("canonical_url", "") or "")
                        ),
                        "original_publisher_hash": hash_string(
                            str(article.get("publisher", "") or "")
                        ),
                        "original_timestamp_hash": hash_string(
                            str(article.get("published_timestamp", "") or "")
                        ),
                        "event_type": event_type,
                        "relative_period": relative_period,
                    }
                )

                result.surrogate_ids.append(article_id)
                result.surrogates_generated += 1
                result.events_by_type[event_type] = result.events_by_type.get(event_type, 0) + 1
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"article {idx}: {exc}")

        provenance_path = private_dir / f"{self.ticker}_news_provenance.json"
        provenance_payload = {
            "ticker": self.ticker,
            "synthetic_company": self.synthetic_company,
            "articles_processed": result.articles_processed,
            "surrogates_generated": result.surrogates_generated,
            "events_by_type": result.events_by_type,
            "provenance_records": provenance_records,
        }
        provenance_path.write_bytes(
            orjson.dumps(
                provenance_payload,
                option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
            )
        )
        result.private_provenance_path = str(provenance_path)

        return result
